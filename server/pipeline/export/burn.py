"""Cover hardsubs + burn translated captions."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..asr import _rapidocr_gpu_kwargs
from ..core.jobs import _job_procs, check_cancel
from ..core.media import h264_encoder_args, video_size
from ..core.project import ensure_layout, set_status
from ..core.resources import adaptive_workers
from ..ocr.extract import _ocr_join_lines, _rapidocr_labels
from ..ocr.labels import (
    clamp_label_box,
    cover_fit_label,
    is_tall_label,
    is_vertical_cjk_source,
    layout_label_caption,
    pick_label_box,
)
from ..ocr.locate import (
    ocr_mid_hardsub_boxes,
    ocr_mid_labels,
    ocr_mid_vertical,
)
from ..translate import _clean_burn_text

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


# Vietnamese probe — precomposed + horned vowels; reject fonts that draw □ tofu.
_VI_PROBE = "ĂÂĐÊÔƠƯăâđêôơưáàảãạắằấầếềốồớờứừýỳệỗộỹỐồủỹ"
_REF_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _glyph_ink(font: Any, ch: str, size: int = 48) -> int:
    from PIL import Image, ImageDraw

    img = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(img).text((4, 4), ch, font=font, fill=255)
    return int(sum(img.getdata()))


def _ref_ink_map(sample: str, size: int = 48) -> dict[str, int]:
    from PIL import ImageFont

    need = {c for c in sample if not c.isspace()}
    for p in _REF_FONTS:
        if not Path(p).exists():
            continue
        try:
            font = ImageFont.truetype(p, size)
        except OSError:
            continue
        m = {ch: _glyph_ink(font, ch, size) for ch in need}
        if all(v > 200 for v in m.values()):
            return m
    return {}


def _font_covers_text(path: str, sample: str, size: int = 48) -> bool:
    """True if font draws sample glyphs (not empty / not tofu □)."""
    try:
        from PIL import ImageFont
    except Exception:
        return Path(path).exists()
    try:
        font = ImageFont.truetype(path, size)
    except OSError:
        return False
    need = {c for c in sample if not c.isspace()}
    if not need:
        return True
    ref = _ref_ink_map(sample, size)
    # tofu box ink is nearly constant across missing VI glyphs
    inks: list[int] = []
    for ch in need:
        ink = _glyph_ink(font, ch, size)
        inks.append(ink)
        if ink < 30:
            return False
        if hasattr(font, "getbbox"):
            bb = font.getbbox(ch)
            if not bb or bb[2] <= bb[0]:
                return False
        if ref:
            r = ref.get(ch, 0)
            if r > 200 and ink < max(80, int(r * 0.35)):
                return False
    if len(inks) >= 6:
        # many missing glyphs → identical square tofu ink
        uniq = {round(v / 500) for v in inks if v > 0}
        if len(uniq) <= 2 and min(inks) > 1000:
            # if almost all glyphs share ~same ink, likely tofu fallback
            lo, hi = min(inks), max(inks)
            if hi > 0 and (hi - lo) / hi < 0.08 and not ref:
                return False
    return True


_font_cache: dict[str, str] = {}


def _pick_font(candidates: tuple[str, ...], *, sample: str = _VI_PROBE, cache_key: str = "") -> str:
    key = cache_key or sample
    hit = _font_cache.get(key)
    if hit and Path(hit).exists():
        return hit
    for p in candidates:
        if Path(p).exists() and _font_covers_text(p, sample):
            _font_cache[key] = p
            return p
    for p in candidates:
        if Path(p).exists():
            _font_cache[key] = p
            return p
    return "Arial"


def _subtitle_font() -> str:
    return _pick_font(
        (
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ),
        cache_key="sub",
    )


def _subtitle_font_vertical() -> str:
    """Font đậm cho title dọc (VI/Latin); bỏ font thiếu dấu Việt (vd. Arial Rounded)."""
    return _pick_font(
        (
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
            "/System/Library/Fonts/Supplemental/Tahoma Bold.ttf",
            "/System/Library/Fonts/Supplemental/Trebuchet MS Bold.ttf",
            "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Avenir Next Condensed.ttc",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/SFNS.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ),
        cache_key="vert",
    )


def _blur_region(frame_bgr: Any, box: tuple[int, int, int, int]) -> Any:
    """Che kín hardsub: phủ màu nền + texture nhẹ (không giữ pixel chữ cũ)."""
    import cv2
    import numpy as np

    h, w = frame_bgr.shape[:2]
    x0, y0, x1, y1 = (
        max(0, box[0]),
        max(0, box[1]),
        min(w, box[2]),
        min(h, box[3]),
    )
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        return frame_bgr
    roi = frame_bgr[y0:y1, x0:x1]
    y_ref0 = max(0, y0 - max(20, bh))
    ref = frame_bgr[y_ref0:y0, x0:x1]
    if ref.size >= 30:
        med = np.median(ref.reshape(-1, 3), axis=0).astype(np.float32)
    else:
        med = np.median(roi.reshape(-1, 3), axis=0).astype(np.float32)
    # texture từ ROI đã pixelate (không còn nét chữ)
    sx, sy = max(2, bw // 20), max(2, bh // 14)
    tiny = cv2.resize(roi, (sx, sy), interpolation=cv2.INTER_AREA)
    tex = cv2.resize(tiny, (bw, bh), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    covered = (tex * 0.12 + med * 0.88).astype(np.uint8)
    ksz = max(11, (min(bw, bh) // 4) | 1)
    if ksz % 2 == 0:
        ksz += 1
    ksz = min(ksz, 21)
    covered = cv2.GaussianBlur(covered, (ksz, ksz), 0)
    # Chỉ hòa mép trên/dưới. Mép trái/phải phải được thay 100%, nếu không nét
    # hardsub nằm sát bbox OCR vẫn còn lộ ra sau khi đè bản dịch.
    feather = 2
    alpha = np.ones((bh, bw), np.float32)
    for i in range(feather):
        a = (i + 1) / (feather + 1)
        alpha[i, :] = np.minimum(alpha[i, :], a)
        alpha[-(i + 1), :] = np.minimum(alpha[-(i + 1), :], a)
    a3 = alpha[..., None]
    frame_bgr[y0:y1, x0:x1] = (
        covered.astype(np.float32) * a3 + roi.astype(np.float32) * (1.0 - a3)
    ).astype(np.uint8)
    return frame_bgr


def _parse_hex_color(hex_str: str, default: tuple[int, int, int] = (76, 29, 149)) -> tuple[int, int, int]:
    s = (hex_str or "").strip().lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            pass
    return default


def _feather_vertical_blend(
    frame_bgr: Any, roi: Any, covered: Any, y0: int, y1: int, x0: int, x1: int, feather: int = 2
) -> None:
    """Hòa mép trên/dưới vùng che — giữ mép trái/phải thay 100%."""
    import numpy as np

    bh, bw = roi.shape[:2]
    alpha = np.ones((bh, bw), np.float32)
    for i in range(feather):
        a = (i + 1) / (feather + 1)
        alpha[i, :] = np.minimum(alpha[i, :], a)
        alpha[-(i + 1), :] = np.minimum(alpha[-(i + 1), :], a)
    a3 = alpha[..., None]
    frame_bgr[y0:y1, x0:x1] = (
        covered.astype(np.float32) * a3 + roi.astype(np.float32) * (1.0 - a3)
    ).astype(np.uint8)


def _blur_tint_region(
    frame_bgr: Any,
    box: tuple[int, int, int, int],
    color_hex: str = "#4c1d95",
    opacity_pct: int = 40,
) -> Any:
    """Khớp preview: blur nền video + phủ màu (backdrop-blur + tint)."""
    import cv2
    import numpy as np

    h, w = frame_bgr.shape[:2]
    x0, y0, x1, y1 = (
        max(0, box[0]),
        max(0, box[1]),
        min(w, box[2]),
        min(h, box[3]),
    )
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        return frame_bgr
    roi = frame_bgr[y0:y1, x0:x1]
    # ponytail: 2 pass blur — che nét chữ hardsub, giữ vibe kính mờ như preview
    ksz = max(13, (min(bw, bh) // 4) | 1)
    if ksz % 2 == 0:
        ksz += 1
    ksz = min(ksz, 27)
    blurred = cv2.GaussianBlur(roi, (ksz, ksz), 0)
    k2 = max(9, (ksz * 2 // 3) | 1)
    if k2 % 2 == 0:
        k2 += 1
    blurred = cv2.GaussianBlur(blurred, (k2, k2), 0)
    r, g, b = _parse_hex_color(color_hex)
    tint_bgr = np.array([b, g, r], dtype=np.float32)
    alpha = float(np.clip(opacity_pct / 100.0, 0.05, 1.0))
    covered = (blurred.astype(np.float32) * (1.0 - alpha) + tint_bgr * alpha).astype(np.uint8)
    # Thay 100% ROI — mép trái/phải không blend lại pixel chữ gốc sắc
    frame_bgr[y0:y1, x0:x1] = covered
    return frame_bgr


def _solid_region(
    frame_bgr: Any,
    box: tuple[int, int, int, int],
    color_hex: str = "#000000",
    opacity_pct: int = 75,
) -> Any:
    import numpy as np

    h, w = frame_bgr.shape[:2]
    x0, y0, x1, y1 = (
        max(0, box[0]),
        max(0, box[1]),
        min(w, box[2]),
        min(h, box[3]),
    )
    bw, bh = x1 - x0, y1 - y0
    if bw < 4 or bh < 4:
        return frame_bgr
    roi = frame_bgr[y0:y1, x0:x1]
    r, g, b = _parse_hex_color(color_hex, (0, 0, 0))
    tint_bgr = np.array([b, g, r], dtype=np.float32)
    alpha = float(np.clip(opacity_pct / 100.0, 0.0, 1.0))
    covered = (roi.astype(np.float32) * (1.0 - alpha) + tint_bgr * alpha).astype(np.uint8)
    _feather_vertical_blend(frame_bgr, roi, covered, y0, y1, x0, x1)
    return frame_bgr


def _apply_cover_mask(
    frame_bgr: Any,
    box: tuple[int, int, int, int],
    *,
    style: str = "blur",
    color_hex: str = "#4c1d95",
    opacity_pct: int = 40,
) -> Any:
    st = (style or "blur").lower()
    if st == "solid":
        return _solid_region(frame_bgr, box, color_hex, opacity_pct)
    if st == "mosaic":
        # ponytail: "Khối" = _blur_region cũ (median + pixelate + gaussian) — che hardsub thật
        return _blur_region(frame_bgr, box)
    return _blur_tint_region(frame_bgr, box, color_hex, opacity_pct)


def _cover_max_h(frame_h: int, font_size: int = 36) -> int:
    """Đủ 1–3 dòng phụ đề — theo font, không kẹp quá thấp."""
    one = int(round(font_size * 1.45 + 10))
    cap = int(round(font_size * 3.4 + 16))
    by_frame = int(round(frame_h * 0.065))
    return max(one, min(cap, by_frame))


def _preview_cover_pad(font_size: int, frame_w: int) -> tuple[int, int, int]:
    """Khớp LivePreviewEditor.coverPad — sát trên, dư đáy che stroke."""
    pad_x = max(8, int(round(frame_w * 0.012)))
    pad_top = max(2, int(round(font_size * 0.04)))
    pad_bot = max(18, int(round(font_size * 0.55)))
    return pad_x, pad_top, pad_bot


_COVER_SHADOW_BOT = 4


def _cover_bleed_x(content_w: int, frame_w: int = 1080) -> int:
    # Bleed vừa đủ stroke — không nới xa (khớp LivePreviewEditor)
    return max(6, int(round(content_w * 0.028)), int(round(frame_w * 0.006)))


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
            src_fs = max(font_size, int(round(sh * 0.78)))
            font = ImageFont.truetype(_subtitle_font(), src_fs)
            src_w = int(math.ceil(draw.textbbox((0, 0), src, font=font)[2] * 1.08))
        except OSError:
            pass
    old_w = max(sw, _cover_box_width(src_w, frame_w) if src_w > 0 else 0)
    w = min(frame_w, max(old_w, auto_w))
    cx = (sx0 + sx1) / 2.0
    top_slack = int(round(sh * 0.28))
    y0 = max(0, sy0 + top_slack - pad_top)
    bot_extra = max(pad_bot, int(round(sh * 0.55)), int(round(font_size * 0.85)))
    y1 = min(frame_h, sy1 + bot_extra)
    x0 = max(0, int(round(cx - w / 2)))
    x1 = min(frame_w, x0 + w)
    return x0, y0, x1, max(y0 + 12, y1)


def _layout_caption_over(
    text: str,
    font_size: int,
    ocr_box: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    source_text: str = "",
) -> tuple[dict[str, Any], tuple[int, int, int, int]]:
    """Layout over khớp preview — trả (caption lay, cover box)."""
    from PIL import Image, ImageDraw, ImageFont

    pad_x, pad_top, pad_bot = _preview_cover_pad(font_size, frame_w)
    ox0, oy0, ox1, oy1 = ocr_box
    ocr_w, ocr_h = ox1 - ox0, oy1 - oy0
    cx = (ox0 + ox1) // 2
    font_path = _subtitle_font()
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        font = ImageFont.load_default()
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)

    trimmed = (text or "").strip()
    one_w = draw.textbbox((0, 0), trimmed, font=font)[2] if trimmed else 0
    max_inner = max(24, frame_w - pad_x * 4)
    if not trimmed:
        lines = [""]
    elif one_w <= int(max_inner * 1.05):
        lines = [trimmed]
    else:
        lines = _wrap_text(draw, trimmed, font, max_inner)
        if len(lines) > 3:
            lines = _merge_to_n_lines(lines, 3)

    line_boxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    gap_line = max(2, font_size // 8)
    text_w = max((b[2] - b[0]) for b in line_boxes) if line_boxes else one_w
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    line_h = (max(line_hs) if line_hs else font_size) + gap_line
    text_block_h = int(math.ceil(len(lines) * font_size * 1.12 + 4))

    src_w = 0
    src = (source_text or "").strip()
    if src:
        src_fs = max(font_size, int(round(ocr_h * 0.72)))
        try:
            src_font = ImageFont.truetype(font_path, src_fs)
            # +16% khớp preview — outline CJK dày hơn đo font
            src_w = int(math.ceil(draw.textbbox((0, 0), src, font=src_font)[2] * 1.12))
        except OSError:
            pass

    orig_w = max(src_w, ocr_w) if src else ocr_w
    content_w = max(orig_w, text_w)
    cap_pad_x = 2
    cap_w = int(text_w + cap_pad_x * 2)
    auto_w = min(frame_w, max(_fit_cover_width(content_w, cap_w, frame_w), cap_w))
    cover_box = _fit_hardsub_box(
        (ox0, oy0, ox1, oy1), auto_w, font_size, frame_w, frame_h, src
    )
    cover_x0, cover_y0, cover_x1, cover_y1 = cover_box
    cover_w = cover_x1 - cover_x0
    cover_h = cover_y1 - cover_y0

    cap_w = int(text_w + cap_pad_x * 2)
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
) -> dict[str, Any] | None:
    """Layout caption gửi từ preview — không tính lại trên server."""
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
        fs = max(16, min(120, int(cl.get("fontSize") or default_fs)))
    except (KeyError, TypeError, ValueError):
        return None
    if bw <= 0 or bh <= 0:
        return None
    lines = [str(ln) for ln in lines_raw if str(ln).strip() or len(lines_raw) == 1]
    if not lines:
        return None
    from PIL import Image, ImageDraw

    font = font_getter(fs)
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)
    line_boxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    gap_line = max(2, fs // 8)
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    line_h = (max(line_hs) if line_hs else fs) + gap_line
    return {
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
        "vertical": False,
    }


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
    max_w = int(round(frame_w * 0.72))
    if (x1 - x0) > max_w:
        cx = (x0 + x1) // 2
        x0, x1 = cx - max_w // 2, cx + max_w // 2
    return (
        max(0, x0),
        max(0, y0),
        min(frame_w, x1),
        min(frame_h, y1),
    )


def _blur_hardsubs(
    frame_bgr: Any, boxes: list[tuple[int, int, int, int]]
) -> Any:
    """Che hardsub sát chữ: blur ROI hẹp (không quét nửa khung)."""
    h, w = frame_bgr.shape[:2]
    box = _tight_cover_box(frame_bgr, boxes if boxes else None)
    if box is None:
        return frame_bgr
    return _blur_region(frame_bgr, box)


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
    ink = _cover_box_from_ink(frame_bgr, None, tight=True)
    if ink is None:
        return None
    x0, y0, x1, y1 = ink
    bw, bh = x1 - x0, y1 - y0
    if bh > max_h or bw > int(w * 0.80) or bw < int(w * 0.10):
        return None
    return (max(0, x0), max(0, y0), min(w, x1), min(h, y1))


def _paint_caption(frame_bgr: Any, layout: dict[str, Any]) -> Any:
    """Vẽ chữ trắng + viền — không plate/nền đen."""
    overlay = _caption_overlay(layout)
    if overlay is None:
        return frame_bgr
    return _blit_overlay(frame_bgr, overlay)


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
    m = 6
    bw, bh = (x1 - x0) + 2 * m, (y1 - y0) + 2 * m
    if bw < 4 or bh < 4:
        return None
    overlay = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    box_w = x1 - x0
    box_h = y1 - y0
    # vertical distribute: giãn đều theo full box_h; horizontal: căn giữa khối
    if layout.get("vertical") and layout.get("distribute") and lines:
        ty = m + max(0, pad_y)
    else:
        ty = m + max(0, (box_h - text_h) // 2)
    thick = bool(layout.get("vertical") or layout.get("label"))
    outline = (
        (
            (-3, 0),
            (3, 0),
            (0, -3),
            (0, 3),
            (-2, -2),
            (2, -2),
            (-2, 2),
            (2, 2),
            (-3, -1),
            (3, -1),
            (-3, 1),
            (3, 1),
            (-1, -3),
            (1, -3),
            (-1, 3),
            (1, 3),
            (-2, -3),
            (2, -3),
            (-2, 3),
            (2, 3),
        )
        if thick
        else (
            (-2, 0),
            (2, 0),
            (0, -2),
            (0, 2),
            (-2, -1),
            (2, -1),
            (-2, 1),
            (2, 1),
            (-1, -2),
            (1, -2),
            (-1, 2),
            (1, 2),
        )
    )
    for i, line in enumerate(lines):
        bb = draw.textbbox((0, 0), line, font=font)
        tw = bb[2] - bb[0]
        th = line_hs[i] if i < len(line_hs) else (bb[3] - bb[1])
        tx = m + (box_w - tw) // 2
        # bù top bearing của font (textbbox top thường > 0) → căn giữa đúng nét.
        top = bb[1]
        gy = ty - top
        for dx, dy in outline:
            draw.text((tx + dx, gy + dy), line, font=font, fill=(0, 0, 0, 250))
        draw.text((tx, gy), line, font=font, fill=(255, 255, 255, 255))
        ty += th + (gap_line if i + 1 < len(lines) else 0)
    rgba = np.asarray(overlay)
    return rgba, x0 - m, y0 - m


def _blit_overlay(frame_bgr: Any, overlay: tuple[Any, int, int]) -> Any:
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
    a = patch[:, :, 3:4].astype(np.float32) * (1.0 / 255.0)
    # PIL RGB → BGR
    src = patch[:, :, 2::-1].astype(np.float32)
    roi = frame_bgr[dy0:y1, dx0:x1]
    frame_bgr[dy0:y1, dx0:x1] = (src * a + roi.astype(np.float32) * (1.0 - a)).astype(
        np.uint8
    )
    return frame_bgr


def _is_corner_ui_box(
    box: tuple[int, int, int, int], fw: int, fh: int
) -> bool:
    """Logo / watermark góc (vd. '12' trái) — không gộp vào dải hardsub."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return True
    cx = (x0 + x1) * 0.5
    # Góc trái/phải + hẹp (không phải dòng phụ đề giữa khung).
    edge = fw * 0.22
    if cx <= edge or cx >= fw - edge:
        if bw <= fw * 0.28:
            return True
        # logo gần vuông / cao so với rộng
        if bh >= bw * 0.55 and bw <= fw * 0.40:
            return True
    return False


