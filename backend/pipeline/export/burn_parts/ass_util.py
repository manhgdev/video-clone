"""Hardsub cover + caption burn — ass_util."""
from __future__ import annotations

"""Cover hardsubs + burn translated captions."""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pipeline.ocr.extract import _rapidocr_gpu_kwargs
from pipeline.core.jobs import _job_procs, check_cancel
from pipeline.core.media import h264_encoder_args, video_size
from pipeline.core.project import ensure_layout, set_status
from pipeline.core.resources import adaptive_workers
from pipeline.ocr.extract import _ocr_join_lines, _rapidocr_labels
from pipeline.ocr.cover_timing import resolve_cover_window
from pipeline.ocr.labels import (
    clamp_label_box,
    cover_fit_label,
    is_tall_label,
    is_vertical_cjk_source,
    layout_label_caption,
    pick_label_box,
)
from pipeline.ocr.locate import (
    ocr_mid_hardsub_boxes,
    ocr_mid_labels,
    ocr_mid_vertical,
)
from pipeline.translate import _clean_burn_text

# aliases — giữ tên cũ cho call sites / tests
_clamp_label_box = clamp_label_box
_pick_label_box = pick_label_box
_ocr_mid_labels = ocr_mid_labels
_ocr_mid_vertical = ocr_mid_vertical
_ocr_mid_hardsub_boxes = ocr_mid_hardsub_boxes


def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_text(s: str) -> str:
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def write_ass(path: Path, segments: list[dict[str, Any]], width: int, height: int) -> Path:
    portrait = height > width
    band = 0.22 if portrait else 0.28
    fontsize = max(28, int(height * (0.032 if portrait else 0.038)))
    margin_v = int(height * band * 0.42)
    font_file = _subtitle_font()
    font_name = "Arial Unicode MS" if "Unicode" in font_file else Path(font_file).stem
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for seg in segments:
        if seg.get("maskOnly"):
            continue
        text = (seg.get("translation") or seg.get("source") or "").strip()
        if not text:
            continue
        start = float(seg["start"])
        end = max(float(seg["end"]), start + 0.4)
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{_ass_text(text)}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")
    return path


__all__ = [
    '_clamp_label_box',
    '_pick_label_box',
    '_ocr_mid_labels',
    '_ocr_mid_vertical',
    '_ocr_mid_hardsub_boxes',
    '_ass_time',
    '_ass_text',
    'write_ass',
]
