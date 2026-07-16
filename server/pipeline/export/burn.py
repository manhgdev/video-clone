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
from ..ocr.cover_timing import resolve_cover_window
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
    """Khớp preview `font-bold` — ưu tiên Arial/Segoe Bold."""
    return _pick_font(
        (
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ),
        cache_key="sub_bold",
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


def _blur_tint_alpha(opacity_pct: int) -> float:
    """Tint mỏng khớp coverMaskPreviewStyle CSS: clamp(opacity%×0.28, 0.06, 0.22)."""
    a_ui = max(0.0, min(1.0, float(opacity_pct) / 100.0))
    return max(0.06, min(0.22, a_ui * 0.28))


def _blur_css_radius(opacity_pct: int) -> float:
    """Khớp coverMaskPreviewStyle: blurPx = round(22 + a×20) → ~22–42 CSS-px."""
    a = max(0.0, min(1.0, float(opacity_pct) / 100.0))
    return 22.0 + a * 20.0


def _desaturate_bgr(img: Any, sat: float = 0.88) -> Any:
    """Khớp backdrop-filter saturate(0.88)."""
    import cv2
    import numpy as np

    if abs(sat - 1.0) < 1e-3:
        return img
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * sat, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _css_glass_blur(expanded: Any, radius_src: float) -> Any:
    """Gần backdrop-filter: downscale → blur → upscale (kính mờ, không kem Gaussian kép)."""
    import cv2

    eh, ew = expanded.shape[:2]
    if eh < 2 or ew < 2 or radius_src < 0.5:
        return expanded
    # Browser hay downsample khi blur lớn — cho look frosted glass
    down = max(1.0, radius_src / 8.0)
    tw = max(4, int(round(ew / down)))
    th = max(4, int(round(eh / down)))
    small = cv2.resize(expanded, (tw, th), interpolation=cv2.INTER_AREA)
    # Chromium: sigma ≈ blur/2 (ở không gian đã scale)
    sigma = max(0.5, radius_src / (2.0 * down))
    k = max(3, int(round(sigma * 3.0)) | 1)
    if k % 2 == 0:
        k += 1
    k = min(k, (min(tw, th) | 1))
    if k % 2 == 0:
        k = max(3, k - 1)
    small = cv2.GaussianBlur(small, (k, k), sigmaX=sigma, sigmaY=sigma)
    return cv2.resize(small, (ew, eh), interpolation=cv2.INTER_LINEAR)


def _blur_tint_region(
    frame_bgr: Any,
    box: tuple[int, int, int, int],
    color_hex: str = "#4c1d95",
    opacity_pct: int = 40,
) -> Any:
    """Làm mờ = kính CapCut khớp editor: blur CSS + saturate(0.88) + tint mỏng.

    Preview: backdrop-filter blur(22–42px) saturate(0.88) + rgba tint.
    Export cũ (2× Gaussian theo chiều box) nhìn khác hẳn — đổi sang downscale-blur.
    """
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
    css_blur = _blur_css_radius(opacity_pct)
    # Map CSS-px → pixel nguồn: stage editor ~560 CSS-px cạnh ngắn
    css_to_src = max(1.0, min(w, h) / 560.0)
    radius = css_blur * css_to_src
    pad = max(int(round(radius)) + 4, 16)
    ex0, ey0 = max(0, x0 - pad), max(0, y0 - pad)
    ex1, ey1 = min(w, x1 + pad), min(h, y1 + pad)
    expanded = frame_bgr[ey0:ey1, ex0:ex1]
    blurred_exp = _css_glass_blur(expanded, radius)
    ly0, lx0 = y0 - ey0, x0 - ex0
    covered = blurred_exp[ly0 : ly0 + bh, lx0 : lx0 + bw].copy()
    covered = _desaturate_bgr(covered, 0.88)
    r, g, b = _parse_hex_color(color_hex)
    tint_bgr = np.array([b, g, r], dtype=np.float32)
    alpha = _blur_tint_alpha(opacity_pct)
    if alpha >= 0.005:
        covered = (covered.astype(np.float32) * (1.0 - alpha) + tint_bgr * alpha).astype(np.uint8)
    # Thay ROI 100% (mép mềm nhờ pad lấy pixel ngoài)
    frame_bgr[y0:y1, x0:x1] = covered
    return frame_bgr


def _feather_mask(bh: int, bw: int, feather_y: int, feather_x: int = 0) -> Any:
    """Alpha mask mép mềm — dùng solid/mosaic; blur CapCut không cần."""
    import numpy as np

    a = np.ones((bh, bw), np.float32)
    fy = max(0, min(feather_y, bh // 2))
    fx = max(0, min(feather_x, bw // 2))

    def _smooth(t: float) -> float:
        return t * t * (3.0 - 2.0 * t)

    for i in range(fy):
        t = _smooth((i + 1) / (fy + 1))
        a[i, :] = np.minimum(a[i, :], t)
        a[-(i + 1), :] = np.minimum(a[-(i + 1), :], t)
    for i in range(fx):
        t = _smooth((i + 1) / (fx + 1))
        a[:, i] = np.minimum(a[:, i], t)
        a[:, -(i + 1)] = np.minimum(a[:, -(i + 1)], t)
    return a


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
    pad_x = max(3, int(round(frame_w * 0.003)))
    pad_top = max(2, int(round(font_size * 0.04)))
    pad_bot = max(18, int(round(font_size * 0.55)))
    return pad_x, pad_top, pad_bot


_COVER_SHADOW_BOT = 4


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
        src_fs = max(int(round(font_size * 1.12)), int(round(ocr_h * 0.92)), 28)
        try:
            src_font = ImageFont.truetype(font_path, src_fs)
            raw = draw.textbbox((0, 0), src, font=src_font)[2]
            cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
            cjk_floor = int(math.ceil(cjk * src_fs * 1.15)) if cjk else 0
            outline = int(math.ceil(src_fs * 0.5))
            # khớp preview measureSourceInkWidth — đủ outline hardsub
            src_w = max(int(math.ceil(raw * 1.2)), cjk_floor) + outline
        except OSError:
            pass

    orig_w = max(src_w, ocr_w) if src else ocr_w
    content_w = max(orig_w, text_w)
    cap_pad_x = 2
    cap_w = int(text_w + cap_pad_x * 2)
    # Cover = che full chữ cũ (content từ source/OCR), rồi mới fit nếu VI dài hơn
    auto_w = min(frame_w, max(_fit_cover_width(content_w, cap_w, frame_w), cap_w))
    cover_box = _fit_hardsub_box(
        (ox0, oy0, ox1, oy1), auto_w, font_size, frame_w, frame_h, src
    )
    cover_x0, cover_y0, cover_x1, cover_y1 = cover_box
    cover_w = cover_x1 - cover_x0
    cover_h = cover_y1 - cover_y0

    # Caption frame trong cover (có thể hẹp hơn cover) — không co mask theo VI
    cap_w = int(text_w + cap_pad_x * 2)
    if len(lines) == 1:
        edge = max(4, int(round(cover_w * 0.03)))
        cap_w = min(cover_w, max(cap_w, cover_w - edge * 2))
    cap_x0 = max(cover_x0, min(cover_x1 - cap_w, int((cover_x0 + cover_x1) / 2 - cap_w / 2)))
    # khớp preview captionCenterInCover — đúng giữa cover
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
    max_w = int(round(frame_w * 0.96))  # hardsub đáy hay >72% — đừng cắt lộ chữ
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


def _text_fill_rgba(segment: dict[str, Any] | None) -> tuple[int, int, int, int] | None:
    """Màu chữ free-text overlay (#RRGGBB) — None = trắng mặc định."""
    if not segment:
        return None
    raw = segment.get("textColor") or segment.get("color")
    if not isinstance(raw, str) or not raw.strip():
        return None
    r, g, b = _parse_hex_color(raw.strip(), (255, 255, 255))
    return (r, g, b, 255)


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
    m = 6
    bw, bh = (x1 - x0) + 2 * m, (y1 - y0) + 2 * m
    if bw < 4 or bh < 4:
        return None
    overlay = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    box_w = x1 - x0
    box_h = y1 - y0
    ty = m + max(0, (box_h - text_h) // 2)
    thick = bool(layout.get("vertical") or layout.get("label"))
    # Dọc/nhãn: viền dày. Mid/ngang: soft drop-shadow khớp preview (drop-shadow 0 2px 4px).
    outline_thick = (
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
    soft_shadow = (
        (0, 1, 120),
        (0, 2, 200),
        (0, 3, 140),
        (0, 4, 80),
        (1, 2, 110),
        (-1, 2, 110),
        (2, 3, 70),
        (-2, 3, 70),
        (1, 1, 60),
        (-1, 1, 60),
    )
    for i, line in enumerate(lines):
        bb = draw.textbbox((0, 0), line, font=font)
        tw = bb[2] - bb[0]
        th = line_hs[i] if i < len(line_hs) else (bb[3] - bb[1])
        tx = m + (box_w - tw) // 2
        # bù top bearing của font (textbbox top thường > 0) → căn giữa đúng nét.
        top = bb[1]
        gy = ty - top
        if thick:
            for dx, dy in outline_thick:
                draw.text((tx + dx, gy + dy), line, font=font, fill=(0, 0, 0, 250))
        else:
            for dx, dy, aa in soft_shadow:
                draw.text((tx + dx, gy + dy), line, font=font, fill=(0, 0, 0, aa))
        draw.text((tx, gy), line, font=font, fill=fill)
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
            elif layout == "mid":
                # Hardsub ngang giữa khung — bám OCR thật, không band đáy
                b, _tx = _ocr_mid_hardsub_boxes(frame, ocr, source=source or "")
            else:
                # horizontal: ưu tiên vị trí động theo source
                # (chữ giữa khung như「咱们拿回家做一顶」≠ cố định đáy)
                src = source or ""
                src_cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
                mid_b, mid_tx = ([], "")
                if src_cjk >= 1:
                    mid_b, mid_tx = _ocr_mid_hardsub_boxes(frame, ocr, source=src)
                if mid_b:
                    u0 = mid_b[0]
                    cy0 = (u0[1] + u0[3]) * 0.5
                    # cy giữa khung → dùng mid; nếu thực sự sát đáy thì để band xử lý
                    if fh * 0.18 < cy0 < fh * 0.70:
                        b = mid_b
                    else:
                        b, _tx = _ocr_band_subs(frame, ocr)
                else:
                    b, _tx = _ocr_band_subs(frame, ocr)
                    # Band có thể bắt nhầm chữ đáy khác — nếu source mid flash ngắn, thử mid không lọc
                    if (not b) and 0 < src_cjk <= 4 and len(src.strip()) <= 6:
                        b, _tx = _ocr_mid_hardsub_boxes(frame, ocr, source=src)
            u = _union_box(b) if b else None
            if u is None and layout not in ("vertical", "label"):
                # Chỉ fallback mực đáy khi không phải mid (tránh kéo cover xuống)
                if layout != "mid":
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


def _expand_vertical_watermark_cover(
    box: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    frame_bgr: Any | None = None,
) -> tuple[int, int, int, int]:
    """Che sát mực watermark: Latin trái + đuôi dưới; pad mỏng — không phình slab."""
    import cv2
    import numpy as np

    x0, y0, x1, y1 = box
    col_w = max(1, x1 - x0)
    col_h = max(1, y1 - y0)
    if frame_bgr is not None:
        # Quét mực trắng hẹp: trái + dưới quanh cột OCR
        mx = max(18, int(col_w * 0.85), int(frame_w * 0.028))
        my_bot = max(28, int(col_h * 0.55), int(frame_h * 0.03))
        my_top = max(4, int(col_h * 0.04))
        rx0 = max(0, x0 - mx)
        ry0 = max(0, y0 - my_top)
        rx1 = min(frame_w, x1 + max(4, int(col_w * 0.12)))
        ry1 = min(frame_h, y1 + my_bot)
        roi = frame_bgr[ry0:ry1, rx0:rx1]
        if roi.size >= 100:
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            bright = cv2.threshold(gray, 175, 255, cv2.THRESH_BINARY)[1]
            # chỉ dải ngang sát cột (CJK + Latin trái)
            cx = (x0 + x1) // 2 - rx0
            half = max(int(col_w * 0.95), int(frame_w * 0.04), 28)
            col_m = np.zeros_like(bright)
            col_m[:, max(0, cx - half) : min(bright.shape[1], cx + half)] = 255
            mask = cv2.bitwise_and(bright, col_m)
            ys, xs = np.where(mask > 0)
            if len(xs) >= 20:
                x0 = min(x0, rx0 + int(xs.min()))
                y0 = min(y0, ry0 + int(ys.min()))
                x1 = max(x1, rx0 + int(xs.max()) + 1)
                y1 = max(y1, ry0 + int(ys.max()) + 1)
    # pad mỏng sau ink
    pad_x = max(3, int((x1 - x0) * 0.06))
    pad_y = max(3, int((y1 - y0) * 0.04))
    # trần: không to quá ~2× cột OCR gốc
    max_w = max(col_w + 36, int(col_w * 1.9))
    max_h = max(col_h + 40, int(col_h * 1.45))
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    if x1 - x0 > max_w:
        x0, x1 = cx - max_w // 2, cx + max_w // 2
    if y1 - y0 > max_h:
        # ưu tiên giữ mép trên OCR, cắt đáy thừa
        y1 = min(y1, y0 + max_h)
    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(frame_w, x1 + pad_x),
        min(frame_h, y1 + pad_y),
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


def _box_cy(box: tuple[int, int, int, int]) -> float:
    return (box[1] + box[3]) * 0.5


def _bbox_looks_bottom(box: tuple[int, int, int, int], frame_h: int) -> bool:
    """Cover/caption dính đáy khung (fallback hardsub) — hay sai với chữ giữa."""
    return _box_cy(box) >= frame_h * 0.68


def _editor_layout_locked(segment: dict[str, Any]) -> bool:
    """Editor đã bake bbox + captionLayout — xuất đúng WYSIWYG, không relocate/OCR/re-layout."""
    cl = segment.get("captionLayout")
    bb = segment.get("bbox")
    if not isinstance(cl, dict) or not isinstance(bb, dict):
        return False
    lines = cl.get("lines")
    return isinstance(lines, list) and len(lines) > 0


def _should_paint_cover_mask(cover: bool, layout: str) -> bool:
    """Khớp preview: mid/dọc/nhãn che khi burnSubs; ngang chỉ khi coverHardsubs."""
    if cover:
        return True
    return (layout or "horizontal") in ("mid", "vertical", "label")


def _stored_cover_should_relocate(
    segment: dict[str, Any],
    box: tuple[int, int, int, int],
    frame_h: int,
) -> bool:
    """Bỏ bbox đáy bake khi chưa khóa editor (để OCR lại). Editor locked → không relocate."""
    if _editor_layout_locked(segment):
        return False
    layout = str(segment.get("layout") or "horizontal")
    if layout in ("vertical", "label"):
        return False
    src = str(segment.get("source") or "")
    cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
    if cjk < 2:
        return False
    if not _bbox_looks_bottom(box, frame_h):
        return False
    return True


def _caption_layout_looks_bottom(segment: dict[str, Any], frame_h: int) -> bool:
    cl = segment.get("captionLayout")
    if not isinstance(cl, dict):
        return False
    try:
        y = float(cl.get("y") or 0)
        bh = float(cl.get("h") or 0)
    except (TypeError, ValueError):
        return False
    return (y + bh * 0.5) >= frame_h * 0.68


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

    size = (
        max(10, min(72, int(preferred_fs)))
        if preferred_fs > 0
        else max(
            10,
            min(
                # fill ~90% chiều cao inner — bbox dư trống → chữ to hơn, không phình H
                max(28, int(inner_h * 0.90)),
                int(inner_h / line_mul),
                int(inner_w / max(4, len(re.sub(r"\s+", "", raw)) * 0.52)),
            ),
        )
    )
    lines = _fit(size)
    while size > 8 and (
        not _kept(lines)
        or len(lines) * size * line_mul > inner_h
        or any(
            draw.textbbox((0, 0), ln, font=font_getter(size))[2] > inner_w + 2
            for ln in lines
        )
    ):
        size -= 1
        lines = _fit(size)
    if len(lines) > 1 and len(re.sub(r"\s+", "", raw)) <= 14:
        for fs in range(size, 9, -1):
            if (
                draw.textbbox((0, 0), raw, font=font_getter(fs))[2] <= inner_w
                and fs * line_mul <= inner_h
            ):
                size, lines = fs, [raw]
                break
    font_use = font_getter(size)
    line_boxes = [draw.textbbox((0, 0), ln, font=font_use) for ln in lines]
    line_hs = [max(1, b[3] - b[1]) for b in line_boxes]
    gap_line = max(2, size // 8)
    line_h = (max(line_hs) if line_hs else size) + gap_line
    text_h = sum(line_hs) + gap_line * max(0, len(lines) - 1)
    box_w = min(frame_w, cw)
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
        target_h = min(frame_h, ocr_h + 8)
        # nới cột cho VI dài — tối đa ~16% khung / OCR+pad
        max_w = max(40, min(int(frame_w * 0.16), max(ocr_w + 24, int(ocr_w * 1.6))))
        # khung lớn → chữ lớn: chia chiều cao cột cho số đơn vị
        by_h = int((ocr_h * 0.78) / max(1, n_units_est))
        by_w = int(ocr_w * (0.85 if pure_cjk else 0.72))
        base = max(18, min(72, int(fontsize * 1.05) if fontsize else 36, by_h, by_w))
    else:
        target_h = int(frame_h * (0.22 if compact else 0.55))
        max_w = int(frame_w * 0.18)
        base = max(22, min(72, int(fontsize * 1.1), int(frame_w * 0.09)))

    n = len(units)
    size = base
    font_use = font
    ch_ws: list[int] = []
    ch_hs: list[int] = []
    pad_x = pad_y = gap = 4
    # thử phóng lớn trước rồi thu nếu tràn
    for scale in (1.35, 1.25, 1.15, 1.05, 1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.52):
        size = max(16, int(base * scale))
        try:
            font_use = ImageFont.truetype(font_path, size)
        except OSError:
            try:
                font_use = ImageFont.truetype(_subtitle_font(), size)
            except OSError:
                font_use = font
        pad_x = max(3, size // 8)
        pad_y = max(4, size // 8)
        boxes = [draw.textbbox((0, 0), u, font=font_use) for u in units]
        ch_ws = [max(1, b[2] - b[0]) for b in boxes]
        ch_hs = [max(1, b[3] - b[1]) for b in boxes]
        col_w = max(ch_ws) if ch_ws else size
        sum_h = sum(ch_hs)
        min_gap = max(2, size // 10) if not pure_cjk else max(2, size // 12)
        min_h = sum_h + min_gap * max(0, n - 1) + pad_y * 2
        box_w = col_w + pad_x * 2
        if box_w <= max_w and min_h <= target_h * 1.05:
            break

    col_w = max(ch_ws) if ch_ws else size
    sum_h = sum(ch_hs) if ch_hs else size * n
    floor_gap = max(3, size // 8) if not pure_cjk else max(2, size // 12)
    pad_y = max(4, size // 8)
    pad_x = max(3, size // 8)
    gap = floor_gap if n <= 1 else max(floor_gap, size // 10)
    text_h = sum_h + gap * max(0, n - 1)
    # Block ≈ text; căn giữa trong cột OCR (mask vẫn dùng bbox riêng)
    box_h = min(frame_h, text_h + pad_y * 2)
    box_w = min(frame_w, col_w + pad_x * 2)
    if ocr_box:
        ocr_h = max(48, ocr_box[3] - ocr_box[1])
        cx = (ocr_box[0] + ocr_box[2]) // 2
        y0 = max(0, min(frame_h - box_h, ocr_box[1] + max(0, (ocr_h - box_h) // 2)))
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
        "distribute": False,
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

    from ..ocr.extract import _drop_mid_in_watermark_column

    segments = _drop_mid_in_watermark_column(list(segments))

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
        if layout not in ("horizontal", "vertical", "label", "mid"):
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
            # Khớp preview: chữ + mask theo coverWindow
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start, burn_end = cover_start, max(cover_end, cover_start + 0.04)
        elif layout == "label":
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start, burn_end = cover_start, max(cover_end, cover_start + 0.04)
        elif layout == "mid":
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start, burn_end = cover_start, max(cover_end, cover_start + 0.04)
        else:
            # Cover nới để che hardsub; BURN chữ dịch = clip timeline [start,end)
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start = max(0.0, s0)
            burn_end = max(e0, burn_start + 0.04)
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
        if lay0 == "mid" or lay1 == "mid":
            continue  # mid xử lý riêng — không lấp khe kiểu đáy
        gap = cs1 - ce0
        if 0.0 < gap < 0.45:
            mid = (ce0 + cs1) * 0.5
            cues[i] = (cs0, mid, bs0, be0, t0, src0, lay0)
            cues[i + 1] = (mid, ce1, bs1, be1, t1, src1, lay1)
    # Mid-mid: cắt cover/burn tại giữa khe — không đè «hoàn thiện» bằng câu sau
    mid_idx = [i for i, c in enumerate(cues) if (c[6] if len(c) > 6 else "") == "mid"]
    for a, b in zip(mid_idx, mid_idx[1:]):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[a]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[b]
        # dùng biên burn lõi (gần [start,end) segment) để chia
        core_end = min(be0, ce0)
        core_start = max(bs1, cs1)
        cut = (core_end + core_start) * 0.5
        if ce0 > cut:
            cues[a] = (cs0, min(ce0, cut), bs0, min(be0, cut), t0, src0, lay0)
        if cs1 < cut:
            cues[b] = (max(cs1, cut), ce1, max(bs1, cut), be1, t1, src1, lay1)
    # Horizontal-horizontal: cắt COVER chồng (tail câu trước đè bbox sau)
    for i in range(len(cues) - 1):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[i]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[i + 1]
        if lay0 != "horizontal" or lay1 != "horizontal":
            continue
        # cắt tại giữa [end0, start1] (clip timeline), không theo cover pad
        # lấy segment gốc từ ids
        sid0 = cue_segment_ids[i] if i < len(cue_segment_ids) else ""
        sid1 = cue_segment_ids[i + 1] if i + 1 < len(cue_segment_ids) else ""
        seg0 = next((s for s in segments if str(s.get("id") or "") == sid0), None)
        seg1 = next((s for s in segments if str(s.get("id") or "") == sid1), None)
        e0 = float(seg0["end"]) if seg0 else be0
        s1 = float(seg1["start"]) if seg1 else bs1
        cut = (e0 + s1) * 0.5
        if ce0 > cut:
            cues[i] = (cs0, min(ce0, cut), bs0, be0, t0, src0, lay0)
        if cs1 < cut:
            cues[i + 1] = (max(cs1, cut), ce1, bs1, be1, t1, src1, lay1)
    # Không cho cửa sổ burn chữ dịch chồng nhau — chỉ hardsub đáy (ngang).
    # vertical/label/mid khác vị trí → được phép overlap (watermark dọc xuyên clip).
    for i in range(len(cues) - 1):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[i]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[i + 1]
        if lay0 != "horizontal" or lay1 != "horizontal":
            continue
        if be0 > bs1:
            cut = (be0 + bs1) * 0.5
            cues[i] = (cs0, ce0, bs0, max(bs0 + 0.04, cut), t0, src0, lay0)
            cues[i + 1] = (cs1, ce1, min(be1 - 0.04, cut), be1, t1, src1, lay1)

    ocr = None
    segments_by_id = {str(seg.get("id") or ""): seg for seg in segments}
    manual_by_idx: list[tuple[int, int, int, int] | None] = []
    for sid in cue_segment_ids:
        seg = segments_by_id.get(sid, {})
        mb = _segment_bbox_override(seg, w, h)
        # Bbox đáy bake sẵn + source CJK → bỏ, OCR lại vị trí thật (giữa/đáy)
        if mb is not None and _stored_cover_should_relocate(seg, mb, h):
            mb = None
        manual_by_idx.append(mb)
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
        fs = max(8, min(120, int(size)))
        cached = font_cache.get(fs)
        if cached is not None:
            return cached
        try:
            cached = ImageFont.truetype(_subtitle_font(), fs)
        except OSError:
            cached = ImageFont.load_default()
        font_cache[fs] = cached
        return cached

    # frame mẫu giữa cue label/dọc — expand cover theo mực chữ thật
    label_probe_frames: dict[int, Any] = {}
    if any(
        _should_paint_cover_mask(cover, (c[6] if len(c) > 6 else ""))
        and (c[6] if len(c) > 6 else "") in ("label", "vertical")
        for c in cues
    ):
        import cv2 as _cv2

        _cap = _cv2.VideoCapture(str(video))
        try:
            for i, cue in enumerate(cues):
                lay_m = cue[6] if len(cue) > 6 else "horizontal"
                if lay_m not in ("label", "vertical"):
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
    cue_need_mask: list[bool] = []
    for i, (_cs, _ce, _bs, _be, text, src, lay_mode) in enumerate(cues):
        segment_id = cue_segment_ids[i] if i < len(cue_segment_ids) else ""
        boxes = list(cue_boxes[i] if i < len(cue_boxes) else [])
        paint = _union_box(boxes) if boxes else None
        has_manual_bbox = manual_by_idx[i] is not None
        is_vert = lay_mode == "vertical"
        is_label = lay_mode == "label"
        is_mid = lay_mode == "mid"
        src_s = (src or "").strip()
        use_label_style = is_label
        # Fallback paint: coverHardsubs hoặc overlay mid/dọc (preview vẫn mask khi burn)
        if paint is None and _should_paint_cover_mask(cover, lay_mode):
            from ..ocr.overlay_cover import default_overlay_paint, is_mid_flash_source

            if is_vert:
                paint = default_overlay_paint("vertical", w, h)
            elif is_label:
                paint = default_overlay_paint("label", w, h)
            elif is_mid or is_mid_flash_source(src_s):
                paint = default_overlay_paint("mid", w, h)
            else:
                paint = (int(w * 0.08), int(h * 0.84), int(w * 0.92), int(h * 0.94))
            boxes = [paint]
        # OCR bbox giữa khung → che/chữ tại đó (không ép đáy dù layout=horizontal)
        paint_mid = False
        if paint is not None and not is_vert and not is_label:
            pcy = (paint[1] + paint[3]) * 0.5
            paint_mid = h * 0.18 < pcy < h * 0.70
            if paint_mid:
                is_mid = True
        if is_vert and paint is not None:
            if has_manual_bbox:
                # bbox editor = khung mask/chữ — không expand ink lại
                boxes = [paint]
                layout_paint = paint
                ink = None
            else:
                x0, y0, x1, y1 = paint
                bw = x1 - x0
                if bw > int(w * 0.18):
                    half = max(12, min(int(w * 0.05), bw // 4 + 4))
                    if (x0 + x1) / 2 < w * 0.5:
                        x1 = min(w, x0 + half * 2)
                    else:
                        x0 = max(0, x1 - half * 2)
                cjk_paint = (x0, y0, x1, y1)
                # Cover = mực (CJK+HUAMUZI); caption cao theo cover, ngang bám CJK.
                ink = _expand_vertical_watermark_cover(
                    cjk_paint, w, h, frame_bgr=label_probe_frames.get(i)
                )
                paint = cjk_paint
                boxes = [ink]
                # cho layout: cùng cx CJK, cao đủ tới đáy ink
                layout_paint = (cjk_paint[0], ink[1], cjk_paint[2], ink[3])
        else:
            layout_paint = paint
            ink = None
        # nhãn: xử lý TỪNG box (không union to)
        label_tall = False
        cover_regions: list[tuple[int, int, int, int]] = []
        if use_label_style:
            if has_manual_bbox and paint is not None:
                cover_regions = [paint]
                boxes = [paint]
                label_tall = is_tall_label(paint)
            else:
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
        used_preview_layout = False
        if burn and text:
            seg_meta = segments_by_id.get(segment_id, {})
            cue_fs = _resolve_segment_font_size(
                seg_meta, w, h,
                project_font_size=subtitle_font_size,
                default_font_size=fontsize,
                auto_fontsize=auto_fontsize,
            )
            cue_font = _font_for_size(cue_fs)
            # WYSIWYG: captionLayout từ editor (đã bake giống preview lúc Xuất)
            preview_lay = _preview_caption_layout(
                seg_meta, cue_fs, _font_for_size, layout_mode=lay_mode,
            )
            editor_locked = _editor_layout_locked(seg_meta)
            lay: dict[str, Any] | None = None
            # Mid: ưu tiên layout bake từ preview (caption trong bbox); fallback fit OCR
            if is_mid and paint is not None:
                if preview_lay is not None and editor_locked:
                    lay = preview_lay
                    cover_box = paint
                    used_preview_layout = True
                else:
                    cl = seg_meta.get("captionLayout") if isinstance(seg_meta.get("captionLayout"), dict) else {}
                    mid_pref = int(
                        seg_meta.get("fontSize")
                        or (cl.get("fontSize") if cl else 0)
                        or 0
                    )
                    lay = _layout_mid_caption(
                        text,
                        _font_for_size,
                        paint,
                        w,
                        h,
                        preferred_fs=mid_pref,
                    )
                    cover_box = paint if lay else paint
                    used_preview_layout = cover_box is not None
            elif preview_lay is not None:
                use_preview = True
                if not editor_locked:
                    if paint is not None and paint_mid:
                        if _caption_layout_looks_bottom(seg_meta, h) or (
                            has_manual_bbox and _bbox_looks_bottom(paint, h)
                        ):
                            use_preview = False
                        elif _bbox_looks_bottom(preview_lay["box"], h) and paint_mid:
                            use_preview = False
                    if use_preview and not paint_mid:
                        if (
                            _caption_layout_looks_bottom(seg_meta, h)
                            and sum(1 for c in src_s if "\u4e00" <= c <= "\u9fff") >= 2
                            and lay_mode in ("horizontal", "mid")
                        ):
                            use_preview = False
                if use_preview:
                    lay = preview_lay
                    used_preview_layout = True
                    if editor_locked:
                        mb = _segment_bbox_override(seg_meta, w, h)
                        cover_box = mb if mb is not None else (paint if paint is not None else preview_lay["box"])
                    elif has_manual_bbox and paint is not None and not paint_mid:
                        cover_box = paint
                    elif paint_mid and paint is not None:
                        cover_box = paint
                    else:
                        mb = _segment_bbox_override(seg_meta, w, h)
                        if mb is not None and not _stored_cover_should_relocate(seg_meta, mb, h):
                            cover_box = mb
                        else:
                            cover_box = paint
            if lay is None and is_vert:
                lay = _layout_caption_vertical(
                    text, cue_font, cue_fs, layout_paint if layout_paint else paint, w, h
                )
            elif lay is None and is_mid and paint is not None:
                mid_pref = int(seg_meta.get("fontSize") or 0)
                lay = _layout_mid_caption(
                    text,
                    _font_for_size,
                    paint,
                    w,
                    h,
                    preferred_fs=mid_pref,
                )
                cover_box = lay["box"] if lay else paint
            elif lay is None and use_label_style:
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
            elif lay is None and layout_place == "over" and paint is not None:
                # Overlay OCR không có captionLayout → classic / manual / auto-over
                from ..ocr.overlay_cover import (
                    classic_cover_fit,
                    use_classic_overlay_cover,
                )

                if has_manual_bbox:
                    cover_box = paint
                if use_classic_overlay_cover(
                    layout=lay_mode,
                    source=src_s,
                    has_preview_layout=False,
                ):
                    # đường riêng overlay — layout chữ trong OCR box + cover fit c9
                    lay = _layout_caption(
                        text, cue_font, cue_fs, paint, w, h, placement="over"
                    )
                    cover_box = classic_cover_fit(
                        boxes if boxes else ([paint] if paint else []),
                        lay["box"] if lay else None,
                        w,
                        h,
                    )
                elif has_manual_bbox:
                    lay = _layout_caption_in_cover(
                        text, cue_fs, paint, w, _font_for_size,
                    )
                    cover_box = paint
                else:
                    lay, cover_box = _layout_caption_over(
                        text, cue_fs, paint, w, h, source_text=src_s,
                    )
            elif lay is None:
                lay = _layout_caption(
                    text, cue_font, cue_fs, paint, w, h, placement=layout_place
                )
        else:
            lay = None
        cue_overlays.append(_caption_overlay(lay) if lay else None)
        # che mid/dọc dù coverHardsubs=false (preview maskBoxes cũng vậy);
        # is_mid có thể set sau khi OCR bbox nằm giữa khung
        need_mask = _should_paint_cover_mask(
            cover, "mid" if is_mid else lay_mode
        )
        if seg_meta.get("skipCoverMask"):
            need_mask = False
        cue_need_mask.append(need_mask)
        if need_mask:
            if used_preview_layout and cover_box is not None:
                cue_fits.append([cover_box])
            elif used_preview_layout and paint is not None:
                cue_fits.append([paint])
            elif use_label_style:
                # chữ nằm trên paint — nới cover chính nếu cần, vẫn từng box
                if lay and paint is not None and not has_manual_bbox:
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
                # Cover đã tính ink ở boxes[0]
                cov = boxes[0] if boxes else paint
                cue_fits.append([cov])
            elif is_mid and paint is not None:
                cb = cover_box if cover_box is not None else paint
                # nới nhẹ X cho stroke; Y giữ sát (pad locate đã đủ)
                px = max(6, int(round(w * 0.008)))
                py = max(3, int(round(h * 0.002)))
                cue_fits.append(
                    [
                        (
                            max(0, cb[0] - px),
                            max(0, cb[1] - py),
                            min(w, cb[2] + px),
                            min(h, cb[3] + py),
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
        if ci < len(cue_need_mask) and cue_need_mask[ci]:
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
        for bi in bis:
            # Không ẩn watermark dọc khi label trùng nguồn (OCR flicker cùng cột) —
            # chỉ tạm ẩn nếu nhãn thật khác chữ đang đè cùng frame.
            if (cues[bi][6] if len(cues[bi]) > 6 else "") == "vertical":
                vsrc = (cues[bi][5] if len(cues[bi]) > 5 else "") or ""
                conflict = False
                for bj in bis:
                    if bj == bi:
                        continue
                    if (cues[bj][6] if len(cues[bj]) > 6 else "") != "label":
                        continue
                    lsrc = (cues[bj][5] if len(cues[bj]) > 5 else "") or ""
                    # cùng watermark / gần giống → không conflict
                    if lsrc and vsrc and (
                        lsrc == vsrc
                        or lsrc in vsrc
                        or vsrc in lsrc
                        or abs(len(lsrc) - len(vsrc)) <= 1
                    ):
                        continue
                    conflict = True
                    break
                if conflict:
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
