"""OCR định vị hardsub / nhãn / title dọc trên khung (dùng lúc burn)."""
from __future__ import annotations

from typing import Any

from .extract import _ocr_join_lines, _rapidocr_labels
from .labels import pick_label_box


def _source_matches(text: str, source: str) -> bool:
    """Khớp OCR với source; không cho 1 glyph chung kéo cả box nhiễu vào cue dài."""
    tx = "".join((text or "").split())
    src = "".join((source or "").split())
    if not tx or not src:
        return False
    if tx == src or src in tx:
        return True
    tc = {c for c in tx if "\u4e00" <= c <= "\u9fff"}
    sc = {c for c in src if "\u4e00" <= c <= "\u9fff"}
    if tx in src and (len(tc) >= 2 or len(sc) <= 2):
        return True
    overlap = len(tc & sc)
    need = 1 if len(sc) <= 2 else 2
    return overlap >= need and overlap / max(1, min(len(tc), len(sc))) >= 0.5


def rapidocr_labels() -> Any:
    """OCR lỏng cho nhãn / 1 chữ — default RapidOCR bỏ sót glyph nhỏ."""
    return _rapidocr_labels()


def ocr_mid_labels(
    frame_bgr: Any,
    ocr: Any,
    source: str = "",
) -> tuple[list[tuple[int, int, int, int]], str]:
    """OCR nhãn nhỏ / graphic (bỏ hardsub đáy + title dọc full giữa)."""
    h, w = frame_bgr.shape[:2]
    y1 = int(h * 0.82)
    roi = frame_bgr[0:y1, :]
    if roi.size == 0:
        return [], ""
    # dùng engine truyền vào — không tạo RapidOCR mới mỗi khung
    eng = ocr if ocr is not None else rapidocr_labels()
    result, _ = eng(roi)
    boxes: list[tuple[int, int, int, int]] = []
    texts: list[str] = []
    src = (source or "").strip()
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if cjk < 1 and len(text) < 2:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bx0, by0 = int(min(xs)), int(min(ys))
            bx1, by1 = int(max(xs)), int(max(ys))
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if cjk == 1:
            if bw < max(10, w * 0.012) or bh < max(10, h * 0.012):
                continue
        elif bw < 4 or bh < 4:
            continue
        cx, cy = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5
        if cy > h * 0.72 and bw > w * 0.28 and bh < h * 0.08:
            continue
        # title dọc full giữa (mở clip) — pass vertical lo; cột nhãn hẹp vẫn giữ
        if bh > h * 0.45 and bh > bw * 1.8 and w * 0.35 < cx < w * 0.65:
            continue
        # cột dọc nguyên liệu: cao hẹp — bắt cả box từng chữ (sẽ gộp sau)
        tall_col = bh > bw * 1.15 and bw < w * 0.25 and bh < h * 0.55
        side = cx < w * 0.42 or cx > w * 0.68
        small = bw < w * 0.45 and bh < h * 0.28
        single = cjk >= 1 and len(text.strip()) <= 6 and bw < w * 0.35 and bh < h * 0.22
        mid_g = (
            h * 0.20 < cy < h * 0.72
            and bw < w * 0.55
            and bh < h * 0.30
            and cjk <= 14
        )
        matched = bool(src) and _source_matches(text, src)
        if not (side or small or single or matched or tall_col or mid_g):
            continue
        # nới nhẹ bbox OCR (unclip hay cắt stroke)
        pad = max(4, int(min(bw, bh) * 0.12))
        boxes.append(
            (
                max(0, bx0 - pad),
                max(0, by0 - pad),
                min(w, bx1 + pad),
                min(h, by1 + pad),
            )
        )
        texts.append(text)
    if not boxes:
        return [], ""
    if src:
        matched_boxes = [
            (b, t)
            for b, t in zip(boxes, texts)
            if _source_matches(t, src)
        ]
        if matched_boxes:
            boxes = [b for b, _ in matched_boxes]
            texts = [t for _, t in matched_boxes]
        else:
            return [], ""
    # gộp chữ cùng cột trước khi trả
    from .labels import expand_label_column

    boxes = expand_label_column(boxes, w, h)
    return boxes, _ocr_join_lines(texts)


