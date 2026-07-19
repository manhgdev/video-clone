"""Export audio mux + stem separation (facade).

Implementation split:
  - stem.py — demucs / no-vocals / extract original
  - mux_audio.py — mux_dub / mux_original_audio / TTS mix plan
"""
from __future__ import annotations

from .mux_audio import (  # noqa: F401
    mux_dub,
    mux_original_audio,
    plan_video_slowdown_factor,
)
from .stem import (  # noqa: F401
    _demucs_py_in,
    _demucs_root_candidates,
    export_project_audio,
    extract_original_audio,
    find_cached_no_vocals,
    read_stem_progress,
    resolve_stem_source_video,
    separate_no_vocals,
    separate_vocals,
    set_stem_progress,
)

__all__ = [
    "export_project_audio",
    "extract_original_audio",
    "find_cached_no_vocals",
    "mux_dub",
    "mux_original_audio",
    "plan_video_slowdown_factor",
    "read_stem_progress",
    "resolve_stem_source_video",
    "separate_no_vocals",
    "separate_vocals",
    "set_stem_progress",
]
