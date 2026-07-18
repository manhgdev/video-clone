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

from pipeline.export.mux import (
    export_project_audio,
    find_cached_no_vocals,
    read_stem_progress,
    separate_no_vocals,
)


@router.get("/api/projects/{project_id}/audio/no-vocals/status")
def api_no_vocals_status(project_id: str):
    """Cache hit nhanh — UI không hiện 1% nếu đã có stem."""
    from pipeline.export.mux import find_cached_no_vocals, read_stem_progress

    if not load_meta(project_id):
        raise HTTPException(404)
    cached = find_cached_no_vocals(project_id)
    prog = read_stem_progress(project_id)
    if cached is not None:
        return {
            "ready": True,
            "cached": True,
            "running": False,
            "progress": 100,
            "message": "Đã có stem xóa lời (cache)",
            "audioUrl": f"/api/projects/{project_id}/cache/{cached.name}",
            "file": cached.name,
        }
    return {
        "ready": False,
        "cached": False,
        "running": bool(prog.get("running")),
        "progress": int(prog.get("progress") or 0),
        "message": str(prog.get("message") or ""),
        "audioUrl": None,
        "file": None,
    }


@router.post("/api/projects/{project_id}/audio/no-vocals")
def api_prepare_no_vocals(project_id: str):
    """Tách stem xóa lời (Demucs) — cache dùng chung preview + export."""
    from pipeline.export.mux import find_cached_no_vocals

    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    video = Path(meta["videoPath"])
    if not video.is_file():
        raise HTTPException(404, "Thiếu video nguồn")
    had_cache = find_cached_no_vocals(project_id) is not None
    try:
        # Luôn theo videoPath gốc (ignore work bake) — export tái dùng cùng file
        path = separate_no_vocals(project_id, video, report=False)
    except Exception as e:
        from pipeline.core.jobs import short_cmd_error

        raise HTTPException(500, short_cmd_error(e)) from e
    name = path.name
    return {
        "audioUrl": f"/api/projects/{project_id}/cache/{name}",
        "file": name,
        "cached": had_cache or True,
    }


@router.get("/api/projects/{project_id}/audio/no-vocals/progress")
def api_no_vocals_progress(project_id: str):
    """Tiến độ tách stem — poll song song lúc POST /audio/no-vocals đang chạy."""
    from pipeline.export.mux import find_cached_no_vocals, read_stem_progress

    if not load_meta(project_id):
        raise HTTPException(404)
    cached = find_cached_no_vocals(project_id)
    if cached is not None:
        return {
            "progress": 100,
            "message": "Đã có stem xóa lời (cache)",
            "running": False,
            "ready": True,
            "file": cached.name,
            "audioUrl": f"/api/projects/{project_id}/cache/{cached.name}",
        }
    prog = read_stem_progress(project_id)
    return {**prog, "ready": False, "file": None, "audioUrl": None}


@router.get("/api/projects/{project_id}/audio/download")
def api_project_audio_download(project_id: str, kind: str = "original"):
    """Tải WAV theo chế độ: original | no_vocals | vocals."""
    from pipeline.export.mux import export_project_audio

    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    video = Path(meta["videoPath"])
    if not video.is_file():
        raise HTTPException(404, "Thiếu video nguồn")
    k = (kind or "original").strip().lower()
    try:
        path = export_project_audio(project_id, video, k, report=False)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    label = {
        "original": "original",
        "source": "original",
        "full": "original",
        "no_vocals": "no_vocals",
        "novocals": "no_vocals",
        "bg": "no_vocals",
        "instrumental": "no_vocals",
        "vocals": "vocals",
        "voice": "vocals",
        "speech": "vocals",
    }.get(k, k)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{project_id}_{label}.wav",
        content_disposition_type="attachment",
    )


@router.get("/api/projects/{project_id}/cache/{name}")
def api_cache_file(project_id: str, name: str, download: int = 0):
    if not re.fullmatch(r"(?:no_vocals|vocals|original)_[a-f0-9]+\.wav", name):
        raise HTTPException(400, "Tên file không hợp lệ")
    path = ensure_layout(project_id) / "cache" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=name if download else None,
        content_disposition_type="attachment" if download else "inline",
    )