def _filter_subtitle_boxes(
    boxes: list[tuple[int, int, int, int]], fw: int, fh: int
) -> list[tuple[int, int, int, int]]:
    """Bỏ UI góc; giữ dải phụ đề giữa."""
    if not boxes:
        return []
    kept = [b for b in boxes if not _is_corner_ui_box(b, fw, fh)]
    return kept or boxes


def _merge_ocr_samples(
    samples: list[tuple[int, int, int, int]], fw: int, fh: int
) -> list[tuple[int, int, int, int]]:
    """Gộp bbox OCR các mốc → 1 dải hardsub (bỏ logo góc)."""
    if not samples:
        return []

    def _scy(b: tuple[int, int, int, int]) -> float:
        return (b[1] + b[3]) * 0.5

    samples = _filter_subtitle_boxes(samples, fw, fh)
    thr = fh * (0.62 if fh > fw else 0.72)
    low = [
        b for b in samples if _scy(b) >= thr and (b[2] - b[0]) <= int(fw * 0.95)
    ]
    if not low:
        thr = fh * (0.52 if fh > fw else 0.62)
        low = [
            b for b in samples if _scy(b) >= thr and (b[2] - b[0]) <= int(fw * 0.95)
        ]
    pool = low or [b for b in samples if (b[2] - b[0]) <= int(fw * 0.95)] or samples
    pool = _filter_subtitle_boxes(pool, fw, fh)
    # Ưu tiên cụm giữa (phụ đề); không union với logo góc xa.
    def _sub_score(b: tuple[int, int, int, int]) -> float:
        bw = b[2] - b[0]
        cx = (b[0] + b[2]) * 0.5
        center = 1.0 - abs(cx / max(1, fw) - 0.5) * 1.4
        return float(bw) * max(0.15, center) + (_scy(b) / max(1, fh)) * fw * 0.35

    best = max(pool, key=_sub_score)
    bcx = (best[0] + best[2]) * 0.5
    bcy = _scy(best)
    near = [
        b
        for b in pool
        if abs(_scy(b) - bcy) <= fh * 0.09
        and abs((b[0] + b[2]) * 0.5 - bcx) <= fw * 0.42
        and not _is_corner_ui_box(b, fw, fh)
    ] or [best]
    x0 = min(b[0] for b in near)
    x1 = max(b[2] for b in near)
    y0 = min(b[1] for b in near)
    y1 = max(b[3] for b in near)
    cy = (y0 + y1) // 2
    max_h = max(40, int(fh * 0.10))
    if (y1 - y0) > max_h:
        y0, y1 = cy - max_h // 2, cy + max_h // 2  # cắt đều 2 phía
    pad_x = max(12, int(round(fw * 0.02)))
    pad_y = max(4, int(round(fh * 0.003)))  # trên = dưới
    x0, y0, x1, y1 = x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y
    return [(max(0, x0), max(0, y0), min(fw, x1), min(fh, y1))]


