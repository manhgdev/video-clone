"""Vẽ caption/overlay RGBA + blit lên frame (khớp CSS preview).

Tách từ burn_parts/layout_text.py — layout quyết định box/dòng, file này chỉ vẽ.
"""
from __future__ import annotations

import math
from typing import Any

from pipeline.export.cover_mask import _parse_hex_color


def _paint_caption(frame_bgr: Any, layout: dict[str, Any]) -> Any:
    """Vẽ chữ trắng + viền — không plate/nền đen."""
    overlay = _caption_overlay(layout)
    if overlay is None:
        return frame_bgr
    return _blit_overlay(frame_bgr, overlay)


def _text_fill_rgba(segment: dict[str, Any] | None) -> tuple[int, int, int, int] | None:
    """Màu chữ free-text overlay (#RRGGBB) — None = trắng mặc định."""
    if not segment:
        return None
    raw = segment.get("textColor") or segment.get("color")
    if not isinstance(raw, str) or not raw.strip():
        return None
    r, g, b = _parse_hex_color(raw.strip(), (255, 255, 255))
    return (r, g, b, 255)


def _hex_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = (hex_color or "#ffffff").lstrip("#")
    if len(h) != 6:
        return (255, 255, 255, alpha)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (r, g, b, max(0, min(255, alpha)))
    except ValueError:
        return (255, 255, 255, alpha)


# line-height CSS theo mode preview: mid 1.1 (JSX overlayDisplayFontStyle),
# overlay = textarea free-text 1.25, dọc 1.0 + gap 0.08em, mặc định 1.12.
_CSS_LINE_FACTOR = {"mid": 1.1, "overlay": 1.25, "vertical": 1.0}


def _css_block_layout(mode: str, fs: float, n_lines: int, box_h: float) -> tuple[float, float]:
    """(top, step) của khối dòng CSS trong box — khớp flex preview.

    overlay: top-align (textarea); còn lại căn giữa (kể cả tràn — flex center
    tràn đều 2 phía, không kẹp 0). Dọc: pitch 1.08em + translateY(-0.06em).
    """
    n = max(1, int(n_lines))
    line_h = fs * _CSS_LINE_FACTOR.get(mode, 1.12)
    gap = fs * 0.08 if mode == "vertical" else 0.0
    block_h = line_h * n + gap * (n - 1)
    if mode == "overlay":
        top = 0.0
    else:
        top = (box_h - block_h) / 2.0
    if mode == "vertical":
        top -= fs * 0.06
    return top, line_h + gap


