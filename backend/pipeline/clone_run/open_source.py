"""Open a local media file as a Clone/Review project without copying tens of GB."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pipeline.core.media import ffprobe_duration
from pipeline.core.project import ensure_layout, find_project_by_fp, save_meta, set_status


def open_local_video(
    path: str,
    *,
    kind: str = "clone",
    reuse_existing: bool = True,
) -> str:
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"SOURCE_ERROR: không thấy file {src}")
    try:
        fp = f"{src.stat().st_size:x}-{int(src.stat().st_mtime)}-{src.name}"
        if reuse_existing:
            existing = find_project_by_fp(fp)
            if existing:
                return existing
    except OSError:
        fp = uuid.uuid4().hex
    # ponytail: 10h+ files must not be SHA-scanned or copied; size+mtime token is the cache key.
    project_id = uuid.uuid4().hex[:12]
    ensure_layout(project_id)
    duration = ffprobe_duration(src)
    settings = {"engine": "whisper", "targetLang": "vi", "previewSec": 0, "burnSubs": True}
    meta: dict[str, Any] = {
        "videoPath": str(src),
        "duration": duration,
        "sourceFp": fp,
        "kind": kind,
        "segments": [],
        "cache": {},
        "settings": settings,
        "status": {
            "step": "video",
            "progress": 100,
            "message": "Video sẵn sàng",
            "running": False,
        },
    }
    save_meta(project_id, meta)
    set_status(project_id, step="video", progress=100, message="Video sẵn sàng", running=False)
    return project_id


def light_fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}-{int(st.st_mtime_ns)}-{path.name}"
