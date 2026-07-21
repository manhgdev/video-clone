"""Hardsub cover + caption burn — layout_geo."""
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

from pipeline.export.fonts import _font_for_preset

def _cover_max_h(frame_h: int, font_size: int = 36) -> int:
    """Đủ 1–3 dòng phụ đề — theo font, không kẹp quá thấp."""
    one = int(round(font_size * 1.45 + 10))
    cap = int(round(font_size * 3.4 + 16))
    by_frame = int(round(frame_h * 0.065))
    return max(one, min(cap, by_frame))


def _preview_cover_pad(font_size: int, frame_w: int) -> tuple[int, int, int]:
    """Khớp LivePreviewEditor.coverPad — sát trên, dư đáy che stroke."""
    pad_x = max(3, int(round(frame_w * 0.003)))
    pad_top = max(2, int(round(font_size * 0.04)))
    pad_bot = max(18, int(round(font_size * 0.55)))
    return pad_x, pad_top, pad_bot


def _cover_bleed_x(content_w: int, frame_w: int = 1080) -> int:
    # Bleed vừa đủ stroke — không nới xa (khớp LivePreviewEditor)
    return max(4, int(round(content_w * 0.012)), int(round(frame_w * 0.003)))


def _cover_box_width(content_w: int, frame_w: int) -> int:
    bleed = _cover_bleed_x(content_w, frame_w)
    return min(frame_w, int(content_w + bleed * 2))


def _fit_cover_width(content_w: int, cap_w: int, frame_w: int) -> int:
    """(1) che chữ cũ  (2) fit chữ dịch → max, không phình % khung."""
    return min(frame_w, max(_cover_box_width(content_w, frame_w), cap_w))


def _fit_hardsub_box(
    seed: tuple[int, int, int, int],
    auto_w: int,
    font_size: int,
    frame_w: int,
    frame_h: int,
    source_text: str = "",
) -> tuple[int, int, int, int]:
    """Ngang: max(che hết chữ cũ, fit chữ dịch). Dọc: sát trên, nới đáy."""
    import math

    sx0, sy0, sx1, sy1 = seed
    sw, sh = max(1, sx1 - sx0), max(1, sy1 - sy0)
    _pad_x, pad_top, pad_bot = _preview_cover_pad(font_size, frame_w)
    src = (source_text or "").strip()
    src_w = 0
    if src:
        try:
            from PIL import Image, ImageDraw, ImageFont

            probe = Image.new("RGB", (8, 8))
            draw = ImageDraw.Draw(probe)
            src_fs = max(int(round(font_size * 1.12)), int(round(sh * 0.92)), 28)
            font = ImageFont.truetype(_subtitle_font(), src_fs)
            raw = draw.textbbox((0, 0), src, font=font)[2]
            cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
            cjk_floor = int(math.ceil(cjk * src_fs * 1.15)) if cjk else 0
            outline = int(math.ceil(src_fs * 0.5))
            src_w = max(int(math.ceil(raw * 1.2)), cjk_floor) + outline
        except OSError:
            pass
    old_w = max(sw, _cover_box_width(src_w, frame_w) if src_w > 0 else 0)
    w = min(frame_w, max(old_w, auto_w))
    cx = (sx0 + sx1) / 2.0
    top_slack = int(round(sh * 0.26))
    y0 = max(0, sy0 + top_slack - pad_top)
    bot_extra = max(pad_bot, int(round(sh * 0.4)), int(round(font_size * 0.7)))
    y1 = min(frame_h, sy1 + bot_extra)
    x0 = max(0, int(round(cx - w / 2)))
    x1 = min(frame_w, x0 + w)
    return x0, y0, x1, max(y0 + 12, y1)


def _cover_to_anchor(
    cover: tuple[int, int, int, int],
    font_size: int,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int, int, int]:
    """Khớp LivePreviewEditor.coverToAnchor — cover hiển thị → anchor OCR."""
    pad_x, pad_top, pad_bot = _preview_cover_pad(font_size, frame_w)
    x0, y0, x1, y1 = cover
    ax0 = x0 + pad_x
    ay0 = y0 + pad_top
    ax1 = x1 - pad_x
    ay1 = y1 - pad_top - pad_bot
    ax0 = max(0, min(frame_w - 12, ax0))
    ay0 = max(0, min(frame_h - 12, ay0))
    ax1 = max(ax0 + 12, min(frame_w, ax1))
    ay1 = max(ay0 + 12, min(frame_h, ay1))
    return (ax0, ay0, ax1, ay1)


def _cover_box_over(
    ocr_box: tuple[int, int, int, int] | None,
    caption_box: tuple[int, int, int, int],
    font_size: int,
    frame_w: int,
    frame_h: int,
    source_text: str | None = None,
) -> tuple[int, int, int, int]:
    """Mode over: sát trên, bắt buộc che hết đáy + ngang."""
    pad_x, pad_top, pad_bot = _preview_cover_pad(font_size, frame_w)
    if ocr_box is None:
        x0, y0, x1, y1 = caption_box
        y0 -= pad_top
        y1 += pad_bot + _COVER_SHADOW_BOT
        x0 -= pad_x
        x1 += pad_x
        return (
            max(0, x0),
            max(0, y0),
            min(frame_w, x1),
            min(frame_h, y1),
        )
    cx0, _cy0, cx1, _cy1 = caption_box
    ox0, oy0, ox1, oy1 = ocr_box
    auto_w = max(cx1 - cx0 + 4, _fit_cover_width(ox1 - ox0, cx1 - cx0 + 4, frame_w))
    return _fit_hardsub_box(
        (ox0, oy0, ox1, oy1), auto_w, font_size, frame_w, frame_h, source_text or ""
    )