def _caption_overlay(layout: dict[str, Any]) -> tuple[Any, int, int] | None:
    """Render RGBA một lần / câu — blit nhanh mỗi khung."""
    import numpy as np
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = layout["box"]
    font = layout["font"]
    lines: list[str] = layout["lines"]
    pad_y = layout["pad_y"]
    text_h = layout["text_h"]
    line_hs: list[int] = layout.get("line_hs") or []
    gap_line = int(layout.get("gap_line") or max(2, layout.get("line_h", 20) // 8))
    fill = layout.get("fill") or (255, 255, 255, 255)
    if isinstance(layout.get("fill_hex"), str):
        fill = _hex_rgba(str(layout["fill_hex"]), 255)
    # Mặc định stroke=True nhưng mid/ngang dùng soft shadow (bản đẹp cũ), không outline dày
    stroke_on = layout.get("stroke")
    if stroke_on is None:
        stroke_on = True
    bg_style = str(layout.get("bg_style") or "none").lower()
    bg_hex = str(layout.get("bg_hex") or "#000000")
    bg_op = max(0, min(100, int(layout.get("bg_opacity") if layout.get("bg_opacity") is not None else 55)))
    css_cover_mode = str(layout.get("css_cover_mode") or "").lower()
    # Pad overlay for VI accents/descenders/shadow even in cover mode — FE often
    # uses overflow-visible; m=0 + wrong PIL origin was clipping bottoms of ạ/g/y.
    draw_probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    metric_box = draw_probe.textbbox((0, 0), "Áạgqỵ", font=font)
    metric_h = max(1, metric_box[3] - metric_box[1])
    m = max(6, int(math.ceil(metric_h * 0.3)))
    bw, bh = (x1 - x0) + 2 * m, (y1 - y0) + 2 * m
    if bw < 4 or bh < 4:
        return None
    overlay = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    box_w = x1 - x0
    box_h = y1 - y0
    # Nền chữ chỉ khi user chọn solid/box/blur
    if bg_style in ("solid", "box", "blur"):
        is_box = bg_style == "box"
        a = int(255 * max(0.15, min(0.92, bg_op / 100.0 * (0.55 if bg_style == "blur" else 1.0))))
        br, bg, bb, _ = _hex_rgba(bg_hex, a)
        pad = 4 if is_box else 2
        draw.rounded_rectangle(
            (m - pad, m - pad, m + box_w + pad, m + box_h + pad),
            radius=6 if is_box else 4,
            fill=(br, bg, bb, a),
            # border trắng mờ khớp preview `border: 1px solid rgba(255,255,255,0.12)`
            outline=(255, 255, 255, 30) if is_box else None,
            width=1,
        )
    # Editor flex: line boxes centered in cover. PIL draw origin needs -bbox.
    fs = float(getattr(font, "size", 0) or layout.get("fontsize") or 48)
    # line-height khớp preview: mid 1.1, overlay(textarea) 1.25, dọc 1.0 + gap
    # 0.08em, còn lại 1.12. Dọc thêm translateY(-0.06em) như JSX.
    css_line_h = fs * _CSS_LINE_FACTOR.get(css_cover_mode, 1.12) if css_cover_mode else 0.0
    css_gap = fs * 0.08 if css_cover_mode == "vertical" else 0.0
    if css_cover_mode:
        css_top, _css_step = _css_block_layout(
            css_cover_mode, fs, len(lines), float(box_h)
        )
        ty = m + css_top
    else:
        ty = m + max(0, (box_h - text_h) // 2) - int(round(metric_h * 0.06))
    thick = False  # ponytail: preview dùng soft shadow cho mọi layout; thick outline khác preview
    outline_thick = (
        (-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, -2), (-2, 2), (2, 2),
        (-3, -1), (3, -1), (-3, 1), (3, 1), (-1, -3), (1, -3), (-1, 3), (1, 3),
        (-2, -3), (2, -3), (-2, 3), (2, 3),
    )

    def _line_xy(line: str, line_top: float) -> tuple[int, int, int]:
        bb = draw.textbbox((0, 0), line, font=font)
        left, top = bb[0], bb[1]
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        tx = m + (box_w - tw) // 2 - left
        if css_cover_mode:
            # CSS đặt glyph theo metric font (baseline cố định mọi dòng), không
            # căn giữa ink từng dòng — ink-centering làm dòng có/không dấu nhảy y.
            ascent, descent = font.getmetrics()
            gy = int(round(line_top + (css_line_h - (ascent + descent)) / 2.0))
        else:
            gy = int(round(line_top - top))
        return tx, gy, th

    # CAP-MID/label explicitly override captionChromeStyle with no shadow.
    if stroke_on and not thick and css_cover_mode not in ("mid", "label"):
        from PIL import ImageFilter
        shadow_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        # CSS drop-shadow: 0 2px 4px (relative to 48px base font in preview)
        scale_f = max(0.5, fs / 48.0)
        dy_off = int(round(2 * scale_f))
        blur_rad = max(1.0, 3.0 * scale_f)

        ty_s = ty
        for i, line in enumerate(lines):
            tx, gy, th = _line_xy(line, ty_s)
            if not css_cover_mode:
                th = line_hs[i] if i < len(line_hs) else th
            shadow_draw.text((tx, gy + dy_off), line, font=font, fill=(0, 0, 0, 230))
            ty_s += css_line_h + css_gap if css_cover_mode else th + (gap_line if i + 1 < len(lines) else 0)

        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur_rad))
        overlay.alpha_composite(shadow_layer)

    for i, line in enumerate(lines):
        tx, gy, th = _line_xy(line, ty)
        if not css_cover_mode:
            th = line_hs[i] if i < len(line_hs) else th
        if stroke_on and thick:
            for dx, dy in outline_thick:
                draw.text((tx + dx, gy + dy), line, font=font, fill=(0, 0, 0, 250))
        draw.text((tx, gy), line, font=font, fill=fill)
        ty += css_line_h + css_gap if css_cover_mode else th + (gap_line if i + 1 < len(lines) else 0)
    rgba = np.asarray(overlay)
    return rgba, x0 - m, y0 - m


def _blit_overlay(frame_bgr: Any, overlay: tuple[Any, int, int], opacity: float = 1.0) -> Any:
    import numpy as np

    rgba, x0, y0 = overlay
    h, w = frame_bgr.shape[:2]
    bh, bw = rgba.shape[:2]
    x1, y1 = min(w, x0 + bw), min(h, y0 + bh)
    if x1 <= max(0, x0) or y1 <= max(0, y0):
        return frame_bgr
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    patch = rgba[sy0 : sy0 + (y1 - dy0), sx0 : sx0 + (x1 - dx0)]
    if patch.size == 0:
        return frame_bgr
    a = patch[:, :, 3:4].astype(np.float32) * (max(0.0, min(1.0, opacity)) / 255.0)
    # PIL RGB → BGR
    src = patch[:, :, 2::-1].astype(np.float32)
    roi = frame_bgr[dy0:y1, dx0:x1]
    frame_bgr[dy0:y1, dx0:x1] = (src * a + roi.astype(np.float32) * (1.0 - a)).astype(
        np.uint8
    )
    return frame_bgr


def _image_overlay(path: str, box: tuple[int, int, int, int]) -> tuple[Any, int, int] | None:
    import numpy as np
    from PIL import Image
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return None
    try:
        image = Image.open(path).convert("RGBA")
        image.thumbnail((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    except Exception:
        return None
    canvas = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2))
    return np.asarray(canvas), x0, y0
