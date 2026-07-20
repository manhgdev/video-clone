"""Domain API routes."""
from __future__ import annotations

import json
import math
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.deps import (
    AppConfigIn,
    CloneRenameIn,
    CompoundClipIn,
    ExportPayload,
    PreviewTtsIn,
    RebakeSpeedIn,
    RetranslateIn,
    SEG_PRESERVE,
    SegmentIn,
    Settings,
    StudioSynthIn,
    TextOverlayIn,
    VoiceBulkMoveIn,
    VoicePatchIn,
    require_meta,
    validate_overlay,
    validate_segment_editor_fields,
)
from api.job_spawn import spawn
from api.video_serve import serve_video_file
from pipeline import (
    DATA,
    PUBLIC_DATA,
    ensure_layout,
    ffprobe_duration,
    find_project_by_fp,
    hardware,
    list_voices,
    load_meta,
    mutate_meta,
    out_final,
    project_dir,
    request_cancel,
    run_dub,
    run_export,
    run_pipeline,
    save_meta,
    set_status,
    tts_cache_key,
    tts_segment,
    video_fingerprint,
)
from pipeline.core.jobs import Cancelled, arm_job, clear_job
from pipeline.core.media import meta_baked_speed, meta_has_user_bake, video_size
from pipeline.export.mux import (
    export_project_audio,
    find_cached_no_vocals,
    read_stem_progress,
    separate_no_vocals,
)
from pipeline.tts import engines_status

router = APIRouter()

# Aliases matching original routes_all names
_spawn = spawn
_serve_video_file = serve_video_file
_validate_overlay = validate_overlay
_validate_segment_editor_fields = validate_segment_editor_fields
_SEG_PRESERVE = SEG_PRESERVE


@router.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "Thiếu file")
    ext = Path(file.filename).suffix or ".mp4"
    tmp = DATA / f"_upload_{uuid.uuid4().hex}{ext}"
    DATA.mkdir(exist_ok=True)
    with tmp.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    fp = video_fingerprint(tmp)
    existing = find_project_by_fp(fp)
    if existing:
        tmp.unlink(missing_ok=True)
        meta = load_meta(existing)
        ensure_layout(existing)
        set_status(
            existing,
            step=meta.get("status", {}).get("step") or "video",
            progress=100,
            message="Video đã có sẵn (cache)",
            running=False,
            error=None,
        )
        return {
            "projectId": existing,
            "videoUrl": f"/api/projects/{existing}/video",
            "duration": meta.get("duration") or ffprobe_duration(Path(meta["videoPath"])),
            "cached": True,
            "segments": meta.get("segments") or [],
            "settings": meta.get("settings") or {},
        }

    project_id = uuid.uuid4().hex[:12]
    root = ensure_layout(project_id)
    dest = root / f"source{ext}"
    tmp.replace(dest)
    duration = ffprobe_duration(dest)
    save_meta(
        project_id,
        {
            "videoPath": str(dest),
            "duration": duration,
            "sourceFp": fp,
            "segments": [],
            "cache": {},
            "settings": Settings().model_dump(),
            "status": {
                "step": "video",
                "progress": 100,
                "message": "Video sẵn sàng",
                "running": False,
            },
        },
    )
    return {
        "projectId": project_id,
        "videoUrl": f"/api/projects/{project_id}/video",
        "duration": duration,
        "cached": False,
        "segments": [],
    }


@router.get("/api/projects/{project_id}/video")
@router.get("/api/projects/{project_id}/video/{_rev}")
def api_video(project_id: str, request: Request, _rev: str | None = None):
    # {_rev} = cache-bust từ frontend (…/video/1) — bỏ qua
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    from pipeline.core.project import resolve_project_video

    return _serve_video_file(resolve_project_video(meta, project_id), request)


