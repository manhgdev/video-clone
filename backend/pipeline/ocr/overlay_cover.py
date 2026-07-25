"""Che + bố chữ dịch cho overlay OCR (giữa / dọc / nhãn) — đường riêng.

Khôi phục logic kiểu pre-LivePreview (c9): bbox OCR ∪ caption, pad mỏng sát ink;
vertical = cột; label = multi-box; mid = flash giữa. Không dùng preview pad cứng.
"""
from __future__ import annotations

import math
import re
from typing import Any


def mid_bottom_cutoff(frame_w: int, frame_h: int) -> float:
    """Start of the visual bottom band for the decoded input aspect ratio."""
    if frame_w <= 0 or frame_h <= 0:
        return 0.78
    aspect = frame_w / frame_h
    # ponytail: log scale is reciprocal for portrait/landscape and covers any
    # input ratio; clamp only pathological panoramas/tall strips.
    cutoff = 0.75 - 0.03 * math.log2(aspect) / math.log2(16 / 9)
    return max(0.70, min(0.80, cutoff))


def is_overlay_layout(layout: str | None) -> bool:
    return (layout or "horizontal") in ("vertical", "label", "mid")


def is_mid_flash_source(source: str | None) -> bool:
    """Hardsub giữa khung: CJK ngang (pass mid) — không phải câu đáy dài không bbox."""
    compact = re.sub(r"\s+", "", source or "")
    if not compact:
        return False
    cjk = sum(1 for c in compact if "\u4e00" <= c <= "\u9fff")
    return 1 <= cjk <= 20 and len(compact) <= 28


def use_classic_overlay_cover(
    *,
    layout: str | None,
    source: str | None,
    has_preview_layout: bool,
) -> bool:
    """True → che/ chữ theo OCR overlay (classic), không qua preview over."""
    if has_preview_layout:
        return False
    if is_overlay_layout(layout):
        return True
    return is_mid_flash_source(source)


def classic_cover_fit(
    ocr_boxes: list[tuple[int, int, int, int]],
    text_box: tuple[int, int, int, int] | None,
    frame_w: int,
    frame_h: int,
    *,
    font_size: int = 36,
) -> tuple[int, int, int, int] | None:
    """Pad mid/OCR như FE fitMidOcrCover — che stroke/shadow, không phình đáy."""
    if not ocr_boxes and text_box is None:
        return None
    if ocr_boxes:
        x0 = min(b[0] for b in ocr_boxes)
        y0 = min(b[1] for b in ocr_boxes)
        x1 = max(b[2] for b in ocr_boxes)
        y1 = max(b[3] for b in ocr_boxes)
    else:
        assert text_box is not None
        x0, y0, x1, y1 = text_box
    if text_box is not None:
        x0 = min(x0, text_box[0])
        y0 = min(y0, text_box[1])
        x1 = max(x1, text_box[2])
        y1 = max(y1, text_box[3])
    sh = max(1, y1 - y0)
    sw = max(1, x1 - x0)
    cx = (x0 + x1) / 2.0
    fs = max(12, int(font_size))
    pad_x = max(3, int(round(frame_w * 0.003)))
    pad_top = max(2, int(round(fs * 0.04)))
    pad_bot = max(6, int(round(fs * 0.18))) + 4  # + COVER_SHADOW_BOT
    top_slack = int(round(sh * 0.12))
    bot_extra = max(pad_bot, int(round(sh * 0.12)), int(round(fs * 0.2)))
    ny0 = max(0, y0 + top_slack - pad_top)
    ny1 = min(frame_h, y1 + bot_extra)
    w = min(frame_w, sw + pad_x * 2)
    nx0 = max(0, int(round(cx - w / 2)))
    nx1 = min(frame_w, nx0 + w)
    max_h = max(24, int(frame_h * (0.18 if text_box is not None else 0.10)))
    if (ny1 - ny0) > max_h:
        cy = (ny0 + ny1) // 2
        ny0, ny1 = cy - max_h // 2, cy + max_h // 2
        ny0 = max(0, ny0)
        ny1 = min(frame_h, ny1)
    return (nx0, ny0, nx1, max(ny0 + 12, ny1))


def default_overlay_paint(
    layout: str,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int, int, int]:
    """Fallback nhỏ khi chưa có OCR box."""
    if layout == "vertical":
        x0 = int(frame_w * 0.04)
        y0 = int(frame_h * 0.12)
        x1 = x0 + max(22, int(frame_w * 0.06))
        y1 = y0 + int(frame_h * 0.28)
        return (x0, y0, x1, y1)
    if layout == "label":
        return (
            int(frame_w * 0.06),
            int(frame_h * 0.18),
            int(frame_w * 0.20),
            int(frame_h * 0.24),
        )
    # mid
    return (
        int(frame_w * 0.32),
        int(frame_h * 0.44),
        int(frame_w * 0.68),
        int(frame_h * 0.50),
    )