def _cover_box_fit(
    ocr_boxes: list[tuple[int, int, int, int]],
    text_box: tuple[int, int, int, int] | None,
    frame_w: int,
    frame_h: int,
    *,
    tight: bool = False,
) -> tuple[int, int, int, int] | None:
    """Khung che — tight=True: sát OCR, không nới theo caption (mode over)."""
    from .ocr_boxes import _union_box  # lazy: tránh cycle layout_geo ↔ ocr_boxes

    ocr_u = _union_box(ocr_boxes) if ocr_boxes else None
    if ocr_u is None and text_box is None:
        return None
    if ocr_u is not None:
        x0, y0, x1, y1 = ocr_u
    else:
        assert text_box is not None
        x0, y0, x1, y1 = text_box
    if text_box is not None and not tight:
        x0 = min(x0, text_box[0])
        x1 = max(x1, text_box[2])
        # below/above: caption nằm ngoài vùng che — chỉ nới ngang nếu cần
    cy = (y0 + y1) // 2
    if tight:
        pad_x, pad_y = 4, 2
        max_h = _cover_max_h(frame_h)
    else:
        pad_x = max(6, int(round(frame_w * 0.006)))
        pad_y = max(3, int(round(frame_h * 0.002)))
        max_h = max(36, int(frame_h * (0.34 if text_box is not None else 0.09)))
    x0 -= pad_x
    x1 += pad_x
    y0 -= pad_y
    y1 += pad_y
    if (y1 - y0) > max_h:
        y0, y1 = cy - max_h // 2, cy + max_h // 2
    # Full ngang video — khớp editor (không cắt 85–96%)
    max_w = frame_w
    if (x1 - x0) > max_w:
        cx = (x0 + x1) // 2
        x0, x1 = cx - max_w // 2, cx + max_w // 2
    return (
        max(0, x0),
        max(0, y0),
        min(frame_w, x1),
        min(frame_h, y1),
    )


def _tight_cover_box(
    frame_bgr: Any, hint_boxes: list[tuple[int, int, int, int]] | None
) -> tuple[int, int, int, int] | None:
    """ROI che sát bbox OCR — pad nhỏ, không phình ink ra gần full khung."""
    h, w = frame_bgr.shape[:2]
    max_h = _cover_max_h(h)

    if hint_boxes:
        hx0 = min(b[0] for b in hint_boxes)
        hy0 = min(b[1] for b in hint_boxes)
        hx1 = max(b[2] for b in hint_boxes)
        hy1 = max(b[3] for b in hint_boxes)
        x0, y0, x1, y1 = hx0 - 8, hy0 - 6, hx1 + 8, hy1 + 6
        if (y1 - y0) > max_h:
            cy = (hy0 + hy1) // 2
            y0, y1 = cy - max_h // 2, cy + max_h // 2
        return (max(0, x0), max(0, y0), min(w, x1), min(h, y1))

    # không có OCR: dò mực trong dải phụ đề, từ chối box quá to
    from .ocr_boxes import _cover_box_from_ink  # lazy: tránh cycle

    ink = _cover_box_from_ink(frame_bgr, None, tight=True)
    if ink is None:
        return None
    x0, y0, x1, y1 = ink
    bw, bh = x1 - x0, y1 - y0
    if bh > max_h or bw > int(w * 0.80) or bw < int(w * 0.10):
        return None
    return (max(0, x0), max(0, y0), min(w, x1), min(h, y1))


def _auto_subtitle_font_size(width: int, height: int) -> int:
    """Cỡ mặc định khi auto — khớp AUTO_SUBTITLE_FONT=48 của preview."""
    _ = width, height  # ponytail: flat default; scale theo bbox ở _layout_caption nếu cần
    return 48


def _resolve_segment_font_size(
    seg: dict[str, Any],
    width: int,
    height: int,
    *,
    project_font_size: int,
    default_font_size: int,
    auto_fontsize: bool,
) -> int:
    """Per-segment override → captionLayout bake → project → auto/default."""
    seg_fs = int(seg.get("fontSize") or 0)
    if seg_fs > 0:
        return max(8, min(120, seg_fs))
    cl = seg.get("captionLayout")
    if isinstance(cl, dict):
        cl_fs = int(cl.get("fontSize") or 0)
        if cl_fs > 0:
            return max(8, min(120, cl_fs))
    if not auto_fontsize:
        return default_font_size
    proj = int(project_font_size or 0)
    if proj > 0:
        return max(16, min(120, proj))
    return _auto_subtitle_font_size(width, height)


__all__ = [
    '_clamp_label_box',
    '_pick_label_box',
    '_ocr_mid_labels',
    '_ocr_mid_vertical',
    '_ocr_mid_hardsub_boxes',
    '_cover_max_h',
    '_preview_cover_pad',
    '_cover_bleed_x',
    '_cover_box_width',
    '_fit_cover_width',
    '_fit_hardsub_box',
    '_cover_to_anchor',
    '_cover_box_over',
    '_cover_box_fit',
    '_tight_cover_box',
    '_auto_subtitle_font_size',
    '_resolve_segment_font_size',
]
