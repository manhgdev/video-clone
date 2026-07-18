"""Cover mask ops for hardsub burn (blur/solid/mosaic)."""
from __future__ import annotations

from typing import Any

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


