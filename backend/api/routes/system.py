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

_install_state: dict[str, Any] = {
    "running": False,
    "kind": "",
    "message": "",
    "error": "",
    "needsRestart": False,
    "result": None,
}
_install_lock = threading.Lock()


def _setup_gate_path() -> Path:
    home = (os.environ.get("VIDEO_CLONE_HOME") or "").strip()
    if home:
        return Path(home) / "setup_ok"
    return Path(DATA) / "setup_ok"


def _setup_gate_passed() -> bool:
    return _setup_gate_path().is_file()


def _mark_setup_gate() -> None:
    path = _setup_gate_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1\n", encoding="utf-8")


def _start_install_job(kind: str, fn, *, needs_restart: bool = True) -> dict[str, Any]:
    with _install_lock:
        if _install_state["running"]:
            return {
                "ok": True,
                "running": True,
                "kind": _install_state["kind"],
                "message": f"Đang cài {_install_state['kind']}…",
            }
        _install_state.update(
            running=True,
            kind=kind,
            message="Đang cài…",
            error="",
            needsRestart=False,
            result=None,
        )

    def work() -> None:
        try:
            result = fn()
            changed = "Đã cài" in str(result.get("message", ""))
            if changed and needs_restart and os.environ.get("VIDEO_CLONE_DESKTOP") == "1":
                result = {**result, "needsRestart": True}
            with _install_lock:
                _install_state["result"] = result
                _install_state["message"] = str(result.get("message") or "")
                _install_state["needsRestart"] = bool(result.get("needsRestart"))
        except Exception as e:
            with _install_lock:
                _install_state["error"] = str(e)
        finally:
            with _install_lock:
                _install_state["running"] = False

    threading.Thread(target=work, name=f"install-{kind}", daemon=True).start()
    return {"ok": True, "running": True, "kind": kind, "message": f"Đang cài {kind}…"}

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


@router.get("/api/system/checks")
def api_system_checks(refresh: bool = False, deep: bool = False):
    """Dependency checklist — luôn fast. deep=1 bị bỏ qua (tránh đơ UI/API)."""
    from pipeline.core.system_check import system_checks

    try:
        # ponytail: deep probe (torch/demucs import) treo request hàng phút → đơ webview.
        return system_checks(refresh=refresh, fast=True)
    except Exception as e:
        # Không để exception Python kéo sập UI; native crash vẫn chỉ tránh bằng check nhẹ.
        raise HTTPException(500, f"system checks failed: {e}") from e


@router.get("/api/system/install/status")
def api_install_status():
    with _install_lock:
        st = dict(_install_state)
    out: dict[str, Any] = {
        "running": bool(st.get("running")),
        "kind": st.get("kind") or "",
    }
    if st.get("error"):
        out["error"] = st["error"]
        out["ok"] = False
        return out
    if st.get("result") and not st.get("running"):
        result = st["result"] if isinstance(st["result"], dict) else {}
        out.update(result)
        out["running"] = False
        if st.get("needsRestart"):
            out["needsRestart"] = True
        return out
    if st.get("message"):
        out["message"] = st["message"]
    return out


@router.post("/api/system/install/ai_runtime")
def api_install_ai_runtime():
    from pipeline.core.system_check import install_ai_runtime, _runtime_venv_fast

    if getattr(sys, "frozen", False):
        ok, detail = _runtime_venv_fast()
        if ok:
            with _install_lock:
                _install_state.update(
                    running=False,
                    kind="",
                    error="",
                    message="Gói AI đã sẵn sàng",
                    needsRestart=False,
                    result={"ok": True, "message": "Gói AI đã sẵn sàng", "detail": detail},
                )
            return {
                "ok": True,
                "running": False,
                "message": "Gói AI đã sẵn sàng",
                "detail": detail,
            }
    return _start_install_job("ai_runtime", install_ai_runtime)


@router.post("/api/system/install/ocr_cuda")
def api_install_ocr_cuda():
    from pipeline.core.system_check import install_ocr_cuda

    return _start_install_job("ocr_cuda", install_ocr_cuda)


@router.post("/api/system/install/demucs_cuda")
def api_install_demucs_cuda():
    from pipeline.core.system_check import install_demucs_cuda

    return _start_install_job("demucs_cuda", install_demucs_cuda, needs_restart=False)


@router.get("/api/system/setup-gate")
def api_get_setup_gate():
    """Cổng first-run — lưu file dưới VIDEO_CLONE_HOME (không phụ thuộc port/localStorage)."""
    return {"passed": _setup_gate_passed()}


@router.post("/api/system/setup-gate")
def api_pass_setup_gate():
    _mark_setup_gate()
    return {"passed": True}


@router.post("/api/system/restart")
def api_system_restart():
    """Khởi động lại bản desktop — gọi sau khi cài xong mọi gói cần reload."""
    if os.environ.get("VIDEO_CLONE_DESKTOP") != "1":
        raise HTTPException(400, "Chỉ bản desktop hỗ trợ khởi động lại từ app")
    subprocess.Popen([sys.executable, "--restart-after", str(os.getpid())])
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"ok": True, "message": "Đang khởi động lại…"}


@router.get("/api/system/logs")
def api_system_logs(tail: int = 800):
    """Log app (job lỗi, crash hook) — tab Cấu hình → Log."""
    from pipeline.core.app_log import read_log

    try:
        return read_log(tail=tail)
    except Exception as e:
        raise HTTPException(500, f"log read failed: {e}") from e


@router.delete("/api/system/logs")
def api_system_logs_clear():
    from pipeline.core.app_log import clear_log

    return clear_log()

