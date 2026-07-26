"""Hardsub cover + caption burn — layout_text."""
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

from .layout_geo import (
    _cover_bleed_x, _cover_box_width, _cover_max_h, _cover_to_anchor,
    _fit_cover_width, _fit_hardsub_box, _preview_cover_pad,
    _auto_subtitle_font_size, _resolve_segment_font_size,
)
from pipeline.export.fonts import _font_for_preset, _subtitle_font, _subtitle_font_vertical
from pipeline.export.cover_mask import _apply_cover_mask, _parse_hex_color


def _ink_w(draw: Any, text: str, font: Any) -> int:
    """Bề ngang mực chữ — bbox + getlength + slack (khớp FE measureLineWidth)."""
    if not text:
        return 0
    bb = draw.textbbox((0, 0), text, font=font)
    raw = max(0, int(bb[2] - bb[0]))
    try:
        raw = max(raw, int(math.ceil(float(font.getlength(text)))))
    except Exception:
        pass
    fs = int(getattr(font, "size", 24) or 24)
    # Slack cố định (khớp FE) — không *1.08 để cover khỏi phình lệch
    return int(math.ceil(raw + max(6, fs * 0.12)))


def _layout_caption_over(
    text: str,
    font_size: int,
    ocr_box: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    source_text: str = "",
    *,
    font_path: str | None = None,
) -> tuple[dict[str, Any], tuple[int, int, int, int]]:
    """Layout over khớp preview — trả (caption lay, cover box)."""
    from PIL import Image, ImageDraw, ImageFont

    pad_x, pad_top, pad_bot = _preview_cover_pad(font_size, frame_w)
    ox0, oy0, ox1, oy1 = ocr_box
    ocr_w, ocr_h = ox1 - ox0, oy1 - oy0
    cx = (ox0 + ox1) // 2
    font_path = font_path or _subtitle_font()
    font = ImageFont.truetype(font_path, font_size)
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)

    trimmed = (text or "").strip()
    # Full ngang frame; 1 dòng (co font) rồi mới 2 dòng — khớp editor
    max_inner = max(24, frame_w - max(8, pad_x * 2))
    size = font_size
    lines = [trimmed] if trimmed else [""]
    line_hs: list[int] = [font_size]
    gap_line = max(2, font_size // 8)
    text_w = 0
    text_h = font_size
    text_block_h = font_size
    min_one_line = max(12, int(round(font_size * 0.8)))
    while size > min_one_line:
        font = ImageFont.truetype(font_path, size)
        one_w = _ink_w(draw, trimmed, font) if trimmed else 0
        if one_w <= int(max_inner * 1.02):
            break
        size -= 1

    font = ImageFont.truetype(font_path, size)
    one_w = _ink_w(draw, trimmed, font) if trimmed else 0

    if one_w <= int(max_inner * 1.06):
        lines = [trimmed] if trimmed else [""]
        line_boxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    else:
        size = font_size
        while size >= 12:
            font = ImageFont.truetype(font_path, size)
            lines = _wrap_text(draw, trimmed, font, max_inner)
            if len(lines) > 2:
                lines = _merge_to_n_lines(lines, 2)
            line_boxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
            tw = max((_ink_w(draw, ln, font) for ln in lines), default=0)
            if tw <= max_inner * 1.02 or size <= 12:
                break
            size -= 1

    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    gap_line = max(2, size // 8)
    text_w = max((_ink_w(draw, ln, font) for ln in lines), default=one_w)
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    text_block_h = int(math.ceil(len(lines) * size * 1.12 + 4))
    font_size = size
    line_h = (max(line_hs) if line_hs else font_size) + gap_line

    src_w = 0
    src = (source_text or "").strip()
    if src:
        src_fs = max(int(round(font_size * 1.12)), int(round(ocr_h * 0.92)), 28)
        try:
            src_font = ImageFont.truetype(font_path, src_fs)
            raw = _ink_w(draw, src, src_font)
            cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
            cjk_floor = int(math.ceil(cjk * src_fs * 1.15)) if cjk else 0
            outline = int(math.ceil(src_fs * 0.5))
            src_w = max(int(math.ceil(raw * 1.2)), cjk_floor) + outline
        except OSError:
            pass

    orig_w = max(src_w, ocr_w) if src else ocr_w
    content_w = max(orig_w, text_w)
    # khớp FE CAP_PAD_X=4
    cap_pad_x = 4
    cap_w = int(text_w + cap_pad_x * 2)
    # Cover full ngang được (frame_w)
    auto_w = min(frame_w, max(_fit_cover_width(content_w, cap_w, frame_w), cap_w))
    cover_box = _fit_hardsub_box(
        (ox0, oy0, ox1, oy1),
        auto_w,
        font_size,
        frame_w,
        frame_h,
        src,
        font_path=font_path,
    )
    cover_x0, cover_y0, cover_x1, cover_y1 = cover_box
    cover_w = cover_x1 - cover_x0
    cover_h = cover_y1 - cover_y0

    cap_w = int(text_w + cap_pad_x * 2)
    if len(lines) == 1:
        edge = max(2, int(round(cover_w * 0.02)))
        cap_w = min(cover_w, max(cap_w, cover_w - edge * 2))
    cap_x0 = max(cover_x0, min(cover_x1 - cap_w, int((cover_x0 + cover_x1) / 2 - cap_w / 2)))
    cap_y0 = cover_y0 + max(0, (cover_h - text_block_h) // 2)
    cap_box = (cap_x0, cap_y0, cap_x0 + cap_w, cap_y0 + text_block_h)

    lay = {
        "box": cap_box,
        "lines": lines,
        "font": font,
        "fontsize": font_size,
        "line_h": line_h,
        "line_hs": line_hs,
        "gap_line": gap_line,
        "pad_y": max(2, font_size // 10),
        "text_h": text_h,
        "cover_plate": False,
        "vertical": False,
    }
    return lay, cover_box


def _preview_caption_layout(
    segment: dict[str, Any],
    default_fs: int,
    font_getter: Any,
    *,
    layout_mode: str = "",
) -> dict[str, Any] | None:
    """Layout caption gửi từ preview — không tính lại trên server (WYSIWYG)."""
    cl = segment.get("captionLayout")
    if not isinstance(cl, dict):
        return None
    lines_raw = cl.get("lines")
    if not isinstance(lines_raw, list) or not lines_raw:
        return None
    try:
        x = int(round(float(cl["x"])))
        y = int(round(float(cl["y"])))
        bw = int(round(float(cl["w"])))
        bh = int(round(float(cl["h"])))
        # mid preview có thể <16 — khớp font đã bake, không sàn 16
        fs = max(8, min(120, int(cl.get("fontSize") or default_fs)))
    except (KeyError, TypeError, ValueError):
        return None
    if bw <= 0 or bh <= 0:
        return None
    lines = [str(ln) for ln in lines_raw if str(ln).strip() or len(lines_raw) == 1]
    if not lines:
        return None
    from PIL import Image, ImageDraw

    mode = (layout_mode or str(segment.get("layout") or "")).lower()
    # captionLayout is the Editor's committed geometry.  Its font/line fit was
    # measured before export; expanding the box again with PIL metrics makes
    # the rendered video drift from the preview (especially on Vietnamese
    # glyphs and Windows font fallback).
    font = font_getter(fs)
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)
    line_boxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]

    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    gap_line = max(2, fs // 8)
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    line_h = (max(line_hs) if line_hs else fs) + gap_line
    mode = (layout_mode or str(segment.get("layout") or "")).lower()
    out: dict[str, Any] = {
        "box": (x, y, x + bw, y + bh),
        "lines": lines,
        "font": font,
        "fontsize": fs,
        "line_h": line_h,
        "line_hs": line_hs,
        "gap_line": gap_line,
        "pad_y": max(2, fs // 10),
        "text_h": text_h,
        "cover_plate": False,
        "vertical": mode == "vertical",
        "label": mode == "label",
    }
    fill = _text_fill_rgba(segment)
    if fill is not None:
        out["fill"] = fill
    return out


def _layout_caption_in_cover(
    text: str,
    font_size: int,
    cover: tuple[int, int, int, int],
    frame_w: int,
    font_getter: Any,
) -> dict[str, Any]:
    """Chữ trong cover cố định — khớp layoutCaptionInCover (không nới cover)."""
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = cover
    cw = max(1, x1 - x0)
    ch = max(1, y1 - y0)
    edge = max(4, int(round(cw * 0.03)))
    max_inner = max(24, cw - edge * 2)
    font = font_getter(font_size)
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)
    trimmed = (text or "").strip()
    one_w = draw.textbbox((0, 0), trimmed, font=font)[2] if trimmed else 0
    if not trimmed:
        lines = [""]
    elif one_w <= int(max_inner * 1.1):
        lines = [trimmed]
    else:
        lines = _wrap_text(draw, trimmed, font, max_inner)
        if len(lines) > 3:
            lines = _merge_to_n_lines(lines, 3)
    line_boxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    gap_line = max(2, font_size // 8)
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    line_h = (max(line_hs) if line_hs else font_size) + gap_line
    text_block_h = int(math.ceil(len(lines) * font_size * 1.12 + 4))
    text_w = max((b[2] - b[0]) for b in line_boxes) if line_boxes else one_w
    pad_x = max(4, font_size // 6)
    if len(lines) == 1:
        caption_w = min(cw, max(text_w + pad_x * 2, cw - edge * 2))
    else:
        caption_w = min(cw, text_w + pad_x * 2)
    cx = (x0 + x1) / 2.0
    cap_x = int(round(max(x0, min(x1 - caption_w, cx - caption_w / 2))))
    cap_y = int(round(y0 + max(0, (ch - text_block_h) / 2)))
    return {
        "box": (cap_x, cap_y, cap_x + int(caption_w), cap_y + text_block_h),
        "lines": lines,
        "font": font,
        "fontsize": font_size,
        "line_h": line_h,
        "line_hs": line_hs,
        "gap_line": gap_line,
        "pad_y": max(2, font_size // 10),
        "text_h": text_h,
        "cover_plate": False,
        "vertical": False,
    }


# Phần vẽ (render RGBA/blit) tách sang draw_text.py — re-export giữ import cũ
from .draw_text import (  # noqa: F401
    _CSS_LINE_FACTOR,
    _blit_overlay,
    _caption_overlay,
    _css_block_layout,
    _hex_rgba,
    _image_overlay,
    _paint_caption,
    _text_fill_rgba,
)


def _wrap_balanced(draw: Any, text: str, font: Any, max_w: int) -> list[str]:
    """Wrap rồi cân dòng (tránh dòng cuối chỉ còn 1 từ)."""
    lines = _wrap_text(draw, text, font, max_w)
    if len(lines) < 2:
        return lines
    last, prev = lines[-1], lines[-2]
    trial = f"{prev} {last}"
    if draw.textbbox((0, 0), trial, font=font)[2] <= max_w:
        return lines[:-2] + [trial]
    raw = text.strip()
    if " " not in raw:
        return lines
    mid = len(raw) // 2
    cut = raw.rfind(" ", 0, mid + 1)
    if cut < 1:
        cut = raw.find(" ", mid)
    if cut < 1:
        return lines
    a, b = raw[:cut].strip(), raw[cut + 1 :].strip()
    if (
        a
        and b
        and draw.textbbox((0, 0), a, font=font)[2] <= max_w
        and draw.textbbox((0, 0), b, font=font)[2] <= max_w
    ):
        return [a, b]
    return lines


def _layout_caption(
    text: str,
    font: Any,
    fontsize: int,
    ocr_box: tuple[int, int, int, int] | None,
    frame_w: int,
    frame_h: int,
    *,
    cover_plate: bool = False,
    placement: str = "over",
) -> dict[str, Any]:
    """placement: over=trên dải OCR (cover); below/above=ngoài dải hardsub."""
    from PIL import Image, ImageDraw, ImageFont

    place = (placement or "over").lower()
    if place not in ("over", "below", "above"):
        place = "over"
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)
    font_path = str(getattr(font, "path", "") or _subtitle_font())
    max_lines = 3
    # below/above: cỡ ≈ chiều cao bbox che (OCR) — không dùng 48/project to
    if ocr_box and place in ("below", "above"):
        ocr_w0 = max(16, int(ocr_box[2] - ocr_box[0]))
        ocr_h0 = max(12, int(ocr_box[3] - ocr_box[1]))
        compact_n = max(1, len(re.sub(r"\s+", "", text or "")))
        by_h = int(ocr_h0 * (0.78 if compact_n <= 12 else 0.65))
        by_w = int(ocr_w0 / max(2.5, compact_n * 0.55))
        cap_fs = max(10, min(by_h, by_w, int(ocr_h0 * 0.92), 48))
        if fontsize and fontsize > 0:
            cap_fs = max(10, min(int(fontsize), max(cap_fs, int(ocr_h0 * 0.95))))
        fontsize = cap_fs
    est_3line = int(fontsize * 3.4) + 16
    size = fontsize
    font_use = font
    # over: wrap theo OCR; below/above: wrap ≈ bề rộng dải che (không 90% frame)
    if ocr_box and place == "over":
        ocr_w = int(ocr_box[2] - ocr_box[0])
        target_w = max(24, ocr_w)
        max_box_h = max(est_3line, _cover_max_h(frame_h, fontsize))
    elif ocr_box and place in ("below", "above"):
        ocr_w = max(24, int(ocr_box[2] - ocr_box[0]))
        target_w = max(ocr_w, min(int(frame_w * 0.92), int(ocr_w * 1.2) + fontsize * 2))
        max_box_h = max(est_3line, int(fontsize * 3.2) + 12)
    else:
        target_w = int(frame_w * 0.90)
        max_box_h = max(int(frame_h * 0.26), est_3line, 72)
    if place in ("below", "above"):
        max_box_h = min(max_box_h, int(frame_h * 0.22))
    elif not (ocr_box and place == "over"):
        max_box_h = min(max_box_h, int(frame_h * 0.32))
    lines = [text]
    for scale in (1.0, 0.94, 0.88, 0.82, 0.76, 0.70, 0.64, 0.58, 0.52, 0.46, 0.40):
        size = max(10, int(fontsize * scale))
        pad_x = max(3, size // 7)
        pad_y = max(2, size // 10)
        gap_line = max(2, size // 8)
        try:
            font_use = ImageFont.truetype(font_path, size)
        except OSError:
            font_use = font
        if ocr_box and place == "over":
            inner_w = max(24, frame_w - pad_x * 4)
        else:
            inner_w = max(24, target_w - pad_x * 2)
        one_w = draw.textbbox((0, 0), text, font=font_use)[2]
        if one_w <= inner_w:
            cand = [text]
        else:
            cand = _wrap_text(draw, text, font_use, inner_w)
            if len(cand) == 2:
                cand = _wrap_balanced(draw, text, font_use, inner_w)
            elif len(cand) > max_lines:
                cand = _merge_to_n_lines(cand, max_lines)
        overflow = any(
            draw.textbbox((0, 0), ln, font=font_use)[2] > inner_w for ln in cand
        )
        lbs = [draw.textbbox((0, 0), ln, font=font_use) for ln in cand]
        tw = max((b[2] - b[0]) for b in lbs) if lbs else 0
        th = sum(max(1, b[3] - b[1]) for b in lbs) + gap_line * max(0, len(cand) - 1)
        # 3 dòng: nới max_box_h theo text thật (không kẹp OCR 1 dòng)
        need_h = th + pad_y * 2
        allow_h = max(max_box_h, need_h)
        allow_h = min(allow_h, int(frame_h * 0.34))
        if (
            not overflow
            and tw <= inner_w
            and need_h <= allow_h
            and len(cand) <= max_lines
        ):
            lines = cand
            max_box_h = allow_h
            break
        lines = cand
    else:
        size = max(13, int(fontsize * 0.40))
        pad_x = max(4, size // 6)
        pad_y = max(2, size // 10)
        gap_line = max(2, size // 8)
        try:
            font_use = ImageFont.truetype(font_path, size)
        except OSError:
            font_use = font
        inner_w = max(24, target_w - pad_x * 2)
        lines = _wrap_text(draw, text, font_use, inner_w)
        if len(lines) > max_lines:
            lines = _merge_to_n_lines(lines, max_lines)

    pad_x = max(4, size // 6)
    pad_y = max(2, size // 10)
    gap_line = max(2, size // 8)
    line_boxes = [draw.textbbox((0, 0), ln, font=font_use) for ln in lines]
    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    line_h = (max(line_hs) if line_hs else size) + gap_line
    text_w = max((b[2] - b[0]) for b in line_boxes) if line_boxes else 0
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    box_w = min(frame_w, max(text_w + pad_x * 2, 1))
    box_h = text_h + pad_y * 2
    # 3 dòng: box_h = đủ text (không cắt)
    box_h = min(max(box_h, text_h + pad_y * 2), frame_h)
    box_w = min(box_w, frame_w)
    gap = max(3, size // 6) if place in ("below", "above") else max(6, size // 4)
    if ocr_box and place == "over":
        ox0, oy0, ox1, oy1 = ocr_box
        ocr_w = ox1 - ox0
        ocr_h = oy1 - oy0
        ocr_cx = (ox0 + ox1) // 2
        x0 = max(0, min(frame_w - box_w, ocr_cx - box_w // 2))
        if box_h <= ocr_h:
            y0 = oy0 + max(0, (ocr_h - box_h) // 2)
        else:
            cy = (oy0 + oy1) // 2
            y0 = max(0, min(frame_h - box_h, cy - box_h // 2))
        x1, y1 = x0 + box_w, y0 + box_h
    elif ocr_box:
        # below/above: sát mép dải che (OCR), căn giữa ngang theo bbox
        cx = (ocr_box[0] + ocr_box[2]) // 2
        if place == "below":
            y0 = min(frame_h - box_h, ocr_box[3] + gap)
        elif place == "above":
            y0 = max(0, ocr_box[1] - gap - box_h)
        else:
            cy = (ocr_box[1] + ocr_box[3]) // 2
            y0 = max(0, min(frame_h - box_h, cy - box_h // 2))
        x0 = max(0, min(frame_w - box_w, cx - box_w // 2))
        x1, y1 = x0 + box_w, y0 + box_h
    else:
        cx = frame_w // 2
        if place == "above":
            y0 = max(0, int(frame_h * 0.70) - box_h // 2)
        else:
            y0 = max(0, min(frame_h - box_h, int(frame_h * 0.90) - box_h))
        x0 = max(0, min(frame_w - box_w, cx - box_w // 2))
        x1, y1 = x0 + box_w, y0 + box_h
    return {
        "box": (x0, y0, x1, y1),
        "lines": lines,
        "font": font_use,
        "fontsize": size,
        "line_h": line_h,
        "line_hs": line_hs,
        "gap_line": gap_line,
        "pad_y": pad_y,
        "text_h": text_h,
        "cover_plate": False,
        "vertical": False,
    }


def _layout_mid_caption(
    text: str,
    font_getter: Any,
    ocr_box: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    *,
    preferred_fs: int = 0,
) -> dict[str, Any]:
    """Mid: wrap + font fit trong bbox OCR (không cắt / không phình H)."""
    from PIL import Image, ImageDraw

    ox0, oy0, ox1, oy1 = (int(ocr_box[0]), int(ocr_box[1]), int(ocr_box[2]), int(ocr_box[3]))
    cw = max(8, ox1 - ox0)
    ch = max(8, oy1 - oy0)
    raw = (text or "").strip() or " "
    line_mul = 1.15
    # pad mỏng — chữ fill bbox; không nới khung OCR
    pad_x = max(3, int(round(cw * 0.02)))
    pad_y = max(3, int(round(ch * 0.04)))
    inner_w = max(12, cw - pad_x * 2)
    inner_h = max(12, ch - pad_y * 2)
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)

    def _fit(fs: int) -> list[str]:
        font_use = font_getter(fs)
        one_w = draw.textbbox((0, 0), raw, font=font_use)[2]
        if one_w <= inner_w:
            return [raw]
        lines = _wrap_text(draw, raw, font_use, inner_w)
        if len(lines) == 2:
            lines = _wrap_balanced(draw, raw, font_use, inner_w)
        return lines

    def _kept(lines: list[str]) -> bool:
        return re.sub(r"\s+", " ", " ".join(lines)).strip() == re.sub(r"\s+", " ", raw).strip()

    # Ưu tiên 1 dòng (binary max fs) — CAP-MID vừa dịch phải giống kéo tay
    max_one = min(
        int(preferred_fs) if preferred_fs > 0 else 40,
        int(inner_h / line_mul),
        40,
    )
    lo, hi, best_one = 8, max(8, max_one), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        one_w = draw.textbbox((0, 0), raw, font=font_getter(mid))[2]
        if one_w <= inner_w and mid * line_mul <= inner_h + 0.5:
            best_one = mid
            lo = mid + 1
        else:
            hi = mid - 1
    compact = len(re.sub(r"\s+", "", raw))
    words = [w for w in re.split(r"\s+", raw) if w]
    prefer_one = len(words) <= 6 or compact <= 24
    if best_one >= (9 if prefer_one else 11):
        size, lines = best_one, [raw]
    else:
        size = (
            max(8, min(48, int(preferred_fs)))
            if preferred_fs > 0
            else max(
                8,
                min(
                    max(8, int(inner_h * 0.55)),
                    int(inner_h / line_mul),
                    int(inner_w / max(3, compact * 0.58)),
                ),
            )
        )
        lines = _fit(size)
        while size > 8 and (
            not _kept(lines)
            or len(lines) * size * line_mul > inner_h
            or any(
                draw.textbbox((0, 0), ln, font=font_getter(size))[2] > inner_w
                for ln in lines
            )
        ):
            size -= 1
            lines = _fit(size)
        if draw.textbbox((0, 0), raw, font=font_getter(size))[2] <= inner_w:
            lines = [raw]
    font_use = font_getter(size)
    line_boxes = [draw.textbbox((0, 0), ln, font=font_use) for ln in lines]
    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    gap_line = max(2, size // 8)
    line_h = (max(line_hs) if line_hs else size) + gap_line
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    # Keep the same 0.5em side breathing room as the preview.
    pad_x = max(pad_x, int(math.ceil(size * 0.5)))
    # ponytail: nới rộng bbox ngang nếu chữ dịch vẫn dài hơn bbox gốc
    max_line_w = max(
        (draw.textbbox((0, 0), ln, font=font_use)[2] for ln in lines), default=0
    )
    need_w = max_line_w + pad_x * 2
    box_w = min(frame_w, max(cw, need_w))
    box_h = ch
    cx = (ox0 + ox1) // 2
    x0 = max(0, min(frame_w - box_w, cx - box_w // 2))
    y0 = max(0, min(frame_h - box_h, oy0))
    x1, y1 = x0 + box_w, y0 + box_h
    return {
        "box": (x0, y0, x1, y1),
        "lines": lines,
        "font": font_use,
        "fontsize": size,
        "line_h": line_h,
        "line_hs": line_hs,
        "gap_line": gap_line,
        "pad_y": pad_y,
        "pad_x": pad_x,
        "text_h": text_h,
        "cover_plate": False,
        "vertical": False,
    }


def _merge_to_n_lines(parts: list[str], n: int) -> list[str]:
    """Gộp list dòng thành đúng ≤ n dòng (giữ đủ chữ, không cắt)."""
    parts = [p for p in parts if (p or "").strip()]
    if not parts or n <= 1:
        return [" ".join(parts).strip()] if parts else [""]
    if len(parts) <= n:
        return parts
    # chia đều theo số từ
    words: list[str] = []
    for p in parts:
        words.extend(p.split())
    if not words:
        return [""]
    if len(words) <= n:
        return words
    out: list[str] = []
    per = max(1, (len(words) + n - 1) // n)
    for i in range(0, len(words), per):
        out.append(" ".join(words[i : i + per]))
        if len(out) == n - 1:
            rest = " ".join(words[i + per :])
            if rest:
                out.append(rest)
            break
    return out[:n] if out else [""]


def _layout_caption_vertical(
    text: str,
    font: Any,
    fontsize: int,
    ocr_box: tuple[int, int, int, int] | None,
    frame_w: int,
    frame_h: int,
) -> dict[str, Any]:
    """Chữ dọc full cột OCR — CJK 1 ký tự; VI/Latin 1 từ/dòng, giãn đều theo chiều cao khung."""
    from PIL import Image, ImageDraw, ImageFont

    raw = (text or "").strip()
    if not raw:
        return _layout_caption(text, font, fontsize, ocr_box, frame_w, frame_h)
    cjk_n = sum(1 for c in raw if "\u4e00" <= c <= "\u9fff")
    pure_cjk = cjk_n >= max(2, len(re.sub(r"\s+", "", raw)) * 0.5)
    if pure_cjk:
        units = list(re.sub(r"\s+", "", raw))
    else:
        words = [w for w in re.split(r"[\s·・/|]+", raw) if w]
        if not words:
            words = [raw]
        # ponytail: all-caps single word (brand/abbreviation) → 1 char per line
        if len(words) == 1 and re.fullmatch(r"[A-Z]+", words[0]):
            units = list(words[0])
        elif len(words) == 1 and len(words[0]) > 10:
            w0 = words[0]
            mid = max(1, len(w0) // 2)
            cut = -1
            for i in range(mid, max(1, mid - 4), -1):
                if w0[i - 1].islower() and w0[i].isupper():
                    cut = i
                    break
            words = [w0[:cut], w0[cut:]] if cut > 0 else [w0]
            units = []
            for w in words:
                if any("\u4e00" <= c <= "\u9fff" for c in w):
                    units.append(w)
                else:
                    units.append(w[:1].upper() + w[1:] if len(w) > 1 else w.upper())
        else:
            units = []
            for w in words:
                if any("\u4e00" <= c <= "\u9fff" for c in w):
                    units.append(w)
                else:
                    units.append(w[:1].upper() + w[1:] if len(w) > 1 else w.upper())
    if not units:
        return _layout_caption(text, font, fontsize, ocr_box, frame_w, frame_h)

    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)
    # Font display đậm; CJK fallback Unicode nếu rounded không vẽ được
    font_path = str(
        getattr(font, "path", "")
        or (_subtitle_font_vertical() if not pure_cjk else _subtitle_font())
    )
    # title dọc dài (CJK ≥4): full cột; text ngắn: bám OCR, không phình 42% khung
    n_units_est = len(re.sub(r"\s+", "", raw)) if pure_cjk else len(
        [w for w in re.split(r"[\s·・/|]+", raw) if w] or [raw]
    )
    compact = n_units_est <= 3 and not pure_cjk
    # Cover = bbox OCR (không nới cột) — thu font cho vừa
    if ocr_box:
        ocr_w = max(16, ocr_box[2] - ocr_box[0])
        ocr_h = max(24, ocr_box[3] - ocr_box[1])
        target_h = ocr_h
        max_w = ocr_w
        by_h = int((ocr_h * 0.7) / max(1, n_units_est))
        by_w = int(ocr_w * (0.85 if pure_cjk else 0.7))
        base = max(8, min(40, int(fontsize) if fontsize else by_h, by_h, by_w))
    else:
        target_h = int(frame_h * (0.22 if compact else 0.4))
        max_w = int(frame_w * 0.1)
        base = max(10, min(36, int(fontsize) if fontsize else 22, int(frame_w * 0.06)))

    n = len(units)
    size = base
    font_use = font
    ch_ws: list[int] = []
    ch_hs: list[int] = []
    pad_x = pad_y = gap = 2
    # chỉ shrink — không scale 1.35 (to hơn bbox)
    for scale in (1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.52, 0.44, 0.36):
        size = max(8, int(base * scale))
        try:
            font_use = ImageFont.truetype(font_path, size)
        except OSError:
            try:
                font_use = ImageFont.truetype(_subtitle_font(), size)
            except OSError:
                font_use = font
        pad_x = max(1, size // 12)
        pad_y = max(2, size // 12)
        boxes = [draw.textbbox((0, 0), u, font=font_use) for u in units]
        ch_ws = [max(1, b[2] - b[0]) for b in boxes]
        ch_hs = [max(1, b[3] - b[1]) for b in boxes]
        col_w = max(ch_ws) if ch_ws else size
        sum_h = sum(ch_hs)
        min_gap = max(1, size // 12)
        min_h = sum_h + min_gap * max(0, n - 1) + pad_y * 2
        box_w = col_w + pad_x * 2
        if box_w <= max_w and min_h <= target_h:
            break

    col_w = max(ch_ws) if ch_ws else size
    sum_h = sum(ch_hs) if ch_hs else size * n
    gap = max(1, size // 12) if n > 1 else 0
    pad_y = max(2, size // 12)
    pad_x = max(1, size // 12)
    text_h = sum_h + gap * max(0, n - 1)
    # box = đúng cột OCR (không phình theo text)
    if ocr_box:
        x0, y0 = int(ocr_box[0]), int(ocr_box[1])
        box_w = max(8, int(ocr_box[2]) - x0)
        box_h = max(8, int(ocr_box[3]) - y0)
    else:
        box_w = min(frame_w, col_w + pad_x * 2)
        box_h = min(frame_h, text_h + pad_y * 2)
        x0 = max(0, (frame_w - box_w) // 2)
        y0 = max(0, int(frame_h * 0.22))
    return {
        "box": (x0, y0, x0 + box_w, y0 + box_h),
        "lines": units,
        "font": font_use,
        "fontsize": size,
        "line_h": (max(ch_hs) if ch_hs else size) + gap,
        "line_hs": ch_hs,
        "gap_line": gap,
        "pad_y": pad_y,
        "text_h": text_h,
        "cover_plate": False,
        "vertical": True,
        "distribute": False,
    }


def _wrap_text(draw: Any, text: str, font: Any, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if _ink_w(draw, trial, font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


__all__ = [
    '_clamp_label_box',
    '_pick_label_box',
    '_ocr_mid_labels',
    '_ocr_mid_vertical',
    '_ocr_mid_hardsub_boxes',
    '_layout_caption_over',
    '_preview_caption_layout',
    '_layout_caption_in_cover',
    '_paint_caption',
    '_text_fill_rgba',
    '_hex_rgba',
    '_caption_overlay',
    '_blit_overlay',
    '_wrap_balanced',
    '_layout_caption',
    '_layout_mid_caption',
    '_merge_to_n_lines',
    '_layout_caption_vertical',
    '_wrap_text',
]