def _ocr_cue_boxes(
    video: Path,
    cue: tuple[float, float, float, float, str, str] | tuple[float, float, float, float, str, str, str],
    ocr: Any,
    fw: int,
    fh: int,
) -> list[tuple[int, int, int, int]]:
    """OCR 5 mốc 1 câu — mỗi worker mở VideoCapture riêng (thread-safe)."""
    import cv2

    c_start, c_end = cue[0], cue[1]
    source = cue[5] if len(cue) > 5 else ""
    layout = cue[6] if len(cue) > 6 else "horizontal"
    cap = cv2.VideoCapture(str(video))
    samples: list[tuple[int, int, int, int]] = []
    label_cands: list[tuple[tuple[int, int, int, int], str]] = []
    try:
        for frac in (0.08, 0.28, 0.50, 0.72, 0.92):
            mid = c_start + (c_end - c_start) * frac
            cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
            ok, frame = cap.read()
            if not ok:
                continue
            if layout == "vertical":
                # Quét giữa khung (tiêu đề dọc) thay vì dải đáy
                b, _tx = _ocr_mid_vertical(frame, ocr, source=source)
            elif layout == "label":
                b, tx = _ocr_mid_labels(frame, ocr, source=source)
                for box in b:
                    label_cands.append((box, tx or source or ""))
                if b:
                    # gộp cột dọc + union — che hết stroke CJK
                    from ..ocr.labels import expand_label_column, union_boxes

                    b = expand_label_column(b, fw, fh)
                    if len(b) >= 2:
                        u = union_boxes(b)
                        if u:
                            samples.append(clamp_label_box(u, fw, fh))
                    else:
                        pick = pick_label_box(b, [tx or ""] * len(b), source, fw, fh)
                        if pick:
                            samples.append(pick)
                continue
            else:
                # hardsub đáy trước; chỉ fallback mid nếu short CJK (行) không thấy ở đáy
                src = source or ""
                src_cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
                short_cjk = 0 < src_cjk <= 2 and len(src.strip()) <= 4
                if short_cjk:
                    # Chữ flash ngắn thường nằm giữa khung; OCR dải đáy có thể
                    # bắt nhầm chi tiết nền rồi đặt cover sai hẳn vị trí.
                    b, _tx = _ocr_mid_hardsub_boxes(frame, ocr, source=src)
                else:
                    b, _tx = _ocr_band_subs(frame, ocr)
            u = _union_box(b) if b else None
            if u is None and layout not in ("vertical", "label"):
                u = _cover_box_from_ink(frame, None, tight=True)
            if u:
                # chỉ kẹp ô nhỏ khi box thật sự giữa khung (pop-up 行), không đụng hardsub đáy
                if layout == "horizontal" and source:
                    sc = sum(1 for c in source if "\u4e00" <= c <= "\u9fff")
                    cy = (u[1] + u[3]) * 0.5
                    if sc <= 2 and len(source.strip()) <= 4 and cy < fh * 0.70:
                        u = clamp_label_box(u, fw, fh)
                samples.append(u)
    finally:
        cap.release()
    if layout == "vertical" and samples:
        # 1 box dọc — union rồi clamp tỷ lệ cao/hẹp
        u = _union_box(samples)
        if u:
            x0, y0, x1, y1 = u
            bw, bh = x1 - x0, y1 - y0
            if bw > bh * 0.85:
                # OCR ngang nhầm — thu hẹp về cột giữa
                cx = (x0 + x1) // 2
                half = max(20, min(bw // 4, int(fw * 0.08)))
                x0, x1 = cx - half, cx + half
            return [(max(0, x0), max(0, y0), min(fw, x1), min(fh, y1))]
        return []
    if layout == "label":
        from ..ocr.labels import expand_label_column

        # nhiều nhãn nguyên liệu: GIỮ từng box (không union 1 khối to)
        if label_cands:
            boxes = [c[0] for c in label_cands]
            boxes = expand_label_column(boxes, fw, fh)
            # dedupe gần trùng
            uniq: list[tuple[int, int, int, int]] = []
            for b in boxes:
                if any(
                    abs(b[0] - u[0]) < 12
                    and abs(b[1] - u[1]) < 12
                    and abs(b[2] - u[2]) < 12
                    and abs(b[3] - u[3]) < 12
                    for u in uniq
                ):
                    continue
                uniq.append(clamp_label_box(b, fw, fh))
            if uniq:
                # tối đa 6 box; bỏ box quá to (noise)
                uniq = [
                    b
                    for b in uniq
                    if (b[2] - b[0]) <= fw * 0.85 and (b[3] - b[1]) <= fh * 0.40
                ][:6]
                if uniq:
                    return uniq
        if samples:
            # 1 sample / frame đã clamp — lấy median size
            samples.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            mid = samples[len(samples) // 2]
            return [clamp_label_box(mid, fw, fh)]
        return [
            clamp_label_box(
                (int(fw * 0.42), int(fh * 0.45), int(fw * 0.58), int(fh * 0.55)),
                fw,
                fh,
            )
        ]
    return _merge_ocr_samples(samples, fw, fh)


def _resolve_workers(requested: int | None, *, cap: int = 16, n: int | None = None) -> int:
    """1–cap theo setting; 0 tự điều chỉnh theo tài nguyên đang rảnh."""
    return adaptive_workers(requested, kind="cpu", cap=cap, tasks=n)


def _precompute_cue_boxes(
    video: Path,
    cues: list[tuple[float, float, float, float, str, str]],
    ocr: Any,
    project_id: str | None = None,
    workers: int = 0,
) -> list[list[tuple[int, int, int, int]]]:
    """OCR song song theo nhóm câu; fallback mực nếu miss."""
    import cv2

    cache_path: Path | None = None
    if project_id:
        stat = video.stat()
        cue_sig = [
            (
                round(c[0], 3),
                round(c[1], 3),
                c[5],
                c[6] if len(c) > 6 else "horizontal",
            )
            for c in cues
        ]
        # v25: source dài không nhận box nhiễu chỉ trùng một glyph.
        raw_key = json.dumps(
            ["ocr_boxes_v25", str(video.resolve()), stat.st_size, stat.st_mtime_ns, cue_sig],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        key = hashlib.sha1(raw_key.encode()).hexdigest()[:16]
        cache_path = ensure_layout(project_id) / "cache" / f"ocr_boxes_{key}.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, list) and len(cached) == len(cues):
                    if project_id:
                        set_status(
                            project_id,
                            step="export",
                            progress=20,
                            message=f"Dùng cache định vị {len(cues)} câu",
                            running=True,
                        )
                    return [
                        [tuple(int(v) for v in box) for box in boxes]
                        for boxes in cached
                    ]
            except (OSError, ValueError, TypeError):
                cache_path.unlink(missing_ok=True)

    probe = cv2.VideoCapture(str(video))
    try:
        fh = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
        fw = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
    finally:
        probe.release()

    n = len(cues)
    out: list[list[tuple[int, int, int, int]]] = [[] for _ in range(n)]
    if n == 0:
        return out

    # Nhóm câu song song — mỗi worker 1 VideoCapture + OCR.
    workers = _resolve_workers(workers, n=n)
    done = 0

    def _job(i: int) -> tuple[int, list[tuple[int, int, int, int]]]:
        check_cancel(project_id)
        return i, _ocr_cue_boxes(video, cues[i], ocr, fw, fh)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocr") as pool:
        # map theo nhóm để cập nhật progress giữa chừng
        chunk = max(1, workers * 2)
        for start in range(0, n, chunk):
            check_cancel(project_id)
            idxs = list(range(start, min(n, start + chunk)))
            for i, boxes in pool.map(_job, idxs):
                out[i] = boxes
            done = min(n, start + chunk)
            if project_id:
                set_status(
                    project_id,
                    step="export",
                    progress=12 + int(8 * done / max(1, n)),
                    message=f"Định vị chữ {done}/{n} ({workers} luồng)",
                    running=True,
                )

    if cache_path is not None:
        cache_path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


def _fill_hardsub_flat(
    frame_bgr: Any, box: tuple[int, int, int, int]
) -> Any:
    """DEPRECATED — dùng _blur_hardsubs. Giữ stub tránh import cũ."""
    return _blur_hardsubs(frame_bgr, [box])


def _erase_hardsubs(frame_bgr: Any, boxes: list[tuple[int, int, int, int]]) -> Any:
    """Cover-only: blur CapCut (không inpaint smear)."""
    return _blur_hardsubs(frame_bgr, boxes)


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


def _ocr_norm(s: str) -> str:
    return "".join((s or "").lower().split())


def _ocr_matches_subtitle(ocr_text: str, source: str) -> bool:
    """Chỉ giữ box thuộc câu phụ đề đang dịch — bỏ logo/UI."""
    a, b = _ocr_norm(ocr_text), _ocr_norm(source)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / max(len(sa), len(sb)) >= 0.55


def _ocr_subtitle_boxes(
    frame_bgr: Any,
    ocr: Any,
    source: str,
    *,
    pad_x: int = 22,
    pad_y: int = 10,
) -> list[tuple[int, int, int, int]]:
    """OCR → bbox phụ đề; pad ngang đủ để không sót nét 2 bên."""
    import cv2

    h, w = frame_bgr.shape[:2]
    scale = 1.0
    y0 = int(h * (0.55 if h > w else 0.65))
    band = frame_bgr[y0:h, :]
    bh, bw = band.shape[:2]
    if max(bh, bw) > 960:
        scale = 960 / max(bh, bw)
        img = cv2.resize(band, (int(bw * scale), int(bh * scale)))
    else:
        img = band
    result, _ = ocr(img)

    def boxes_from(rows, match: bool) -> list[tuple[int, int, int, int]]:
        out: list[tuple[int, int, int, int]] = []
        for row in rows or []:
            pts = row[0]
            text = (row[1] or "").strip()
            if len(text) < 2:
                continue
            if match and source.strip() and not _ocr_matches_subtitle(text, source):
                continue
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            bx0 = max(0, int(min(xs) / scale) - pad_x)
            by0 = max(0, int(min(ys) / scale) + y0 - pad_y)
            bx1 = min(w, int(max(xs) / scale) + pad_x)
            by1 = min(h, int(max(ys) / scale) + y0 + pad_y)
            if bx1 - bx0 >= 6 and by1 - by0 >= 6:
                out.append((bx0, by0, bx1, by1))
        return out

    boxes = boxes_from(result, match=True)
    if not boxes and source.strip():
        boxes = boxes_from(result, match=False)
    boxes = _merge_boxes(boxes, gap=48)  # cùng dòng: gộp hết chiều ngang
    return _widen_boxes_to_ink(frame_bgr, boxes)


def _row_ink_mask(gray: Any) -> Any:
    """Mask mực phụ đề (trắng/sáng + viền tối) — nới ngưỡng cho film xám."""
    import cv2
    import numpy as np

    k = np.ones((3, 3), np.uint8)
    # Ngưỡng cố định + tương đối (phim đen trắng / hardsub mờ).
    p90 = float(np.percentile(gray, 90)) if gray.size else 200.0
    p10 = float(np.percentile(gray, 10)) if gray.size else 40.0
    white_t = max(150.0, min(200.0, p90 - 8.0))
    dark_t = min(90.0, max(40.0, p10 + 12.0))
    white = (gray > white_t).astype(np.uint8)
    dark = (gray < dark_t).astype(np.uint8)
    ink = (white & cv2.dilate(dark, k, iterations=2)) | (
        dark & cv2.dilate(white, k, iterations=2)
    )
    # Cũng giữ vùng rất sáng (hardsub trắng không có viền rõ).
    bright = (gray > max(185.0, white_t)).astype(np.uint8)
    ink = np.maximum(ink, bright)
    return cv2.dilate(ink, k, iterations=2)


def _widen_boxes_to_ink(
    frame_bgr: Any, boxes: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Nới bbox theo mực trên cùng hàng — full chiều ngang hardsub."""
    import cv2
    import numpy as np

    if not boxes:
        return []
    h, w = frame_bgr.shape[:2]
    out: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in boxes:
        # Quét gần full ngang trên dải Y của dòng phụ đề.
        ax0, ax1 = 0, w
        ay0 = max(0, y0 - 8)
        ay1 = min(h, y1 + 8)
        roi = frame_bgr[ay0:ay1, ax0:ax1]
        if roi.size == 0:
            out.append((x0, y0, x1, y1))
            continue
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        ink = _row_ink_mask(gray)
        # Cột có mực: nối đoạn giao OCR (tránh logo xa 2 bên).
        col = ink.max(axis=0) > 0
        if not np.any(col):
            out.append((x0, y0, x1, y1))
            continue
        cx0 = max(0, x0 - ax0)
        cx1 = min(w - 1, max(cx0 + 1, x1 - ax0))
        # Nới trái/phải từ đoạn OCR; khe nhỏ OK, không nhảy qua khoảng trống lớn
        # (tránh nối hardsub giữa với logo góc "12").
        left, right = cx0, cx1
        gap = max(4, w // 120)
        edge_stop = max(8, int(w * 0.06))
        i = cx0 - 1
        miss = 0
        while i >= edge_stop:
            if col[i]:
                left = i
                miss = 0
            else:
                miss += 1
                if miss > gap:
                    break
            i -= 1
        i = cx1
        miss = 0
        while i < w - edge_stop:
            if col[i]:
                right = i
                miss = 0
            else:
                miss += 1
                if miss > gap:
                    break
            i += 1
        ys, xs = np.where(ink[:, left : right + 1] > 0)
        if len(xs) < 8:
            out.append((x0, y0, x1, y1))
            continue
        nx0 = max(0, left + ax0 - 10)
        nx1 = min(w, right + ax0 + 11)
        # Không kéo cover vào dải mép (logo góc).
        margin = int(w * 0.04)
        if nx0 < margin and (x0 - nx0) > int(w * 0.12):
            nx0 = max(nx0, x0 - int(w * 0.04))
        if nx1 > w - margin and (nx1 - x1) > int(w * 0.12):
            nx1 = min(nx1, x1 + int(w * 0.04))
        ny0 = max(0, int(ys.min()) + ay0 - 4)
        ny1 = min(h, int(ys.max()) + ay0 + 4)
        out.append((min(x0, nx0), min(y0, ny0), max(x1, nx1), max(y1, ny1)))
    return _merge_boxes(out, gap=48)


def _merge_boxes(
    boxes: list[tuple[int, int, int, int]], *, gap: int = 24
) -> list[tuple[int, int, int, int]]:
    """Gộp box cùng hàng thành một dải ngang liên tục."""
    if not boxes:
        return []
    # nhóm theo overlap Y (cùng dòng phụ đề)
    items = sorted(boxes, key=lambda b: (b[1], b[0]))
    groups: list[list[list[int]]] = [[[*items[0]]]]
    for x0, y0, x1, y1 in items[1:]:
        placed = False
        for g in groups:
            gy0 = min(b[1] for b in g)
            gy1 = max(b[3] for b in g)
            # cùng hàng nếu overlap theo Y (nới gap)
            if not (y1 < gy0 - gap or y0 > gy1 + gap):
                g.append([x0, y0, x1, y1])
                placed = True
                break
        if not placed:
            groups.append([[x0, y0, x1, y1]])
    out: list[tuple[int, int, int, int]] = []
    for g in groups:
        out.append(
            (
                min(b[0] for b in g),
                min(b[1] for b in g),
                max(b[2] for b in g),
                max(b[3] for b in g),
            )
        )
    return out


def _union_box(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _segment_bbox_override(
    segment: dict[str, Any], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """User bbox wins over OCR — giữ đúng w/h preview (chỉ kẹp trong khung video)."""
    bbox = segment.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = int(round(float(bbox["x"])))
        y = int(round(float(bbox["y"])))
        bw = int(round(float(bbox["w"])))
        bh = int(round(float(bbox["h"])))
    except (KeyError, TypeError, ValueError):
        return None
    min_size = 12
    if bw < min_size or bh < min_size:
        return None
    x0 = max(0, min(x, width - min_size))
    y0 = max(0, min(y, height - min_size))
    x1 = min(width, x + bw)
    y1 = min(height, y + bh)
    if x1 - x0 < min_size:
        x1 = min(width, x0 + min_size)
    if y1 - y0 < min_size:
        y1 = min(height, y0 + min_size)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


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
    font_path = _subtitle_font()
    max_lines = 3
    est_3line = int(fontsize * 3.4) + 16
    size = fontsize
    font_use = font
    # over: wrap theo OCR + chữ thật (PIL), không phình full khung
    if ocr_box and place == "over":
        ocr_w = int(ocr_box[2] - ocr_box[0])
        target_w = max(24, ocr_w)
        max_box_h = max(est_3line, _cover_max_h(frame_h, fontsize))
    else:
        target_w = int(frame_w * 0.90)
        max_box_h = max(int(frame_h * 0.26), est_3line, 72)
    if not (ocr_box and place == "over"):
        max_box_h = min(max_box_h, int(frame_h * 0.32))
    lines = [text]
    for scale in (1.0, 0.94, 0.88, 0.82, 0.76, 0.70, 0.64, 0.58, 0.52, 0.46, 0.40):
        size = max(13, int(fontsize * scale))
        pad_x = max(4, size // 6)
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
    gap = max(6, size // 4)
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
        if len(words) == 1 and len(words[0]) > 10:
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
    if not units:
        return _layout_caption(text, font, fontsize, ocr_box, frame_w, frame_h)

    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)
    # Font display đậm; CJK fallback Unicode nếu rounded không vẽ được
    font_path = _subtitle_font_vertical() if not pure_cjk else _subtitle_font()
    # title dọc dài (CJK ≥4): full cột; text ngắn: bám OCR, không phình 42% khung
    n_units_est = len(re.sub(r"\s+", "", raw)) if pure_cjk else len(
        [w for w in re.split(r"[\s·・/|]+", raw) if w] or [raw]
    )
    compact = n_units_est <= 3 and not pure_cjk
    if ocr_box:
        ocr_w = max(28, ocr_box[2] - ocr_box[0])
        ocr_h = max(48, ocr_box[3] - ocr_box[1])
        target_h = min(frame_h, max(ocr_h + 24, int(frame_h * 0.12)))
        max_w = max(40, min(int(frame_w * 0.24), ocr_w + 24))
        base = max(22, min(int(fontsize * 1.15), int(ocr_w * (0.78 if pure_cjk else 0.62))))
    else:
        target_h = int(frame_h * (0.22 if compact else 0.55))
        max_w = int(frame_w * 0.18)
        base = max(22, min(int(fontsize * 1.1), int(frame_w * 0.09)))

    n = len(units)
    size = base
    font_use = font
    ch_ws: list[int] = []
    ch_hs: list[int] = []
    pad_x = pad_y = gap = 4
    for scale in (1.15, 1.05, 1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.52, 0.44):
        size = max(16, int(base * scale))
        try:
            font_use = ImageFont.truetype(font_path, size)
        except OSError:
            try:
                font_use = ImageFont.truetype(_subtitle_font(), size)
            except OSError:
                font_use = font
        pad_x = max(8, size // 4)
        # lề trên/dưới rộng hơn — tránh chữ dính mép bar
        pad_y = max(22, int(size * 0.72))
        boxes = [draw.textbbox((0, 0), u, font=font_use) for u in units]
        ch_ws = [max(1, b[2] - b[0]) for b in boxes]
        ch_hs = [max(1, b[3] - b[1]) for b in boxes]
        col_w = max(ch_ws) if ch_ws else size
        sum_h = sum(ch_hs)
        # gap tối thiểu; phần dư sẽ giãn đều (fill target_h)
        min_gap = max(4, size // 8)
        min_h = sum_h + min_gap * max(0, n - 1) + pad_y * 2
        box_w = col_w + pad_x * 2
        if box_w <= max_w and min_h <= target_h * 1.05:
            break

    col_w = max(ch_ws) if ch_ws else size
    sum_h = sum(ch_hs) if ch_hs else size * n
    # Giãn đều: box = full target_h (khung dài → chữ dài đẹp)
    box_h = min(frame_h, max(target_h, sum_h + pad_y * 2 + max(4, size // 8) * max(0, n - 1)))
    # giữ lề mép ≥ ~0.9× cỡ chữ (không để gap ăn hết pad)
    edge = max(pad_y, int(size * 0.9), int(box_h * 0.06))
    pad_y = edge
    inner = max(1, box_h - pad_y * 2)
    if n <= 1:
        gap = max(4, size // 8)
        text_h = sum_h
    else:
        # phân bố đều: gap = (inner - sum_h) / (n-1)
        gap = max(4, int(round((inner - sum_h) / (n - 1))))
        # nếu gap quá lớn (ít từ + cột rất dài) kẹp nhẹ để không loãng quá
        max_gap = max(size * 2, int(inner * 0.28))
        gap = min(gap, max_gap)
        text_h = sum_h + gap * (n - 1)
        # phần dư còn lại → thêm vào lề trên/dưới (căn giữa, vẫn xa mép)
        if text_h < inner:
            pad_y = max(pad_y, (box_h - text_h) // 2)
    box_w = min(frame_w, max(col_w + pad_x * 2, int(max_w * 0.85) if ocr_box else col_w + pad_x * 2))
    # căn giữa theo cột OCR
    if ocr_box:
        cx = (ocr_box[0] + ocr_box[2]) // 2
        # full cột: y0 bám mép trên OCR (không co về giữa)
        y0 = max(0, min(frame_h - box_h, ocr_box[1]))
        # nếu box ngắn hơn OCR → căn giữa trong cột
        if box_h < (ocr_box[3] - ocr_box[1]):
            cy = (ocr_box[1] + ocr_box[3]) // 2
            y0 = max(0, min(frame_h - box_h, cy - box_h // 2))
        else:
            # box ≈ full OCR height
            y0 = max(0, min(frame_h - box_h, ocr_box[1] - max(0, (box_h - (ocr_box[3] - ocr_box[1])) // 2)))
    else:
        cx = frame_w // 2
        y0 = max(0, min(frame_h - box_h, int(frame_h * 0.22)))
    x0 = max(0, min(frame_w - box_w, cx - box_w // 2))
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
        "distribute": True,  # overlay căn đều theo box_h
    }


def _hardsub_ink_mask(frame_bgr: Any, y0: int, y1: int) -> Any:
    """Mask nét phụ đề cứng (trắng + viền đen) trong [y0:y1]."""
    import cv2
    import numpy as np

    h, w = frame_bgr.shape[:2]
    y0, y1 = max(0, y0), min(h, y1)
    if y1 - y0 < 8:
        return np.zeros((h, w), np.uint8)
    roi = frame_bgr[y0:y1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    k = np.ones((3, 3), np.uint8)
    white = (gray > 185).astype(np.uint8)
    dark = (gray < 70).astype(np.uint8)
    ink = (white & cv2.dilate(dark, k, iterations=2)) | (
        dark & cv2.dilate(white, k, iterations=2)
    )
    # dilation rất nhẹ — chỉ nối ký tự cách xa trong cùng dòng phụ đề
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8), iterations=1)
    # lọc nghiêm ngặt: component phụ đề gọn, không quá cao (sọc áo) hay quá rộng (ngực)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    clean = np.zeros_like(ink)
    roi_h = y1 - y0
    max_w = int(w * 0.70)       # phụ đề rộng nhất ~70% khung, không full ngang
    max_h = int(roi_h * 0.12)  # phụ đề cao nhất ~12% band quét
    min_area = max(120, roi_h * w // 500)  # bỏ nhiễu nhỏ, chỉ giữ đám mực đủ lớn
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < min_area:
            continue
        if cw > max_w or ch > max_h:
            continue
        clean[labels == i] = 255
    full = np.zeros((h, w), np.uint8)
    full[y0:y1] = clean
    return full


def _cover_box_from_ink(
    frame_bgr: Any,
    hint_boxes: list[tuple[int, int, int, int]] | None = None,
    *,
    tight: bool = False,
) -> tuple[int, int, int, int] | None:
    """Bbox theo mực hardsub. tight=True: dải hẹp + pad nhỏ (tránh áo sọc)."""
    import numpy as np

    h, w = frame_bgr.shape[:2]
    if hint_boxes:
        pad = 8 if tight else 30
        hy0 = min(b[1] for b in hint_boxes) - pad
        hy1 = max(b[3] for b in hint_boxes) + pad
    else:
        # dải phụ đề hẹp tối đa — chỉ dưới cùng, bỏ sọc áo
        if h > w:
            hy0, hy1 = int(h * 0.82), int(h * 0.91)
        else:
            hy0, hy1 = int(h * 0.88), int(h * 0.93)
    mask = _hardsub_ink_mask(frame_bgr, hy0, hy1)
    ys, xs = np.where(mask > 0)
    edge = 4 if tight else 8
    if len(xs) < 40:
        if hint_boxes:
            return (
                max(0, min(b[0] for b in hint_boxes) - edge),
                max(0, min(b[1] for b in hint_boxes) - 4),
                min(w, max(b[2] for b in hint_boxes) + edge),
                min(h, max(b[3] for b in hint_boxes) + 4),
            )
        return None
    x0, x1 = int(xs.min()) - edge, int(xs.max()) + edge
    y0, y1 = int(ys.min()) - 4, int(ys.max()) + 4
    if hint_boxes:
        x0 = min(x0, min(b[0] for b in hint_boxes) - 4)
        x1 = max(x1, max(b[2] for b in hint_boxes) + 4)
        y0 = min(y0, min(b[1] for b in hint_boxes) - 2)
        y1 = max(y1, max(b[3] for b in hint_boxes) + 2)
    return (max(0, x0), max(0, y0), min(w, x1), min(h, y1))


def _ocr_band_subs(
    frame_bgr: Any, ocr: Any
) -> tuple[list[tuple[int, int, int, int]], str]:
    """Mọi bbox + text phụ đề ở dải dưới (không lọc theo source)."""
    import cv2

    h, w = frame_bgr.shape[:2]
    # dải rộng — hardsub portrait hay nằm ~0.65–0.85
    y0 = int(h * (0.48 if h > w else 0.55))
    band = frame_bgr[y0:h, :]
    # không downscale — dòng 2 hardsub hay mất khi resize
    try:
        eng = _rapidocr_labels()
    except Exception:
        eng = ocr
    result, _ = eng(band)
    boxes: list[tuple[int, int, int, int]] = []
    texts: list[str] = []
    pad_x, pad_y = 16, 4  # dọc đều, gọn — pad cover còn cộng thêm
    for row in result or []:
        pts = row[0]
        text = (row[1] or "").strip()
        # 1 CJK (行) là hardsub hợp lệ
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if not text or (cjk < 1 and len(text) < 2):
            continue
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        bx0 = max(0, int(min(xs)) - pad_x)
        by0 = max(0, int(min(ys)) + y0 - pad_y)
        bx1 = min(w, int(max(xs)) + pad_x)
        by1 = min(h, int(max(ys)) + y0 + pad_y)
        if bx1 - bx0 >= 6 and by1 - by0 >= 6:
            boxes.append((bx0, by0, bx1, by1))
            texts.append(text)
    # Bỏ logo góc trước khi nới mực (tránh nối "12" với hardsub).
    boxes = _filter_subtitle_boxes(boxes, w, h)
    boxes = _widen_boxes_to_ink(frame_bgr, boxes)
    boxes = _merge_boxes(boxes, gap=48)
    boxes = _filter_subtitle_boxes(boxes, w, h)
    # Hardsub nằm dải dưới. Bỏ text giữa khung (biển hiệu / UI) trước khi union.
    if boxes:
        def _cy(b: tuple[int, int, int, int]) -> float:
            return (b[1] + b[3]) * 0.5

        thr = h * (0.62 if h > w else 0.72)
        lower = [b for b in boxes if _cy(b) >= thr]
        if not lower:
            thr = h * (0.52 if h > w else 0.62)
            lower = [b for b in boxes if _cy(b) >= thr]
        pool = _filter_subtitle_boxes(lower or boxes, w, h)

        def _sub_score(b: tuple[int, int, int, int]) -> float:
            bw = b[2] - b[0]
            cx = (b[0] + b[2]) * 0.5
            center = 1.0 - abs(cx / max(1, w) - 0.5) * 1.4
            # Ưu tiên rộng + giữa + thấp (phụ đề), phạt logo góc / chữ giữa khung.
            return float(bw) * max(0.15, center) + (_cy(b) / max(1, h)) * w * 0.45

        best = max(pool, key=_sub_score)
        bcy = _cy(best)
        bcx = (best[0] + best[2]) * 0.5
        # Gộp dòng kề (hardsub 2 dòng); không gộp box góc xa.
        near = [
            b
            for b in pool
            if abs(_cy(b) - bcy) <= h * 0.09
            and abs((b[0] + b[2]) * 0.5 - bcx) <= w * 0.42
        ]
        u = _union_box(near) or best
        max_h = max(40, int(h * 0.10))
        # pad dọc đều (trên = dưới)
        x0, y0, x1, y1 = u[0] - 6, u[1] - 4, u[2] + 6, u[3] + 4
        if (y1 - y0) > max_h:
            cy = (u[1] + u[3]) // 2
            half = max_h // 2
            y0, y1 = cy - half, cy + half
        boxes = [(max(0, x0), max(0, y0), min(w, x1), min(h, y1))]
    return boxes, _ocr_join_lines(texts)


def _match_cue_index(
    cues: list[tuple[float, float, str, str]], t: float, ocr_text: str
) -> int:
    """Chọn câu dịch theo chữ OCR trên khung; fallback theo timeline."""
    best_i, best = -1, -1.0
    ot = _ocr_norm(ocr_text)
    for i, (s, e, _tr, src) in enumerate(cues):
        score = 0.0
        st = _ocr_norm(src)
        if ot and st:
            if ot == st or ot in st or st in ot:
                score += 3.0
            else:
                sa, sb = set(ot), set(st)
                score += 2.0 * len(sa & sb) / max(1, len(sa | sb))
        if s <= t < e:
            score += 0.4
        # gần về thời gian
        mid = (s + e) * 0.5
        score += max(0.0, 0.3 - abs(mid - t) * 0.05)
        if score > best:
            best, best_i = score, i
    if best >= 0.55:
        return best_i
    for i, (s, e, _tr, _src) in enumerate(cues):
        if s <= t < e:
            return i
    return -1


def _auto_subtitle_font_size(width: int, height: int) -> int:
    """Cỡ mặc định khi auto — ~36px, dễ đọc trên mọi độ phân giải."""
    _ = width, height  # ponytail: flat default; scale theo bbox ở _layout_caption nếu cần
    return 36


def _resolve_segment_font_size(
    seg: dict[str, Any],
    width: int,
    height: int,
    *,
    project_font_size: int,
    default_font_size: int,
    auto_fontsize: bool,
) -> int:
    """Per-segment override → project setting → auto/default."""
    seg_fs = int(seg.get("fontSize") or 0)
    if seg_fs > 0:
        return max(16, min(120, seg_fs))
    if not auto_fontsize:
        return default_font_size
    proj = int(project_font_size or 0)
    if proj > 0:
        return max(16, min(120, proj))
    return _auto_subtitle_font_size(width, height)


def cover_and_burn(
    video: Path,
    segments: list[dict[str, Any]],
    out: Path,
    *,
    cover: bool,
    burn: bool = True,
    subtitle_font_size: int = 0,
    project_id: str | None = None,
    workers: int = 0,
    caption_placement: str = "below",
    cover_mask_style: str = "blur",
    cover_mask_color: str = "#4c1d95",
    cover_mask_opacity: int = 40,
) -> Path:
    """cover = blur hardsub; burn = đè chữ dịch. placement: below|above khi không cover."""
    import cv2
    from PIL import ImageFont

    if not cover and not burn:
        shutil.copy2(video, out)
        return out

    w, h = video_size(video)
    auto_fontsize = int(subtitle_font_size or 0) <= 0
    fontsize = (
        _auto_subtitle_font_size(w, h)
        if auto_fontsize
        else max(16, min(120, int(subtitle_font_size)))
    )
    workers = _resolve_workers(workers)
    place = (caption_placement or "below").lower()
    if place not in ("below", "above"):
        place = "below"
    mask_style = (cover_mask_style or "blur").lower()
    if mask_style not in ("blur", "solid", "mosaic"):
        mask_style = "blur"
    mask_color = str(cover_mask_color or "#4c1d95")
    mask_opacity = max(0, min(100, int(cover_mask_opacity if cover_mask_opacity is not None else 40)))
    # cover → chữ đè đúng dải OCR; không cover → above/below hardsub
    layout_place = "over" if cover else place
    try:
        font = ImageFont.truetype(_subtitle_font(), fontsize)
    except OSError:
        font = ImageFont.load_default()

    # (cover_start, cover_end, burn_start, burn_end, text, source, layout)
    # Cover nới rộng hơn burn: hardsub hay hiện trước/sau ASR; burn vẫn bám timecode.
    cues: list[tuple[float, float, float, float, str, str, str]] = []
    cue_segment_ids: list[str] = []
    for seg in segments:
        raw = (seg.get("translation") or "").strip()
        source = (seg.get("source") or "").strip()
        burn_text = _clean_burn_text(raw)
        if not burn_text:
            continue
        layout = str(seg.get("layout") or "horizontal")
        if layout not in ("horizontal", "vertical", "label"):
            layout = "horizontal"
        s0 = float(seg["start"])
        e0 = float(seg["end"])
        # Heuristic: title dọc flash đầu clip (layout bị mất khi UI save cũ)
        if layout == "horizontal":
            src = (seg.get("source") or "").strip()
            cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
            dur = max(0.0, e0 - s0)
            if (
                s0 < 2.0
                and dur <= 0.55
                and cjk >= 3
                and cjk >= len(re.sub(r"\s+", "", src)) * 0.7
            ):
                layout = "vertical"
        if layout == "vertical":
            # title flash ~ms — bám sát, không pad
            burn_start = max(0.0, s0)
            burn_end = max(e0, burn_start + 0.04)
            cover_start, cover_end = burn_start, burn_end
        elif layout == "label":
            # nhãn: cover sớm hơn burn (CJK hay lộ trước ASR) — lead 120ms
            cover_start = max(0.0, s0 - 0.12)
            cover_end = max(e0 + 0.10, cover_start + 0.20)
            burn_start = max(0.0, s0)
            burn_end = max(e0, burn_start + 0.04)
        else:
            # Cover nới để che hardsub; BURN chữ dịch = clip timeline [start,end)
            # (khớp preview — nới burn gây đè câu sau lên câu trước)
            src_cjk = sum(1 for c in (seg.get("source") or "") if "\u4e00" <= c <= "\u9fff")
            lead = 0.35
            tail = 1.05 if (e0 - s0) <= 0.75 and src_cjk <= 4 else 0.45
            burn_start = max(0.0, s0)
            burn_end = max(e0, burn_start + 0.04)
            cover_start = max(0.0, s0 - lead)
            cover_end = max(e0 + max(0.4, tail), cover_start + 0.20)
        cues.append(
            (cover_start, cover_end, burn_start, burn_end, burn_text, source, layout)
        )
        cue_segment_ids.append(str(seg.get("id") or ""))
    # Lấp khe cover nhỏ giữa 2 câu hardsub (không đụng tiêu đề dọc / nhãn).
    for i in range(len(cues) - 1):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[i]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[i + 1]
        if lay0 in ("vertical", "label") or lay1 in ("vertical", "label"):
            continue
        gap = cs1 - ce0
        if 0.0 < gap < 0.45:
            mid = (ce0 + cs1) * 0.5
            cues[i] = (cs0, mid, bs0, be0, t0, src0, lay0)
            cues[i + 1] = (mid, ce1, bs1, be1, t1, src1, lay1)
    # Không cho cửa sổ burn chữ dịch chồng nhau (an toàn nếu timeline overlap)
    for i in range(len(cues) - 1):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[i]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[i + 1]
        if be0 > bs1:
            cut = (be0 + bs1) * 0.5
            cues[i] = (cs0, ce0, bs0, max(bs0 + 0.04, cut), t0, src0, lay0)
            cues[i + 1] = (cs1, ce1, min(be1 - 0.04, cut), be1, t1, src1, lay1)

    ocr = None
    segments_by_id = {str(seg.get("id") or ""): seg for seg in segments}
    manual_by_idx: list[tuple[int, int, int, int] | None] = [
        _segment_bbox_override(segments_by_id.get(sid, {}), w, h)
        for sid in cue_segment_ids
    ]
    cue_boxes: list[list[tuple[int, int, int, int]]] = [[] for _ in cues]
    for i, mb in enumerate(manual_by_idx):
        if mb is not None:
            cue_boxes[i] = [mb]

    need_ocr_idx = [i for i, mb in enumerate(manual_by_idx) if mb is None]
    manual_n = len(cues) - len(need_ocr_idx)
    if need_ocr_idx and (cover or burn) and cues:
        try:
            ocr = _rapidocr_labels()
        except ImportError:
            ocr = None
        if ocr is not None:
            if project_id:
                set_status(
                    project_id,
                    step="export",
                    progress=10,
                    message=(
                        f"Định vị hardsub ({len(need_ocr_idx)} câu)…"
                        if manual_n
                        else "Định vị hardsub…"
                    ),
                    running=True,
                )
            if len(need_ocr_idx) == len(cues):
                cue_boxes = _precompute_cue_boxes(
                    video,
                    cues,
                    ocr,
                    project_id=project_id,
                    workers=1 if _rapidocr_gpu_kwargs()["det_use_cuda"] else workers,
                )
            else:
                import cv2 as _cv2

                probe = _cv2.VideoCapture(str(video))
                try:
                    fh = int(probe.get(_cv2.CAP_PROP_FRAME_HEIGHT) or h)
                    fw = int(probe.get(_cv2.CAP_PROP_FRAME_WIDTH) or w)
                finally:
                    probe.release()
                ocr_workers = _resolve_workers(workers, n=len(need_ocr_idx))
                with ThreadPoolExecutor(
                    max_workers=ocr_workers, thread_name_prefix="ocr"
                ) as pool:
                    for i, boxes in pool.map(
                        lambda idx: (idx, _ocr_cue_boxes(video, cues[idx], ocr, fw, fh)),
                        need_ocr_idx,
                    ):
                        cue_boxes[i] = boxes
            ocr = None
    elif manual_n and (cover or burn) and project_id:
        set_status(
            project_id,
            step="export",
            progress=15,
            message=f"Dùng vùng che đã chỉnh trong preview ({manual_n} câu)",
            running=True,
        )

    font_cache: dict[int, Any] = {fontsize: font}

    def _font_for_size(size: int):
        fs = max(16, min(120, int(size)))
        cached = font_cache.get(fs)
        if cached is not None:
            return cached
        try:
            cached = ImageFont.truetype(_subtitle_font(), fs)
        except OSError:
            cached = ImageFont.load_default()
        font_cache[fs] = cached
        return cached

    # frame mẫu giữa mỗi cue label — expand cover theo mực chữ thật
    label_probe_frames: dict[int, Any] = {}
    if cover and any(
        (c[6] if len(c) > 6 else "") == "label" for c in cues
    ):
        import cv2 as _cv2

        _cap = _cv2.VideoCapture(str(video))
        try:
            for i, cue in enumerate(cues):
                lay_m = cue[6] if len(cue) > 6 else "horizontal"
                if lay_m != "label":
                    continue
                mid = (float(cue[0]) + float(cue[1])) * 0.5
                _cap.set(_cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
                ok, fr = _cap.read()
                if ok:
                    label_probe_frames[i] = fr
        finally:
            _cap.release()

    cue_overlays: list[tuple[Any, int, int] | None] = []
    # mỗi cue: 1+ vùng cover (nhãn multi-box)
    cue_fits: list[list[tuple[int, int, int, int]]] = []
    for i, (_cs, _ce, _bs, _be, text, src, lay_mode) in enumerate(cues):
        segment_id = cue_segment_ids[i] if i < len(cue_segment_ids) else ""
        boxes = list(cue_boxes[i] if i < len(cue_boxes) else [])
        paint = _union_box(boxes) if boxes else None
        has_manual_bbox = manual_by_idx[i] is not None
        is_vert = lay_mode == "vertical"
        is_label = lay_mode == "label"
        src_s = (src or "").strip()
        use_label_style = is_label
        if paint is None and cover:
            if is_vert:
                paint = (int(w * 0.46), int(h * 0.28), int(w * 0.54), int(h * 0.78))
            elif is_label:
                paint = (int(w * 0.06), int(h * 0.12), int(w * 0.28), int(h * 0.42))
            else:
                paint = (int(w * 0.08), int(h * 0.84), int(w * 0.92), int(h * 0.94))
            boxes = [paint]
        if is_vert and paint is not None:
            x0, y0, x1, y1 = paint
            pad_x = max(4, int(w * 0.008))
            pad_y = max(4, int(h * 0.004))
            bw = x1 - x0
            if bw > int(w * 0.18):
                cx = (x0 + x1) // 2
                half = max(14, min(int(w * 0.06), bw // 4 + 6))
                x0, x1 = cx - half, cx + half
            paint = (
                max(0, x0 - pad_x),
                max(0, y0 - pad_y),
                min(w, x1 + pad_x),
                min(h, y1 + pad_y),
            )
            boxes = [paint]
        # nhãn: xử lý TỪNG box (không union to)
        label_tall = False
        cover_regions: list[tuple[int, int, int, int]] = []
        if use_label_style:
            probe = label_probe_frames.get(i)
            raw_boxes = boxes if boxes else ([paint] if paint else [])
            refined: list[tuple[int, int, int, int]] = []
            for b in raw_boxes:
                bb = b
                if probe is not None:
                    try:
                        from ..ocr.labels import expand_box_to_ink

                        bb = expand_box_to_ink(probe, bb, w, h)
                    except Exception:
                        pass
                tall_b = is_tall_label(bb)
                bb = clamp_label_box(bb, w, h, force_tall=tall_b)
                refined.append(bb)
            boxes = refined or boxes
            # paint = box chính (lớn nhất) để đặt chữ
            if boxes:
                paint = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            label_tall = bool(paint) and is_tall_label(paint)
            for b in boxes:
                fit = cover_fit_label(
                    b,
                    None,
                    w,
                    h,
                    frame_bgr=probe,
                    force_tall=is_tall_label(b),
                )
                if fit:
                    cover_regions.append(fit)
        cover_box: tuple[int, int, int, int] | None = None
        cue_fs = fontsize
        if burn and text:
            seg_meta = segments_by_id.get(segment_id, {})
            cue_fs = _resolve_segment_font_size(
                seg_meta, w, h,
                project_font_size=subtitle_font_size,
                default_font_size=fontsize,
                auto_fontsize=auto_fontsize,
            )
            cue_font = _font_for_size(cue_fs)
            if is_vert:
                lay = _layout_caption_vertical(text, cue_font, cue_fs, paint, w, h)
            elif use_label_style:
                lab_fs = max(18, int(cue_fs * (0.75 if is_label else 0.85)))
                lay = layout_label_caption(
                    text,
                    cue_font,
                    lab_fs,
                    paint,
                    w,
                    h,
                    font_path=_subtitle_font(),
                    force_vertical=label_tall,
                    source=src_s,
                )
            elif layout_place == "over" and paint is not None:
                # Preview là bản chính: captionLayout + bbox thắng mọi fit lại
                preview_lay = _preview_caption_layout(seg_meta, cue_fs, _font_for_size)
                if has_manual_bbox:
                    cover_box = paint
                if preview_lay is not None:
                    lay = preview_lay
                    if cover_box is None:
                        mb = _segment_bbox_override(seg_meta, w, h)
                        if mb is not None:
                            cover_box = mb
                elif has_manual_bbox:
                    lay = _layout_caption_in_cover(
                        text, cue_fs, paint, w, _font_for_size,
                    )
                    cover_box = paint
                else:
                    lay, cover_box = _layout_caption_over(
                        text, cue_fs, paint, w, h, source_text=src_s,
                    )
            else:
                lay = _layout_caption(
                    text, cue_font, cue_fs, paint, w, h, placement=layout_place
                )
        else:
            lay = None
        cue_overlays.append(_caption_overlay(lay) if lay else None)
        if cover:
            if use_label_style:
                # chữ nằm trên paint — nới cover chính nếu cần, vẫn từng box
                if lay and paint is not None:
                    main = cover_fit_label(
                        paint,
                        lay["box"],
                        w,
                        h,
                        frame_bgr=label_probe_frames.get(i),
                        force_tall=label_tall,
                    )
                    if main:
                        # thay box chính trong regions
                        cover_regions = [
                            main
                            if (
                                abs(r[0] - paint[0]) < 30
                                and abs(r[1] - paint[1]) < 30
                            )
                            else r
                            for r in cover_regions
                        ]
                        if not any(
                            abs(r[0] - main[0]) < 20 and abs(r[1] - main[1]) < 20
                            for r in cover_regions
                        ):
                            cover_regions.append(main)
                cue_fits.append(cover_regions or ([paint] if paint else []))
            elif is_vert and paint is not None:
                x0, y0, x1, y1 = paint
                if lay:
                    lx0, ly0, lx1, ly1 = lay["box"]
                    x0, y0 = min(x0, lx0), min(y0, ly0)
                    x1, y1 = max(x1, lx1), max(y1, ly1)
                pad = max(4, int(w * 0.008))
                cue_fits.append(
                    [
                        (
                            max(0, x0 - pad),
                            max(0, y0 - pad),
                            min(w, x1 + pad),
                            min(h, y1 + pad),
                        )
                    ]
                )
            else:
                if layout_place == "over" and cover_box is not None:
                    # Đúng khung preview — không _cover_box_over (fit/phình lại)
                    cue_fits.append([cover_box])
                elif layout_place == "over" and has_manual_bbox and paint is not None:
                    cue_fits.append([paint])
                elif layout_place == "over" and lay:
                    cue_fits.append([lay["box"]])
                elif has_manual_bbox and paint is not None and layout_place != "over":
                    cue_fits.append([paint])
                else:
                    one = _cover_box_fit(
                        boxes,
                        lay["box"] if lay else None,
                        w,
                        h,
                        tight=layout_place == "over",
                    )
                    cue_fits.append([one] if one else [])
        else:
            cue_fits.append([])

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Không mở được video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{w}x{h}",
            "-r",
            str(fps),
            "-i",
            "-",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            # encode nhanh trung gian; run_export sẽ encode_export_1080 cuối
            *h264_encoder_args(fast=True),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-shortest",
            str(out),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None
    if project_id:
        _job_procs.setdefault(project_id, []).append(proc)

    # Cue indices cho mỗi frame. Title dọc có thể tồn tại đồng thời với hardsub
    # ngang; chỉ giữ một index sẽ làm title dài nuốt toàn bộ subtitle phía sau.
    max_frames = frame_total if frame_total > 0 else 10_000_000
    cover_idx: list[list[int]] = [[] for _ in range(max_frames)]
    burn_idx: list[list[int]] = [[] for _ in range(max_frames)]
    for ci, cue in enumerate(cues):
        if cover:
            f0 = max(0, int(float(cue[0]) * fps))
            f1 = min(max_frames, int(math.ceil(float(cue[1]) * fps)))
            for fi in range(f0, f1):
                cover_idx[fi].append(ci)
        if burn:
            f0 = max(0, int(float(cue[2]) * fps))
            f1 = min(max_frames, int(math.ceil(float(cue[3]) * fps)))
            for fi in range(f0, f1):
                burn_idx[fi].append(ci)

    def _paint_one(item: tuple[int, Any]) -> tuple[int, bytes]:
        fi, fr = item
        cis = cover_idx[fi] if fi < len(cover_idx) else []
        bis = burn_idx[fi] if fi < len(burn_idx) else []
        for ci in cis:
            fits = cue_fits[ci] if ci < len(cue_fits) else []
            for fit in fits:
                if fit is not None:
                    fr = _apply_cover_mask(
                        fr,
                        fit,
                        style=mask_style,
                        color_hex=mask_color,
                        opacity_pct=mask_opacity,
                    )
        has_label = any(
            (cues[bi][6] if len(cues[bi]) > 6 else "horizontal") == "label"
            for bi in bis
        )
        for bi in bis:
            # Nhãn chính ưu tiên chữ dịch; watermark dọc vẫn được
            # cover ở trên, chỉ tạm ẩn caption để không chồng chữ.
            if has_label and (cues[bi][6] if len(cues[bi]) > 6 else "") == "vertical":
                continue
            ov = cue_overlays[bi]
            if ov is not None:
                fr = _blit_overlay(fr, ov)
        return fi, fr.tobytes()

    # Prefetch đọc + pool blur/blit; ghi ffmpeg theo thứ tự frame.
    import threading
    from queue import Empty, Queue

    batch_n = max(24, workers * 12)
    # Hàng đợi batch đã paint (bytes theo thứ tự trong batch)
    painted_q: Queue[list[bytes] | None] = Queue(maxsize=max(2, min(6, workers)))
    read_err: list[BaseException] = []

    def _reader_painter() -> None:
        frame_i = 0
        try:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="burn"
            ) as pool:
                while True:
                    check_cancel(project_id)
                    batch: list[tuple[int, Any]] = []
                    for _ in range(batch_n):
                        ok, frame = cap.read()
                        if not ok:
                            break
                        batch.append((frame_i, frame))
                        frame_i += 1
                    if not batch:
                        break
                    # map giữ thứ tự → ghi ffmpeg tuần tự không reorder
                    raws = [raw for _fi, raw in pool.map(_paint_one, batch)]
                    painted_q.put(raws)
                    if project_id and frame_total > 0:
                        pct = 20 + int(50 * min(1.0, frame_i / frame_total))
                        set_status(
                            project_id,
                            step="export",
                            progress=pct,
                            message=f"Xuất khung {frame_i}/{frame_total} ({workers} luồng)",
                            running=True,
                        )
        except BaseException as e:
            read_err.append(e)
        finally:
            painted_q.put(None)

    t = threading.Thread(target=_reader_painter, name="burn-read", daemon=True)
    try:
        t.start()
        while True:
            check_cancel(project_id)
            try:
                batch_raw = painted_q.get(timeout=0.5)
            except Empty:
                if not t.is_alive() and painted_q.empty():
                    break
                continue
            if batch_raw is None:
                break
            for raw in batch_raw:
                proc.stdin.write(raw)
        t.join(timeout=5)
        if read_err:
            raise read_err[0]
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        rc = proc.wait()
        if project_id and project_id in _job_procs:
            _job_procs[project_id] = [x for x in _job_procs[project_id] if x is not proc]
    check_cancel(project_id)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"cover_and_burn ffmpeg failed (code={rc})")
    return out


def _wrap_text(draw: Any, text: str, font: Any, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines
