"""Resolve export/preview source video path from project meta."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.core.media import ensure_preview_clip, meta_baked_speed, meta_has_user_bake, preview_clip_matches
from pipeline.core.project import ensure_layout


def export_source_video(project_id: str, meta: dict[str, Any]) -> tuple[Path, int]:
    """Clip xuất = work bake (nếu đã Áp dụng) hoặc preview 1× / source.

    Sau Áp dụng tốc độ: workVideo là đồng hồ display — FE timeline khớp file này.
    """
    source = Path(meta["videoPath"]).resolve()
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    work = Path(str(meta.get("workVideo") or ""))
    work_ok = work.is_file()
    bake = meta_baked_speed(meta)
    user_bake = meta_has_user_bake(meta)
    speed_off = abs(float(bake) - 1.0) > 0.02
    use_work = work_ok and (user_bake or speed_off or bool(meta.get("bakedPreferVideo")))

    # Full source window
    if preview_sec <= 0:
        if use_work and "preview_" not in work.name.lower():
            return work, 0
        if speed_off:
            tag = f"s{int(round(bake * 100)):03d}"
            slow_full = ensure_layout(project_id) / "cache" / f"source_{tag}.mp4"
            if slow_full.is_file():
                return slow_full, 0
            slow080 = ensure_layout(project_id) / "cache" / "source_s080.mp4"
            if meta.get("bakedPreferVideo") and slow080.is_file():
                return slow080, 0
        return source, 0

    # Preview N s
    if use_work and preview_clip_matches(work.name, preview_sec):
        return work, preview_sec
    cache = ensure_layout(project_id) / "cache"
    if use_work or speed_off:
        tag = f"s{int(round(bake * 100)):03d}"
        for name in (f"preview_{preview_sec}_{tag}.mp4", f"preview_{preview_sec}_s080.mp4"):
            slow = cache / name
            if slow.is_file():
                return slow, preview_sec
    clip = ensure_preview_clip(
        source,
        cache / f"preview_{preview_sec}.mp4",
        preview_sec,
        project_id,
    )
    return clip, preview_sec