@router.post("/api/projects/{project_id}/rebake-speed")
def api_rebake_speed(project_id: str, body: RebakeSpeedIn):
    """Bake tốc độ preview từ file 1× + remap timeline theo tốc độ mới."""
    from pipeline.core.media import (
        clamp_playback_speed,
        ensure_playback_speed,
        meta_baked_speed,
        preview_1x_path,
        remap_timeline_for_speed_change,
        speed_cache_tag,
    )

    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    if (meta.get("status") or {}).get("running"):
        raise HTTPException(409, "Đang có job chạy — đợi xong rồi áp dụng tốc độ")

    speed = clamp_playback_speed(body.speed)
    old = meta_baked_speed(meta)
    if not body.skipRemap:
        remap_timeline_for_speed_change(meta, old, speed)

    base = preview_1x_path(project_id, meta)
    if not base.is_file():
        raise HTTPException(404, "Chưa có preview 1× — chạy Dịch trước")

    cache = ensure_layout(project_id) / "cache"
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    arm_job(project_id)
    try:
        if abs(speed - 1.0) < 0.001:
            work = base
            meta.pop("bakedPreferVideo", None)
            # Giữ key bakedSpeed=1.0 → khóa 1× (không soft preferVideo 0.8 lại)
            meta["bakedSpeed"] = 1.0
            meta.pop("workDuration", None)
        else:
            set_status(
                project_id,
                step="video",
                progress=5,
                message=f"Bake tốc độ {speed:.2f}×…",
                running=True,
                error=None,
            )
            tag = speed_cache_tag(speed)
            dest = (
                cache / f"preview_{preview_sec}_{tag}.mp4"
                if preview_sec > 0
                else cache / f"source_{tag}.mp4"
            )
            work = ensure_playback_speed(base, dest, speed, project_id=project_id)
            meta["bakedPreferVideo"] = True
            meta["bakedSpeed"] = speed
            meta["workDuration"] = float(ffprobe_duration(work) or 0)
        meta["workVideo"] = str(work.resolve())
        save_meta(project_id, meta)
    except Cancelled as e:
        set_status(
            project_id,
            step="video",
            progress=0,
            message="Đã huỷ áp dụng tốc độ",
            running=False,
            error="cancelled",
        )
        raise HTTPException(409, "cancelled") from e
    except Exception as e:
        set_status(
            project_id,
            step="video",
            progress=0,
            message="Bake tốc độ thất bại",
            running=False,
            error=str(e)[:500],
        )
        raise HTTPException(500, f"Bake tốc độ thất bại: {e}") from e
    finally:
        clear_job(project_id)

    speed_baked = abs(speed - 1.0) > 0.001
    work_clip = float(meta["workDuration"]) if speed_baked else float(preview_sec)
    if not speed_baked and work.is_file():
        try:
            wd = float(ffprobe_duration(work) or 0)
            if wd > 0.2:
                work_clip = wd
        except Exception:
            pass
    duration = float(
        meta.get("workDuration") or ffprobe_duration(work) or meta.get("duration") or 0
    )
    if not speed_baked and work_clip > 0:
        duration = work_clip
    set_status(
        project_id,
        step="video",
        progress=100,
        message=f"Preview {speed:.2f}× sẵn sàng",
        running=False,
        error=None,
    )
    return {
        "ok": True,
        "bakedSpeed": speed,
        "bakedPreferVideo": speed_baked,
        # true cả khi 1× — user đã Áp dụng, không soft 0.8 lại
        "hasBakedSpeed": True,
        "workClipSec": work_clip,
        "duration": duration,
        "segments": meta.get("segments") or [],
        "overlays": meta.get("overlays") or [],
        "videoUrl": f"/api/projects/{project_id}/video",
        "timeScale": (old / speed) if speed > 0.2 and old > 0.2 else 1.0,
        "prevBakedSpeed": old,
    }


@router.get("/api/projects/{project_id}/status")
def api_status(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    st = dict(meta.get("status") or {"step": "video", "progress": 0, "message": "", "running": False})
    # UI restore: duration = cửa sổ làm việc (preview/bake), không phải full source
    from pipeline.core.media import meta_baked_speed, meta_has_user_bake

    src_dur = float(meta.get("duration") or 0)
    preview_1x = max(0, int(meta.get("previewSec") or 0))
    baked_speed = meta_baked_speed(meta)
    user_bake = meta_has_user_bake(meta)
    speed_baked = abs(baked_speed - 1.0) > 0.001
    work_dur = float(meta.get("workDuration") or 0)
    work_clip = 0.0
    display_dur = src_dur
    if speed_baked and work_dur > 0:
        work_clip = work_dur
        display_dur = work_dur
    elif preview_1x > 0:
        work_clip = float(preview_1x)
        if speed_baked and baked_speed > 0.2:
            work_clip = float(preview_1x) / baked_speed
        display_dur = work_clip
        work_path = Path(str(meta.get("workVideo") or ""))
        if work_path.is_file():
            try:
                wd = float(ffprobe_duration(work_path) or 0)
                if wd > 0.2:
                    work_clip = wd
                    display_dur = wd
            except Exception:
                pass
    elif work_dur > 0:
        display_dur = work_dur
    st["sourceDuration"] = src_dur
    st["workClipSec"] = work_clip
    st["duration"] = display_dur if display_dur > 0 else src_dur
    # bakedPreferVideo = file chậm ≠1; hasBakedSpeed = user đã Áp dụng (cả 1×)
    st["bakedPreferVideo"] = bool(speed_baked)
    st["bakedSpeed"] = baked_speed if user_bake else 1.0
    st["hasBakedSpeed"] = bool(user_bake)
    if meta.get("settings"):
        st["settings"] = meta["settings"]
    if meta.get("outputRel"):
        st["outputRel"] = st.get("outputRel") or meta.get("outputRel")
    return st


@router.post("/api/projects/{project_id}/status/dismiss")
def api_dismiss_status(project_id: str):
    """User đóng popup lỗi — xóa error khỏi meta để F5 không hiện lại."""
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    st = dict(meta.get("status") or {})
    if st.get("running"):
        # Job còn chạy: chỉ «Chạy nền», không xóa trạng thái
        return {"ok": True, "ignored": True}
    set_status(
        project_id,
        step=str(st.get("step") or "video"),
        progress=int(st.get("progress") or 0),
        message="",
        running=False,
        error=None,
    )
    return {"ok": True}


@router.post("/api/projects/{project_id}/settings")
def api_save_settings(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    old_default = (meta.get("settings") or {}).get("defaultVoice") or ""
    meta["settings"] = settings.model_dump()
    new_default = settings.defaultVoice or ""
    # đổi giọng mặc định → đồng bộ đoạn đang inherit (system / default cũ)
    if new_default and new_default != old_default:
        for seg in meta.get("segments") or []:
            seg["voice"] = new_default
    save_meta(project_id, meta)
    return {"ok": True, "settings": meta["settings"]}