def ocr_mid_vertical(
    frame_bgr: Any, ocr: Any, source: str = ""
) -> tuple[list[tuple[int, int, int, int]], str]:
    """OCR vùng giữa khung cho chữ dọc (tiêu đề)."""
    import cv2

    h, w = frame_bgr.shape[:2]
    x0, x1 = int(w * 0.05), int(w * 0.75)
    y0, y1 = int(h * 0.12), int(h * 0.88)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return [], ""
    scale = 1.5
    img = cv2.resize(roi, (int(roi.shape[1] * scale), int(roi.shape[0] * scale)))
    result, _ = ocr(img)
    found: list[tuple[tuple[int, int, int, int], str]] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        try:
            xs = [float(p[0]) / scale + x0 for p in box]
            ys = [float(p[1]) / scale + y0 for p in box]
            bx0, by0 = int(min(xs)), int(min(ys))
            bx1, by1 = int(max(xs)), int(max(ys))
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 4 or bh < 4:
            continue
        if bh < bw * 0.9 and len(text) > 6:
            continue
        found.append(((bx0, by0, bx1, by1), text))
    if not found:
        return [], ""

    src = (source or "").strip()
    anchors = [
        (box, text)
        for box, text in found
        if not src or _source_matches(text, src)
    ]
    if src and anchors:
        # Giữ thêm cột Latin sát title (HUAMUZ), nhưng bỏ hardsub ngang ở giữa
        # khung. Trước đây union tất cả kết quả làm bbox lệch phải và cao quá mức.
        selected = list(anchors)
        for box, text in found:
            if (box, text) in selected:
                continue
            bx0, by0, bx1, by1 = box
            bw, bh = bx1 - bx0, by1 - by0
            if bh <= bw * 1.35:
                continue
            for ab, _at in anchors:
                ax0, ay0, ax1, ay1 = ab
                horizontal_gap = max(0, ax0 - bx1, bx0 - ax1)
                vertical_gap = max(0, ay0 - by1, by0 - ay1)
                if horizontal_gap <= w * 0.08 and vertical_gap <= h * 0.08:
                    selected.append((box, text))
                    break
        found = selected
    elif src:
        return [], ""

    boxes = [box for box, _text in found]
    texts = [text for _box, text in found]
    return boxes, _ocr_join_lines(texts)


def ocr_mid_hardsub_boxes(
    frame_bgr: Any,
    ocr: Any,
    source: str = "",
) -> tuple[list[tuple[int, int, int, int]], str]:
    """Định vị hardsub ngắn giữa khung (1–4 CJK)."""
    h, w = frame_bgr.shape[:2]
    y0, y1 = int(h * 0.25), int(h * 0.75)
    x0, x1 = int(w * 0.15), int(w * 0.85)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return [], ""
    eng = ocr if ocr is not None else rapidocr_labels()
    result, _ = eng(roi)
    src = (source or "").strip()
    boxes: list[tuple[int, int, int, int]] = []
    texts: list[str] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if cjk < 1:
            continue
        if src and not _source_matches(text, src):
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bx0 = x0 + int(min(xs)) - 8
            by0 = y0 + int(min(ys)) - 6
            bx1 = x0 + int(max(xs)) + 8
            by1 = y0 + int(max(ys)) + 6
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 8 or bh < 8:
            continue
        if bh > h * 0.18:
            continue
        boxes.append((max(0, bx0), max(0, by0), min(w, bx1), min(h, by1)))
        texts.append(text)
    if not boxes:
        return [], ""
    pick = pick_label_box(boxes, texts, src, w, h)
    return ([pick] if pick else boxes[:1]), (texts[0] if texts else src)
