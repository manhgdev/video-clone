"""Scene detection via FFmpeg scene filter; uniform fallback for long files."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from pipeline.core.jobs import check_cancel
from pipeline.core.media import _ff_bin


def detect_scenes(
    path: Path,
    duration: float,
    *,
    job_id: str | None = None,
    threshold: float = 0.35,
) -> list[dict[str, Any]]:
    cuts = _ffmpeg_scenes(path, threshold=threshold, job_id=job_id)
    if len(cuts) < 2:
        step = 8.0 if duration < 3600 else 12.0
        t = 0.0
        cuts = []
        while t < duration - 0.4:
            cuts.append(t)
            t += step
    cuts = sorted({round(max(0.0, c), 3) for c in cuts if 0 <= c < duration})
    if not cuts or cuts[0] > 0.05:
        cuts = [0.0, *cuts]
    if cuts[-1] < duration - 0.05:
        cuts.append(duration)
    scenes: list[dict[str, Any]] = []
    for i, start in enumerate(cuts[:-1]):
        end = cuts[i + 1]
        if end - start < 0.4:
            continue
        # merge flicker: keep natural length; cap 90s windows for VLM batches
        if end - start > 90:
            inner = start
            while inner < end - 0.4:
                nxt = min(end, inner + 45.0)
                scenes.append(_scene(len(scenes), inner, nxt))
                inner = nxt
        else:
            scenes.append(_scene(len(scenes), start, end))
    return scenes or [_scene(0, 0.0, max(duration, 1.0))]


def _scene(idx: int, start: float, end: float) -> dict[str, Any]:
    return {
        "scene_id": idx,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
    }


def _ffmpeg_scenes(path: Path, *, threshold: float, job_id: str | None) -> list[float]:
    cmd = [
        _ff_bin("ffmpeg"),
        "-hide_banner",
        "-i", str(path),
        "-an",
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null",
        "-",
    ]
    try:
        if job_id:
            check_cancel(job_id)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    times: list[float] = []
    for match in re.finditer(r"pts_time:([0-9.]+)", proc.stderr or ""):
        times.append(float(match.group(1)))
        if len(times) > 8000:
            break
    return times
