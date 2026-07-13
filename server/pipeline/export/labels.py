"""Nhãn graphic: cover bám OCR CJK; chữ VI fill ô (ngang wrap / dọc stack)."""
from __future__ import annotations

import re
from typing import Any


def union_boxes(
    boxes: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int] | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def is_tall_label(box: tuple[int, int, int, int]) -> bool:
    bw = max(1, box[2] - box[0])
    bh = max(1, box[3] - box[1])
    return bh > bw * 1.35


def is_vertical_cjk_source(source: str) -> bool:
    """Cột chữ dọc CJK (茄子鸡丁馅备用) — không có · / space."""
    s = re.sub(r"\s+", "", source or "")
    if not s or "·" in s or "・" in s:
        return False
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    return cjk >= 4 and cjk >= len(s) * 0.85


def expand_label_column(
    boxes: list[tuple[int, int, int, int]],
    fw: int,
    fh: int,
) -> list[tuple[int, int, int, int]]:
    """Gộp box cùng cột dọc (cx gần, chồng Y) — OCR hay tách từng chữ."""
    if len(boxes) <= 1:
        return boxes
    used = [False] * len(boxes)
    out: list[tuple[int, int, int, int]] = []
    for i, b in enumerate(boxes):
        if used[i]:
            continue
        x0, y0, x1, y1 = b
        cx = (x0 + x1) * 0.5
        used[i] = True
        for j, b2 in enumerate(boxes):
            if used[j]:
                continue
            x2, y2, x3, y3 = b2
            cx2 = (x2 + x3) * 0.5
            # cùng cột: lệch ngang < 8% W, khoảng cách dọc < 6% H
            if abs(cx - cx2) > max(24, fw * 0.08):
                continue
            gap = max(0, y2 - y1, y0 - y3)
            if gap > max(28, fh * 0.06):
                continue
            x0, y0 = min(x0, x2), min(y0, y2)
            x1, y1 = max(x1, x3), max(y1, y3)
            cx = (x0 + x1) * 0.5
            used[j] = True
        out.append((x0, y0, x1, y1))
    return out


