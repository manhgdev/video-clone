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
from pipeline.core.project import apply_meta_patch
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
from pipeline.subtitles import subtitle_segments

router = APIRouter()


def _subtitle_file(project_id: str, name: str) -> Path:
    safe = Path(name or "").name
    if not safe or Path(safe).suffix.lower() != ".srt":
        raise HTTPException(422, "Chỉ hỗ trợ file phụ đề .srt")
    return ensure_layout(project_id) / "subtitles" / safe


@router.get("/api/projects/{project_id}/subtitles")
def api_subtitles(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    folder = ensure_layout(project_id) / "subtitles"
    saved = {str(x.get("name")): x for x in meta.get("subtitleSources") or [] if isinstance(x, dict)}
    items = [
        {
            "name": p.name,
            "label": str(saved.get(p.name, {}).get("label") or f"File đã nhập · {p.name}"),
        }
        for p in sorted(folder.glob("*.srt"))
    ]
    return {"items": items, "active": str((meta.get("settings") or {}).get("subtitleSource") or "")}


@router.post("/api/projects/{project_id}/subtitles")
async def api_upload_subtitle(project_id: str, file: UploadFile = File(...)):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    target = _subtitle_file(project_id, file.filename or "")
    target.parent.mkdir(exist_ok=True)
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        cues = subtitle_segments(target)
    except OSError as e:
        target.unlink(missing_ok=True)
        raise HTTPException(422, f"Không đọc được file phụ đề: {e}") from e
    if not cues:
        target.unlink(missing_ok=True)
        raise HTTPException(422, "File SRT không có cue hợp lệ")
    sources = [x for x in meta.get("subtitleSources") or [] if x.get("name") != target.name]
    sources.append({"name": target.name, "label": f"File đã nhập · {target.name}", "origin": "manual"})
    meta["subtitleSources"] = sources
    save_meta(project_id, meta)
    return {"name": target.name, "items": sources}


@router.post("/api/projects/{project_id}/subtitles/{name}/apply")
def api_apply_subtitle(project_id: str, name: str):
    """Apply a selected SRT immediately, so the editor never shows the old file's cues."""
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    source = _subtitle_file(project_id, name)
    if not source.is_file():
        raise HTTPException(404, "Không tìm thấy file phụ đề")
    segments = subtitle_segments(source)
    if not segments:
        raise HTTPException(422, "File SRT không có cue hợp lệ")
    settings = dict(meta.get("settings") or {})
    settings.update({"engine": "subtitle", "subtitleSource": source.name, "matchDuration": "none"})
    meta["segments"] = segments
    meta["settings"] = settings
    meta["cache"] = {}
    meta["translationCaches"] = {}
    set_status(project_id, step="asr", progress=100, message=f"Đã nạp {len(segments)} câu từ phụ đề SRT", running=False, error=None)
    save_meta(project_id, meta)
    return {"segments": segments, "settings": settings}

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
    init_settings = Settings().model_dump()
    from pipeline.core.media import ensure_project_initial_playback_rate

    init_meta: dict = {
        "videoPath": str(dest),
        "duration": duration,
        "sourceFp": fp,
        "segments": [],
        "cache": {},
        "settings": init_settings,
        "status": {
            "step": "video",
            "progress": 100,
            "message": "Video sẵn sàng",
            "running": False,
        },
    }
    ensure_project_initial_playback_rate(init_meta, init_settings)
    save_meta(project_id, init_meta)
    return {
        "projectId": project_id,
        "videoUrl": f"/api/projects/{project_id}/video",
        "duration": duration,
        "cached": False,
        "segments": [],
        "settings": init_settings,
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

    # Chống request cũ ghi đè: chỉ revision >= speedRevision đã lưu mới chạy
    req_rev = body.speedRevision
    if req_rev is not None:
        saved_rev = int(meta.get("speedRevision") or 0)
        if int(req_rev) < saved_rev:
            return {
                "ok": True,
                "ignored": True,
                "reason": "STALE_SPEED_REVISION",
                "speedRevision": saved_rev,
                "bakedSpeed": meta_baked_speed(meta),
                "bakedPreferVideo": bool(meta.get("bakedPreferVideo")),
                "hasBakedSpeed": bool(meta.get("bakedSpeed") is not None or meta.get("bakedPreferVideo")),
                "workClipSec": float(meta.get("workDuration") or meta.get("previewSec") or 0),
                "duration": float(meta.get("workDuration") or meta.get("duration") or 0),
                "segments": meta.get("segments") or [],
                "overlays": meta.get("overlays") or [],
                "videoUrl": f"/api/projects/{project_id}/video",
                "timeScale": 1.0,
                "prevBakedSpeed": meta_baked_speed(meta),
            }
        meta["speedRevision"] = int(req_rev)
        save_meta(project_id, meta)

    # Job khác (export/dịch) — không chặn nếu chính bake tốc độ đang chạy sẽ clear
    st_run = (meta.get("status") or {}).get("running")
    st_step = str((meta.get("status") or {}).get("step") or "")
    if st_run and st_step not in ("", "video", "idle"):
        raise HTTPException(409, "Đang có job chạy — đợi xong rồi áp dụng tốc độ")

    speed = clamp_playback_speed(body.speed)
    old = meta_baked_speed(meta)
    base = preview_1x_path(project_id, meta)
    if not base.is_file():
        raise HTTPException(404, "Chưa có preview 1× — chạy Dịch trước")

    cache = ensure_layout(project_id) / "cache"
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    arm_job(project_id)
    try:
        set_status(
            project_id,
            step="video",
            progress=8,
            message=f"Chuẩn bị tốc độ {speed:.2f}× (từ {old:.2f}×)…",
            running=True,
            error=None,
        )
        # Remap timeline TRƯỚC bake file — scale từ baseline 1×, không nhân chồng
        if not body.skipRemap:
            set_status(
                project_id,
                step="video",
                progress=18,
                message="Đang scale timeline / caption / TTS…",
                running=True,
                error=None,
            )
            remap_timeline_for_speed_change(meta, old, speed)

        if abs(speed - 1.0) < 0.001:
            set_status(
                project_id,
                step="video",
                progress=70,
                message="Khóa file 1.00×…",
                running=True,
                error=None,
            )
            work = base
            meta.pop("bakedPreferVideo", None)
            meta["bakedSpeed"] = 1.0
            meta.pop("workDuration", None)
        else:
            set_status(
                project_id,
                step="video",
                progress=35,
                message=f"Đang bake video {speed:.2f}× (ffmpeg)…",
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
            set_status(
                project_id,
                step="video",
                progress=85,
                message="Đang đo độ dài file bake…",
                running=True,
                error=None,
            )
            meta["bakedPreferVideo"] = True
            meta["bakedSpeed"] = speed
            meta["workDuration"] = float(ffprobe_duration(work) or 0)
        meta["workVideo"] = str(work.resolve())
        # FE timeline sau Áp dụng = đồng hồ display — BE export tin mốc start/end
        meta["timelineClock"] = "display"
        # Trước khi ghi: request mới hơn đã claim revision → bỏ kết quả cũ
        if req_rev is not None:
            latest = load_meta(project_id) or {}
            if int(req_rev) < int(latest.get("speedRevision") or 0):
                set_status(
                    project_id,
                    step="video",
                    progress=100,
                    message="Bỏ qua bake cũ (đã có tốc độ mới hơn)",
                    running=False,
                    error=None,
                )
                return {
                    "ok": True,
                    "ignored": True,
                    "reason": "STALE_SPEED_REVISION",
                    "speedRevision": int(latest.get("speedRevision") or 0),
                    "bakedSpeed": meta_baked_speed(latest),
                    "bakedPreferVideo": bool(latest.get("bakedPreferVideo")),
                    "hasBakedSpeed": True,
                    "workClipSec": float(latest.get("workDuration") or latest.get("duration") or 0),
                    "duration": float(latest.get("workDuration") or latest.get("duration") or 0),
                    "segments": latest.get("segments") or [],
                    "overlays": latest.get("overlays") or [],
                    "videoUrl": f"/api/projects/{project_id}/video",
                    "timeScale": 1.0,
                    "prevBakedSpeed": old,
                }
            meta["speedRevision"] = int(req_rev)
        # Patch theo key (không ghi đè cả snapshot): bake chạy vài phút, PUT
        # segments/overlays hoặc set_status trong lúc đó không được bị nuốt.
        _rebake_keys = (
            "segments", "overlays", "timelineBaseline", "bakedSpeed",
            "workVideo", "timelineClock", "speedRevision", "duration",
            "workDuration", "bakedPreferVideo",
        )
        apply_meta_patch(
            project_id,
            {k: meta[k] for k in _rebake_keys if k in meta},
            remove=tuple(k for k in ("bakedPreferVideo", "workDuration") if k not in meta),
        )
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
    # workClipSec / duration = đúng cửa sổ display (khớp thước sau bake)
    work_clip = float(meta.get("workDuration") or 0)
    if work_clip <= 0.2 and work.is_file():
        try:
            work_clip = float(ffprobe_duration(work) or 0)
        except Exception:
            work_clip = 0.0
    if work_clip <= 0.2 and preview_sec > 0:
        work_clip = float(preview_sec) / speed if speed_baked and speed > 0.2 else float(preview_sec)
    duration = work_clip if work_clip > 0.2 else float(meta.get("duration") or 0)
    set_status(
        project_id,
        step="video",
        progress=100,
        message=f"Đã áp dụng {speed:.2f}× — thước {duration:.1f}s",
        running=False,
        error=None,
    )
    return {
        "ok": True,
        "bakedSpeed": speed,
        "bakedPreferVideo": speed_baked,
        "hasBakedSpeed": True,
        "workClipSec": duration,
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
    # Tốc độ khởi tạo (preferVideo→0.8) — ghi 1 lần, không bake file
    from pipeline.core.media import ensure_project_initial_playback_rate

    had_init = meta.get("projectInitialPlaybackRate") is not None
    init_rate = ensure_project_initial_playback_rate(meta, meta.get("settings") or {})
    if not had_init:
        save_meta(project_id, meta)
    st["projectInitialPlaybackRate"] = float(init_rate)
    if meta.get("settings"):
        st["settings"] = meta["settings"]
    # Watermark discovery is separate from caption segments so the UI can show
    # what will be covered without putting handles into the dubbing script.
    if isinstance(meta.get("logoDetection"), dict):
        st["logoDetection"] = meta["logoDetection"]
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
    # đổi giọng mặc định → CHỈ đồng bộ đoạn đang kế thừa (rỗng / system /
    # default cũ). Giọng user gán riêng từng câu phải giữ nguyên — khớp
    # api_dub (jobs.py) vốn đã tôn trọng lựa chọn riêng.
    if new_default and new_default != old_default:
        for seg in meta.get("segments") or []:
            current = (seg.get("voice") or "").strip()
            if not current or current == "system" or current == old_default:
                seg["voice"] = new_default
    save_meta(project_id, meta)
    return {"ok": True, "settings": meta["settings"]}


class ClearCacheBody(BaseModel):
    """parts: danh sách mục checkbox; rỗng/null = tất cả."""

    parts: list[str] | None = None


@router.delete("/api/cache/project/{project_id}")
@router.delete("/api/projects/{project_id}/cache")
@router.post("/api/cache/project/{project_id}")
@router.post("/api/projects/{project_id}/cache/clear")
def api_clear_project_cache(project_id: str, body: ClearCacheBody | None = None):
    """Xóa cache project — chỉ khi user chủ động bấm «Xóa cache» (+ chọn mục)."""
    from pipeline.core.project import clear_project_cache

    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404, "Project không tồn tại")
    parts = body.parts if body else None
    result = clear_project_cache(project_id, parts=parts)
    return result
