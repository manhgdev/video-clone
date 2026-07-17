"""Local video clone pipeline: Whisper ASR → Ollama MT → TTS → ffmpeg mux."""
from __future__ import annotations

from .core.config import DATA, PUBLIC_DATA
from .core.jobs import request_cancel
from .core.media import ffprobe_duration, hardware
from .core.project import (
    ensure_layout,
    find_project_by_fp,
    load_meta,
    mutate_meta,
    out_final,
    project_dir,
    save_meta,
    set_status,
    video_fingerprint,
)
from .run import run_dub, run_export, run_pipeline
from .tts import list_voices, tts_cache_key, tts_segment

__all__ = [
    "DATA",
    "PUBLIC_DATA",
    "ensure_layout",
    "ffprobe_duration",
    "find_project_by_fp",
    "hardware",
    "list_voices",
    "load_meta",
    "mutate_meta",
    "out_final",
    "project_dir",
    "request_cancel",
    "run_dub",
    "run_export",
    "run_pipeline",
    "save_meta",
    "set_status",
    "tts_cache_key",
    "tts_segment",
    "video_fingerprint",
]
