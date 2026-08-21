"""ffprobe media inspection — no full decode."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from pipeline.core.media import _ff_bin


def inspect_media(path: Path) -> dict[str, Any]:
    cmd = [
        _ff_bin("ffprobe"),
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-of", "json",
        str(path),
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=60)
        data = json.loads(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        data = {}
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {}) or {}
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _fps(video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/1")
    return {
        "path": str(path),
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or path.stat().st_size),
        "format": fmt.get("format_name") or "",
        "videoCodec": video.get("codec_name") or "",
        "width": width,
        "height": height,
        "fps": fps,
        "audioTracks": len(audio),
        "subtitleTracks": [
            {"index": s.get("index"), "codec": s.get("codec_name"), "lang": (s.get("tags") or {}).get("language")}
            for s in subs
        ],
        "chapters": [
            {
                "id": c.get("id"),
                "start": float(c.get("start_time") or 0),
                "end": float(c.get("end_time") or 0),
                "title": ((c.get("tags") or {}).get("title") or ""),
            }
            for c in (data.get("chapters") or [])
        ],
    }


def _fps(rate: str) -> float:
    try:
        if "/" in str(rate):
            a, b = str(rate).split("/", 1)
            den = float(b)
            return float(a) / den if den else 0.0
        return float(rate)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
