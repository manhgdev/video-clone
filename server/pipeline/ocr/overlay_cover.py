"""Che + bố chữ dịch cho overlay OCR (giữa / dọc / nhãn) — đường riêng.

Khôi phục logic kiểu pre-LivePreview (c9): bbox OCR ∪ caption, pad mỏng sát ink;
vertical = cột; label = multi-box; mid = flash giữa. Không dùng preview pad cứng.
"""
from __future__ import annotations

import re
from typing import Any


def is_overlay_layout(layout: str | None) -> bool:
    return (layout or "horizontal") in ("vertical", "label", "mid")


def is_mid_flash_source(source: str | None) -> bool:
    """Hardsub giữa khung: CJK ngắn (pass mid) — không phải câu đáy dài."""
    compact = re.sub(r"\s+", "", source or "")
    if not compact:
        return False
    cjk = sum(1 for c in compact if "\u4e00" <= c <= "\u9fff")
    return 1 <= cjk <= 10 and len(compact) <= 14


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
) -> tuple[int, int, int, int] | None:
    """Pad mỏng đối xứng — che vừa ink, không phình."""
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
    cy = (y0 + y1) // 2
    pad_x = max(3, int(round(frame_w * 0.003)))
    pad_y = max(2, int(round(frame_h * 0.0015)))
    x0 -= pad_x
    x1 += pad_x
    y0 -= pad_y
    y1 += pad_y
    max_h = max(24, int(frame_h * (0.18 if text_box is not None else 0.07)))
    if (y1 - y0) > max_h:
        y0, y1 = cy - max_h // 2, cy + max_h // 2
    return (
        max(0, x0),
        max(0, y0),
        min(frame_w, x1),
        min(frame_h, y1),
    )


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
