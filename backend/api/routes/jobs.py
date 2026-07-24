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
from pipeline.core.config import export_display_path
from pipeline.core.jobs import arm_job
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


@router.post("/api/projects/{project_id}/run")
def api_run(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    dumped = settings.model_dump()
    # Tách: ô Preview (UI) ≠ cửa sổ lần chạy (full / Ns)
    run_raw = dumped.pop("runPreviewSec", None)
    if run_raw is None:
        run_sec = max(0, int(dumped.get("previewSec") or 0))
    else:
        run_sec = max(0, int(run_raw))
    ui_prev = max(0, int(dumped.get("previewSec") or 0))
    if ui_prev <= 0:
        ui_prev = max(0, int((meta.get("settings") or {}).get("previewSec") or 0)) or 20
    dumped["previewSec"] = ui_prev
    meta["settings"] = dumped
    # Cửa sổ clip làm việc / cache tag — không đụng ô Preview UI
    meta["previewSec"] = run_sec
    save_meta(project_id, meta)
    arm_job(project_id)
    hint = f"Preview {run_sec}s…" if run_sec > 0 else "Dịch cả video (full)…"
    set_status(project_id, step="asr", progress=1, message=hint, running=True, error=None)
    # Pipeline nhận previewSec = cửa sổ chạy
    _spawn(run_pipeline, project_id, {**dumped, "previewSec": run_sec})
    return {"ok": True}


@router.post("/api/projects/{project_id}/dub")
def api_dub(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    old_default = (meta.get("settings") or {}).get("defaultVoice") or ""
    force_tts = bool(settings.forceTts)
    dumped = settings.model_dump()
    dumped.pop("forceTts", None)
    meta["settings"] = dumped
    default = settings.defaultVoice
    segs = meta.get("segments") or []
    uniq = {(s.get("voice") or "").strip() for s in segs}
    uniq.discard("")
    uniq.discard("system")
    # đồng bộ default: inherit cũ, hoặc cả loạt cùng 1 giọng ≠ default (vd. Adam còn sót)
    batch = len(uniq) <= 1 and (not uniq or default not in uniq)
    for seg in segs:
        v = (seg.get("voice") or "").strip()
        if batch or not v or v == "system" or v == old_default:
            seg["voice"] = default
        if force_tts:
            # Xóa trỏ cache — run_dub gen lại; file .wav xóa trong run_dub
            seg.pop("audioFile", None)
            seg.pop("audioUrl", None)
            seg.pop("audioDuration", None)
            seg.pop("videoSpeed", None)
    if force_tts:
        meta["forceTts"] = True
    save_meta(project_id, meta)
    arm_job(project_id)
    set_status(
        project_id,
        step="dub",
        progress=1,
        message="Queued… (gen lại TTS)" if force_tts else "Queued…",
        running=True,
        error=None,
    )
    _spawn(run_dub, project_id)
    return {"ok": True}


@router.post("/api/projects/{project_id}/cancel")
def api_cancel(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    st = meta.get("status") or {}
    cur_step = str(st.get("step") or "video")
    # Luôn set cancel flag (kể cả Queued trước begin_job)
    request_cancel(project_id)
    if not st.get("running"):
        # UI có thể đang optimistic running — vẫn ghi cancelled
        set_status(
            project_id,
            step=cur_step if cur_step in ("asr", "translate", "dub", "export") else "video",
            progress=0,
            message="Đã huỷ",
            running=False,
            error="cancelled",
        )
        return {"ok": True}
    msg = {
        "dub": "Đã huỷ lồng tiếng",
        "export": "Đã huỷ xuất bản",
        "asr": "Đã huỷ",
        "translate": "Đã huỷ",
    }.get(cur_step, "Đã huỷ")
    set_status(
        project_id,
        step=cur_step if cur_step in ("asr", "translate", "dub", "export") else "video",
        progress=0,
        message=msg,
        running=False,
        error="cancelled",
    )
    return {"ok": True}


@router.post("/api/projects/{project_id}/export")
def api_export(project_id: str, payload: ExportPayload):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    # UI checkbox phải thắng meta cũ; previewSec ô Preview ≠ độ dài lần dịch
    dumped = payload.model_dump(exclude={"segments", "exportEndSec", "exportStartSec", "renderName"}, exclude_none=False)
    meta["pendingRenderName"] = payload.renderName.strip()
    if payload.segments is not None:
        # List từ editor = source of truth (không merge giữ meta cũ → lệch WYSIWYG)
        ordered = sorted(payload.segments, key=lambda s: (s.start, s.end, s.id))
        out: list[dict] = []
        for i, item in enumerate(ordered):
            d = item.model_dump(exclude_none=False)
            if d.get("bbox") is None:
                d.pop("bbox", None)
            if d.get("captionLayout") is None:
                d.pop("captionLayout", None)
            d["index"] = i
            out.append(d)
        meta["segments"] = out
    if payload.exportEndSec is not None:
        if not math.isfinite(payload.exportEndSec) or payload.exportEndSec <= 0:
            raise HTTPException(422, "exportEndSec phải lớn hơn 0")
        meta["exportEndSec"] = float(payload.exportEndSec)
    else:
        meta.pop("exportEndSec", None)
    if payload.exportStartSec is not None:
        if not math.isfinite(payload.exportStartSec) or payload.exportStartSec < 0:
            raise HTTPException(422, "exportStartSec phải lớn hơn hoặc bằng 0")
        meta["exportStartSec"] = float(payload.exportStartSec)
    else:
        meta.pop("exportStartSec", None)
    run_preview = max(0, int(meta.get("previewSec") or 0))
    ui_prev = max(0, int(dumped.get("previewSec") or 0))
    if ui_prev <= 0:
        ui_prev = max(0, int((meta.get("settings") or {}).get("previewSec") or 0)) or 20
    dumped["previewSec"] = ui_prev
    # Settings editor thắng hoàn toàn (mask/font/cover/burn…)
    meta["settings"] = dumped
    # xuất theo clip lần dịch gần nhất (0 = full), không theo ô Preview
    meta["previewSec"] = run_preview
    save_meta(project_id, meta)
    hint = "full" if run_preview <= 0 else f"preview {run_preview}s"
    arm_job(project_id)
    set_status(
        project_id,
        step="export",
        progress=1,
        message=f"Đang xuất ({hint})…",
        running=True,
        error=None,
    )
    _spawn(run_export, project_id)
    return {
        "ok": True,
        "url": f"/api/projects/{project_id}/output",
        "path": export_display_path(project_dir(project_id) / "out" / "final.mp4"),
        "exports": export_display_path(PUBLIC_DATA / "exports" / f"{project_id}.mp4"),
    }


@router.get("/api/projects/{project_id}/output")
def api_output(project_id: str, download: bool = False):
    path = out_final(project_id)
    if not path.exists():
        legacy = project_dir(project_id) / "output.mp4"
        easy = PUBLIC_DATA / "exports" / f"{project_id}.mp4"
        if legacy.exists():
            path = legacy
        elif easy.exists():
            path = easy
        else:
            raise HTTPException(404)
    name = f"video-clone-{project_id}.mp4"
    # download=1 → attachment; mặc định inline để trình duyệt phát được
    if download:
        return FileResponse(path, filename=name, media_type="video/mp4")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@router.post("/api/projects/{project_id}/reveal-output")
def api_reveal_output(project_id: str):
    """Mở Finder/Explorer tại file đã xuất (local app)."""
    import platform
    import subprocess

    exports = PUBLIC_DATA / project_id / "exports"
    _meta = {}
    try:
        from pipeline.core.project import load_meta
        _meta = load_meta(project_id) or {}
        _custom = str(_meta.get("exportOutputDir") or "").strip()
        if _custom and Path(_custom).is_dir():
            exports = Path(_custom)
            try:
                from pipeline.orchestrate.export_job import _project_slug
                exports = exports / _project_slug(_meta)
            except Exception:
                pass
    except Exception:
        pass

    import re
    render_name = str(_meta.get("lastRenderName") or _meta.get("pendingRenderName") or "").strip() or f"Render {project_id}"
    safe_name = re.sub(r'[^\w\s-]', '', render_name).strip()
    safe_name = re.sub(r'[-\s]+', '-', safe_name)
    if not safe_name:
        safe_name = project_id

    candidates = [
        exports / f"{safe_name}.mp4",
        exports / f"{safe_name}.mp3",
        exports / f"{safe_name}.wav",
        exports / f"{safe_name}.aac",
        exports / f"{safe_name}.srt",
        exports / f"{safe_name}.gif",
        exports / f"{project_id}.mp4",
    ]
    path = next((p for p in candidates if p.exists()), None)

    # Fallback: mở thư mục exports
    if path is None:
        path = exports if exports.exists() else None
    if path is None:
        raise HTTPException(404, "Chưa có file xuất")

    system = platform.system()
    try:
        if system == "Darwin":
            if path.is_dir():
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["open", "-R", str(path)])
        elif system == "Windows":
            if path.is_dir():
                subprocess.Popen(["explorer", str(path)])
            else:
                # /select, và path phải là 1 arg — không có space giữa
                subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            target = path if path.is_dir() else path.parent
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as e:
        raise HTTPException(500, str(e)) from e
    return {"ok": True, "path": str(path.resolve())}