def clamp_label_box(
    box: tuple[int, int, int, int],
    fw: int,
    fh: int,
    *,
    max_w_ratio: float = 0.30,
    max_h_ratio: float = 0.55,
    force_tall: bool = False,
) -> tuple[int, int, int, int]:
    """Kẹp ô nhãn. Cột dọc: pad vừa đủ stroke, GIỮ tỷ lệ cao (không phình ngang)."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    tall = force_tall or bh > bw * 1.25
    if tall:
        # nới ngang: +40% bw mỗi bên tối đa, min ~ glyph+stroke (~2.2× bw gốc)
        # KHÔNG force min 9% frame (làm cột 38px → 100px+ → mất tall)
        target_w = min(int(fw * 0.12), max(int(bw * 2.2), bw + 28, 48))
        max_w = max(target_w, int(fw * 0.14))
        half = target_w // 2
        x0, x1 = cx - half, cx + half
        max_h = max(bh + 16, int(fh * max_h_ratio))
        if bh > max_h:
            half_h = max_h // 2
            y0, y1 = cy - half_h, cy + half_h
        pad_x = max(8, int(target_w * 0.08))
        pad_y = max(8, int(fh * 0.008))
    else:
        max_w = max(40, int(fw * max_w_ratio))
        max_h = max(36, int(fh * 0.20))
        if bw > max_w:
            half = max_w // 2
            x0, x1 = cx - half, cx + half
        if bh > max_h:
            half = max_h // 2
            y0, y1 = cy - half, cy + half
        pad_x = max(8, int(fw * 0.010))
        pad_y = max(6, int(fh * 0.007))
    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(fw, x1 + pad_x),
        min(fh, y1 + pad_y),
    )


def pick_label_box(
    boxes: list[tuple[int, int, int, int]],
    texts: list[str],
    source: str,
    fw: int,
    fh: int,
) -> tuple[int, int, int, int] | None:
    """Chọn box: gộp cột dọc → union; khớp source."""
    if not boxes:
        return None
    boxes = expand_label_column(boxes, fw, fh)
    src = (source or "").strip()
    if len(boxes) >= 2:
        u = union_boxes(boxes)
        if u:
            return clamp_label_box(u, fw, fh)
    scored: list[tuple[float, tuple[int, int, int, int]]] = []
    for i, b in enumerate(boxes):
        x0, y0, x1, y1 = b
        bw, bh = max(1, x1 - x0), max(1, y1 - y0)
        area = float(bw * bh)
        cx = (x0 + x1) * 0.5
        tx = texts[i] if i < len(texts) else ""
        match = 0.0
        if src and tx:
            if src in tx or tx in src or src == tx:
                match = 50.0
            elif any(c in tx for c in src if "\u4e00" <= c <= "\u9fff"):
                match = 25.0
        center = 1.0 - min(1.0, abs(cx / max(1, fw) - 0.5) * 1.2)
        compact = 1.0 / (1.0 + area / max(1.0, fw * fh * 0.002))
        score = match + center * 6.0 + compact * 8.0
        if sum(1 for c in src if "\u4e00" <= c <= "\u9fff") >= 3 and bh > bw * 1.15:
            score += 18.0
        # ưu tiên box cao hơn (đủ che cột)
        score += min(20.0, bh / max(1.0, fh) * 40.0)
        scored.append((score, b))
    scored.sort(key=lambda x: -x[0])
    return clamp_label_box(scored[0][1], fw, fh)


def expand_box_to_ink(
    frame_bgr: Any,
    box: tuple[int, int, int, int],
    fw: int,
    fh: int,
) -> tuple[int, int, int, int]:
    """Nới box theo mực chữ quanh OCR — chỉ ±~1.5× bề ngang cột (không nuốt cả khung)."""
    import cv2
    import numpy as np

    x0, y0, x1, y1 = box
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    tall = bh > bw * 1.15
    # ROI hẹp quanh cột
    mx = max(18, int(bw * (0.9 if tall else 0.55)), int(fw * 0.03))
    my = max(12, int(bh * 0.15), int(fh * 0.015))
    rx0, ry0 = max(0, x0 - mx), max(0, y0 - my)
    rx1, ry1 = min(fw, x1 + mx), min(fh, y1 + my)
    roi = frame_bgr[ry0:ry1, rx0:rx1]
    if roi.size < 100:
        return box
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # chữ sáng (hardsub trắng) — ngưỡng cao, bỏ nền
    bright = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)[1]
    # local contrast peaks
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    peak = cv2.subtract(gray, blur)
    _, peak_m = cv2.threshold(peak, 12, 255, cv2.THRESH_BINARY)
    mask = cv2.bitwise_or(bright, peak_m)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
    # cột dọc: chỉ giữ pixel trong dải ngang hẹp quanh cx
    cx = (x0 + x1) // 2 - rx0
    col_half = max(int(bw * 0.75), int(fw * 0.035), 22)
    col_mask = np.zeros_like(mask)
    c0 = max(0, cx - col_half)
    c1 = min(mask.shape[1], cx + col_half)
    col_mask[:, c0:c1] = 255
    mask = cv2.bitwise_and(mask, col_mask)
    # Y: trong box ± 12%
    y_pad = max(8, int(bh * 0.12))
    ry_local0 = max(0, (y0 - ry0) - y_pad)
    ry_local1 = min(mask.shape[0], (y1 - ry0) + y_pad)
    yband = np.zeros_like(mask)
    yband[ry_local0:ry_local1, :] = 255
    mask = cv2.bitwise_and(mask, yband)

    ys, xs = np.where(mask > 0)
    if len(xs) < 25:
        return box
    ix0 = rx0 + int(xs.min())
    iy0 = ry0 + int(ys.min())
    ix1 = rx0 + int(xs.max()) + 1
    iy1 = ry0 + int(ys.max()) + 1
    # kẹp: không nới quá 1.6× bw / 1.25× bh so với OCR
    max_w = int(bw * 1.65) + 16
    max_h = int(bh * 1.30) + 12
    cx0 = (x0 + x1) // 2
    cy0 = (y0 + y1) // 2
    ux0, uy0 = min(x0, ix0), min(y0, iy0)
    ux1, uy1 = max(x1, ix1), max(y1, iy1)
    if ux1 - ux0 > max_w:
        half = max_w // 2
        ux0, ux1 = cx0 - half, cx0 + half
    if uy1 - uy0 > max_h:
        half = max_h // 2
        uy0, uy1 = cy0 - half, cy0 + half
    return (
        max(0, ux0),
        max(0, uy0),
        min(fw, ux1),
        min(fh, uy1),
    )


def cover_fit_label(
    ocr_box: tuple[int, int, int, int] | None,
    text_box: tuple[int, int, int, int] | None,
    fw: int,
    fh: int,
    *,
    frame_bgr: Any | None = None,
    force_tall: bool = False,
) -> tuple[int, int, int, int] | None:
    """Cover = OCR (+ ink) + pad. Cột dọc: hẹp (≤14% W), không phình ngang."""
    base = ocr_box or text_box
    if base is None:
        return None
    if frame_bgr is not None:
        try:
            base = expand_box_to_ink(frame_bgr, base, fw, fh)
        except Exception:
            pass
    x0, y0, x1, y1 = base
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    tall = force_tall or bh > bw * 1.15
    if tall:
        pad_x = max(10, int(bw * 0.20), int(fw * 0.012))
        pad_y = max(10, int(bh * 0.05), int(fh * 0.008))
        max_w = int(fw * 0.14)
        max_h = int(fh * 0.55)
    else:
        pad_x = max(10, int(fw * 0.012))
        pad_y = max(8, int(fh * 0.008))
        max_w = int(fw * 0.34)
        max_h = int(fh * 0.24)
    out = (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(fw, x1 + pad_x),
        min(fh, y1 + pad_y),
    )
    ow, oh = out[2] - out[0], out[3] - out[1]
    cx, cy = (out[0] + out[2]) // 2, (out[1] + out[3]) // 2
    if ow > max_w:
        half = max_w // 2
        out = (max(0, cx - half), out[1], min(fw, cx + half), out[3])
    if oh > max_h:
        half = max_h // 2
        out = (out[0], max(0, cy - half), out[2], min(fh, cy + half))
    return out


def _split_units(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if "·" in raw or "・" in raw or "," in raw or "，" in raw:
        parts = re.split(r"[·・,，/|]+", raw)
        out: list[str] = []
        for p in parts:
            p = p.strip()
            if p:
                out.extend(_split_units(p) if " " in p else [p])
        return out or [raw]
    if re.search(r"[\u4e00-\u9fff]", raw) and " " not in raw:
        chars = list(re.sub(r"\s+", "", raw))
        return chars if len(chars) > 1 else ["".join(chars)]
    parts = [p for p in re.split(r"\s+", raw) if p]
    return parts or [raw]


def _wrap_units(draw: Any, units: list[str], font: Any, max_w: int) -> list[str]:
    if not units:
        return [""]
    lines: list[str] = []
    cur = units[0]
    for u in units[1:]:
        cjk = any("\u4e00" <= c <= "\u9fff" for c in cur + u)
        trial = f"{cur}{u}" if cjk else f"{cur} {u}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = u
    lines.append(cur)
    return lines


def _load_font(font_path: str, size: int, fallback: Any) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.truetype(font_path, size)
    except OSError:
        return fallback


def layout_label_caption(
    text: str,
    font: Any,
    fontsize: int,
    ocr_box: tuple[int, int, int, int] | None,
    frame_w: int,
    frame_h: int,
    *,
    font_path: str,
    force_vertical: bool = False,
    source: str = "",
) -> dict[str, Any]:
    """Chữ VI fill ô OCR: cột cao / CJK dọc → stack; ngang → wrap."""
    from PIL import Image, ImageDraw

    raw = (text or "").strip()
    empty = {
        "box": ocr_box or (0, 0, 1, 1),
        "lines": [raw or ""],
        "font": font,
        "fontsize": max(14, fontsize),
        "line_h": fontsize,
        "line_hs": [fontsize],
        "gap_line": 2,
        "pad_y": 2,
        "text_h": fontsize,
        "cover_plate": False,
        "vertical": False,
        "label": True,
        "distribute": False,
    }
    if not raw:
        return empty

    if ocr_box is None:
        ocr_box = (
            int(frame_w * 0.35),
            int(frame_h * 0.40),
            int(frame_w * 0.65),
            int(frame_h * 0.55),
        )

    tall = (
        force_vertical
        or is_vertical_cjk_source(source)
        or is_tall_label(ocr_box)
    )
    units = _split_units(raw)
    probe = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(probe)

    if tall:
        # cột dọc: nếu box đã bị phình ngang, thu hẹp lại theo H
        ocr_w = max(1, ocr_box[2] - ocr_box[0])
        ocr_h = max(1, ocr_box[3] - ocr_box[1])
        if ocr_w > ocr_h * 0.55:
            cx = (ocr_box[0] + ocr_box[2]) // 2
            half = max(22, int(ocr_h * 0.18), int(frame_w * 0.045))
            ocr_box = (
                max(0, cx - half),
                ocr_box[1],
                min(frame_w, cx + half),
                ocr_box[3],
            )
        return _layout_label_vertical(
            raw, units, font, fontsize, ocr_box, frame_w, frame_h, font_path, draw
        )
    return _layout_label_horizontal(
        raw, units, font, fontsize, ocr_box, frame_w, frame_h, font_path, draw
    )


def _layout_label_horizontal(
    raw: str,
    units: list[str],
    font: Any,
    fontsize: int,
    ocr_box: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    font_path: str,
    draw: Any,
) -> dict[str, Any]:
    ocr_w = max(28, ocr_box[2] - ocr_box[0])
    ocr_h = max(24, ocr_box[3] - ocr_box[1])
    # font lớn nhất: ~70% chiều cao ô cho 1 dòng, ~45% cho 2+
    max_fs = max(14, min(int(fontsize * 0.95), int(ocr_h * 0.55), int(ocr_w * 0.38)))
    min_fs = max(12, min(16, max_fs))

    best: dict[str, Any] | None = None
    for size in range(max_fs, min_fs - 1, -1):
        pad_x = max(4, size // 5)
        pad_y = max(3, size // 7)
        gap_line = max(2, size // 9)
        font_use = _load_font(font_path, size, font)
        inner_w = max(10, ocr_w - pad_x * 2)
        cand = _wrap_units(draw, units, font_use, inner_w)
        if len(cand) > 4:
            continue
        lbs = [draw.textbbox((0, 0), ln, font=font_use) for ln in cand]
        tw = max((b[2] - b[0]) for b in lbs) if lbs else 0
        th = sum(max(1, b[3] - b[1]) for b in lbs) + gap_line * max(0, len(cand) - 1)
        if tw + pad_x * 2 <= ocr_w + 2 and th + pad_y * 2 <= ocr_h + 2:
            line_hs = [max(1, b[3] - b[1]) for b in lbs]
            best = {
                "box": ocr_box,
                "lines": cand,
                "font": font_use,
                "fontsize": size,
                "line_h": (max(line_hs) if line_hs else size) + gap_line,
                "line_hs": line_hs,
                "gap_line": gap_line,
                "pad_y": pad_y,
                "text_h": th,
                "cover_plate": False,
                "vertical": False,
                "label": True,
                "distribute": False,
            }
            break
    if best:
        return best
    # fallback min size
    size = min_fs
    font_use = _load_font(font_path, size, font)
    pad_x, pad_y, gap_line = max(3, size // 5), max(2, size // 8), max(1, size // 10)
    cand = _wrap_units(draw, units, font_use, max(10, ocr_w - pad_x * 2))[:4]
    lbs = [draw.textbbox((0, 0), ln, font=font_use) for ln in cand]
    line_hs = [max(1, b[3] - b[1]) for b in lbs]
    th = sum(line_hs) + gap_line * max(0, len(cand) - 1)
    return {
        "box": ocr_box,
        "lines": cand,
        "font": font_use,
        "fontsize": size,
        "line_h": (max(line_hs) if line_hs else size) + gap_line,
        "line_hs": line_hs or [size],
        "gap_line": gap_line,
        "pad_y": pad_y,
        "text_h": th,
        "cover_plate": False,
        "vertical": False,
        "label": True,
        "distribute": False,
    }


def _layout_label_vertical(
    raw: str,
    units: list[str],
    font: Any,
    fontsize: int,
    ocr_box: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    font_path: str,
    draw: Any,
) -> dict[str, Any]:
    """Cột dọc: 1 từ/dòng, font lớn theo bề ngang cột, giãn đều chiều cao."""
    ocr_w = max(24, ocr_box[2] - ocr_box[0])
    ocr_h = max(40, ocr_box[3] - ocr_box[1])
    # đơn vị: từ VI (đã split space) — không tách từng ký tự Latin
    lines = units if units else [raw]
    n = max(1, len(lines))
    pad_x = max(3, int(ocr_w * 0.08))
    pad_y = max(4, int(ocr_h * 0.04))
    inner_w = max(12, ocr_w - pad_x * 2)
    inner_h = max(20, ocr_h - pad_y * 2)

    max_fs = max(14, min(int(fontsize * 0.9), int(inner_w * 0.92), int(inner_h / n * 0.85)))
    min_fs = max(11, min(14, max_fs))

    best_size = min_fs
    font_use = _load_font(font_path, min_fs, font)
    line_hs: list[int] = []
    for size in range(max_fs, min_fs - 1, -1):
        fu = _load_font(font_path, size, font)
        lbs = [draw.textbbox((0, 0), ln, font=fu) for ln in lines]
        # nếu từ quá rộng → thu
        if any(b[2] - b[0] > inner_w for b in lbs):
            continue
        lhs = [max(1, b[3] - b[1]) for b in lbs]
        sum_h = sum(lhs)
        if sum_h <= inner_h:
            best_size = size
            font_use = fu
            line_hs = lhs
            break
        line_hs = lhs
        font_use = fu
        best_size = size

    if not line_hs:
        lbs = [draw.textbbox((0, 0), ln, font=font_use) for ln in lines]
        line_hs = [max(1, b[3] - b[1]) for b in lbs]

    sum_h = sum(line_hs)
    gap = 0
    if n > 1:
        gap = max(2, int((inner_h - sum_h) / (n - 1)))
        # kẹp gap để không loãng quá
        gap = min(gap, max(best_size, int(inner_h * 0.2)))
    text_h = sum_h + gap * max(0, n - 1)
    return {
        "box": ocr_box,
        "lines": lines,
        "font": font_use,
        "fontsize": best_size,
        "line_h": (max(line_hs) if line_hs else best_size) + gap,
        "line_hs": line_hs,
        "gap_line": gap,
        "pad_y": pad_y,
        "text_h": text_h,
        "cover_plate": False,
        "vertical": True,
        "label": True,
        "distribute": True,
    }
