"""Resolve export/preview source video path from project meta."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.core.media import ensure_preview_clip
from pipeline.core.project import ensure_layout

def export_source_video(project_id: str, meta: dict[str, Any]) -> tuple[Path, int]:
    """Clip xuất = đúng độ dài lần dịch (meta.previewSec), không lấy nhầm source full."""
    source = Path(meta["videoPath"]).resolve()
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    work = Path(str(meta.get("workVideo") or ""))
    work_ok = work.is_file()
    work_is_preview = work_ok and "preview_" in work.name.lower()

    # Full: không xuất file preview_Ns ngắn
    if preview_sec <= 0:
        if meta.get("bakedPreferVideo") and work_ok and not work_is_preview:
            return work, 0
        slow_full = ensure_layout(project_id) / "cache" / "source_s080.mp4"
        if meta.get("bakedPreferVideo") and slow_full.is_file():
            return slow_full, 0
        return source, 0

    # Preview Ns
    if meta.get("bakedPreferVideo") and work_ok and f"preview_{preview_sec}" in work.name:
        return work, preview_sec
    cache = ensure_layout(project_id) / "cache"
    if meta.get("bakedPreferVideo"):
        slow = cache / f"preview_{preview_sec}_s080.mp4"
        if slow.is_file():
            return slow, preview_sec
    clip = ensure_preview_clip(
        source,
        cache / f"preview_{preview_sec}.mp4",
        preview_sec,
        project_id,
    )
    return clip, preview_sec



from pipeline.export.compound import expand_compound_segments

