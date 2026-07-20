"""Domain API routes."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
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

try:
    from pipeline.core.config import load_app_config, save_app_config
except Exception:  # pragma: no cover
    load_app_config = save_app_config = None  # type: ignore
try:
    from pipeline.core import system_check
except Exception:  # pragma: no cover
    system_check = None  # type: ignore


@router.get("/api/hardware")
def api_hardware():
    return hardware()


@router.get("/api/config")
def api_get_config():
    from pipeline.core.app_config import public_app_config

    return public_app_config()


@router.post("/api/config")
def api_save_config(body: AppConfigIn):
    from pipeline.core.app_config import public_app_config, save_app_config

    patch: dict = {"cloud": {}}
    if body.cloud:
        for k, v in body.cloud.items():
            patch["cloud"][k] = {
                "apiKey": v.apiKey if v.apiKey is not None else "",
                "baseUrl": v.baseUrl or "",
                "model": v.model or "",
            }
    if body.tts and body.tts.elevenlabs is not None:
        patch["tts"] = {
            "elevenlabs": {
                "apiKeys": body.tts.elevenlabs.apiKeys
                if body.tts.elevenlabs.apiKeys is not None
                else "",
            }
        }
    save_app_config(patch)
    # key ElevenLabs đổi → xóa cache list giọng (tránh kẹt [] từ lần trước chưa có key)
    try:
        from pipeline.tts.eleven import clear_el_voices_cache

        clear_el_voices_cache()
    except Exception:
        pass
    return public_app_config()


@router.get("/api/health")
def health():
    return {"ok": True, "data": str(DATA)}


@router.get("/api/system/checks")
def api_system_checks(refresh: bool = False):
    """Dependency checklist cho tab Thiết lập / first-run."""
    from pipeline.core.system_check import system_checks

    try:
        return system_checks(refresh=refresh)
    except Exception as e:
        # Không để exception Python kéo sập UI; native crash vẫn chỉ tránh bằng check nhẹ.
        raise HTTPException(500, f"system checks failed: {e}") from e


@router.post("/api/system/install/ai_runtime")
def api_install_ai_runtime():
    from pipeline.core.system_check import install_ai_runtime

    try:
        result = install_ai_runtime()
        if os.environ.get("VIDEO_CLONE_DESKTOP") == "1":
            subprocess.Popen([sys.executable, "--restart-after", str(os.getpid())])
            threading.Timer(1.0, lambda: os._exit(0)).start()
        return result
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.post("/api/system/install/ocr_cuda")
def api_install_ocr_cuda():
    """Install ONNX Runtime GPU into the backend's Python environment."""
    from pipeline.core.system_check import install_ocr_cuda

    try:
        result = install_ocr_cuda()
        desktop = os.environ.get("VIDEO_CLONE_DESKTOP") == "1"
        if desktop or os.environ.get("VIDEO_CLONE_SUPERVISED") == "1":
            if desktop:
                subprocess.Popen([sys.executable, "--restart-after", str(os.getpid())])
            threading.Timer(1.0, lambda: os._exit(0)).start()
            result["message"] = "Đã cài GPU tăng tốc — đang tự khởi động lại"
        return result
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.post("/api/system/install/demucs_cuda")
def api_install_demucs_cuda():
    """Cài Demucs + PyTorch CUDA (backend/.venv-demucs) — tách lời nhanh."""
    from pipeline.core.system_check import install_demucs_cuda

    try:
        return install_demucs_cuda()
    except Exception as e:
        raise HTTPException(500, str(e)) from e

