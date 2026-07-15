"""OCR định vị hardsub / nhãn / title dọc trên khung (dùng lúc burn)."""
from __future__ import annotations

from pathlib import Path
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
    tc = {c for c in tx if 0x4E00 <= ord(c) <= 0x9FFF}
    sc = {c for c in src if 0x4E00 <= ord(c) <= 0x9FFF}
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
        cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)
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
    """OCR caption ngang bất kỳ Y (giữa hoặc đáy) — khớp source, không hardcode vị trí.

    Ưu tiên dòng đủ glyph/trái-phải (không lấy mảnh OCR lệch một bên).
    """
    hit = _probe_mid_hardsub(frame_bgr, ocr, source=source)
    if not hit:
        return [], ""
    _score, box, text = hit
    return [box], text


def _probe_mid_hardsub(
    frame_bgr: Any,
    ocr: Any,
    source: str = "",
) -> tuple[float, tuple[int, int, int, int], str] | None:
    """Trả (score, xyxy, text) — score cao = khớp source + đủ bề ngang."""
    h, w = frame_bgr.shape[:2]
    y0, y1 = int(h * 0.12), int(h * 0.92)
    x0, x1 = int(w * 0.04), int(w * 0.96)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    eng = ocr if ocr is not None else rapidocr_labels()
    result, _ = eng(roi)
    src = (source or "").strip()
    src_cjk = _cjk_len(src)
    cands: list[tuple[float, tuple[int, int, int, int], str]] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)
        if cjk < 1 or cjk > 28:
            continue
        # mảnh quá thiếu so với Whisper — bỏ; dòng dài hơn thì GIỮ (Whisper cắt nửa hardsub)
        if src_cjk >= 3 and cjk < max(2, int(src_cjk * 0.55)):
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            # pad nhẹ — vừa stroke, không phình theo VI
            bx0 = x0 + int(min(xs)) - 10
            by0 = y0 + int(min(ys)) - 8
            bx1 = x0 + int(max(xs)) + 10
            by1 = y0 + int(max(ys)) + 8
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 8 or bh < 8:
            continue
        if bh > h * 0.24:
            continue
        if bh > bw * 1.35:
            continue
        cy = (by0 + by1) * 0.5
        if not (h * 0.12 < cy < h * 0.92):
            continue
        # quá hẹp so với Whisper — bỏ; rộng hơn OK (che full dòng trên khung)
        if src_cjk >= 3 and bw < src_cjk * max(28, int(w * 0.028)):
            continue
        bb = (max(0, bx0), max(0, by0), min(w, bx1), min(h, by1))
        score = float(cjk) + (bw / max(1, w)) * 4
        if src:
            from .extract import _ocr_sim

            if _source_matches(text, src):
                score = 30 + cjk + (bw / max(1, w)) * 6
            # ưu tiên dòng hardsub đủ bề ngang khi Whisper chỉ có nửa câu
            if cjk > src_cjk + 1:
                score += 14 + (bw / max(1, w)) * 10
            else:
                sim = float(_ocr_sim(text, src) or 0)
                min_sim = 0.28 if src_cjk <= 8 else 0.42
                if sim < min_sim:
                    continue
                score = sim * 22 + cjk - abs(cjk - src_cjk) * 2
                score += (bw / max(1, w)) * 6
            if cjk == src_cjk:
                score += 10
            elif abs(cjk - src_cjk) <= 1:
                score += 4
        cands.append((score, bb, text))
    if not cands:
        return None
    cands.sort(key=lambda x: -x[0])
    return cands[0]


def _bbox_fits_source(bb: dict[str, Any], source: str, fw: int) -> bool:
    """False nếu bbox quá hẹp so với CJK. Rộng hơn OK — che full dòng hardsub."""
    cjk = _cjk_len(source)
    if cjk < 2:
        return True
    try:
        w = float(bb.get("w") or 0)
    except (TypeError, ValueError):
        return False
    lo = cjk * max(28.0, fw * 0.025)
    return w >= lo


def _xyxy_to_seg_bbox(
    x0: int, y0: int, x1: int, y1: int, fw: int, fh: int, pad: int = 6,
    *,
    pad_x: int | None = None,
    pad_y: int | None = None,
) -> dict[str, int]:
    px = pad if pad_x is None else pad_x
    py = pad if pad_y is None else pad_y
    x0 = max(0, x0 - px)
    y0 = max(0, y0 - py)
    x1 = min(fw, x1 + px)
    y1 = min(fh, y1 + py)
    return {
        "x": int(x0),
        "y": int(y0),
        "w": max(8, int(x1 - x0)),
        "h": max(8, int(y1 - y0)),
    }


def _bbox_cy_frac(bbox: dict[str, Any] | None, fh: int) -> float | None:
    if not isinstance(bbox, dict) or fh <= 0:
        return None
    try:
        y = float(bbox.get("y") or 0)
        h = float(bbox.get("h") or 0)
    except (TypeError, ValueError):
        return None
    return (y + h * 0.5) / fh


def _cjk_len(text: str) -> int:
    return sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)


def _similar_hardsub_len(a: str, b: str) -> bool:
    """Whisper phồn thể / OCR giản / lệch 1 glyph — vẫn cùng độ dài khoảng."""
    ca, cb = _cjk_len(a), _cjk_len(b)
    if ca < 1 or cb < 1:
        return False
    return abs(ca - cb) <= max(1, ca // 3)


def _layout_from_cy(cy: float, fh: int) -> str:
    """mid = hardsub giữa/giữa-dưới khung; horizontal = đáy cổ điển."""
    if fh <= 0:
        return "horizontal"
    # 0.78: TikTok mid thường thấp hơn 0.70 — không đẩy nhầm vào Caption
    if fh * 0.18 < cy < fh * 0.78:
        return "mid"
    return "horizontal"


def _retag_layout_from_bbox(seg: dict[str, Any], fh: int) -> None:
    """Gắn layout theo bbox đã đo — không để None/horizontal khi chữ ở giữa."""
    lay = str(seg.get("layout") or "")
    if lay in ("vertical", "label"):
        return
    cy = _bbox_cy_frac(seg.get("bbox") if isinstance(seg.get("bbox"), dict) else None, fh)
    if cy is None:
        return
    seg["layout"] = _layout_from_cy(cy * fh, fh)


def _apply_caption_box(
    seg: dict[str, Any],
    box: tuple[int, int, int, int],
    fw: int,
    fh: int,
) -> None:
    """Gắn bbox OCR; layout tag chỉ theo cy đo được."""
    x0, y0, x1, y1 = box
    cy = (y0 + y1) * 0.5
    # pad X đủ stroke/glow; Y sát glyph — tránh hộp cao chữ bé
    pad_x = max(10, int(round(fw * 0.01)))
    pad_y = max(6, int(round(fh * 0.004)))
    seg["bbox"] = _xyxy_to_seg_bbox(x0, y0, x1, y1, fw, fh, pad_x=pad_x, pad_y=pad_y)
    seg["layout"] = _layout_from_cy(cy, fh)
    seg.pop("captionLayout", None)


def _inherit_caption_bboxes(segments: list[dict[str, Any]], fh: int) -> int:
    """ponytail: OCR fail 1 câu → mượn bbox câu kề (cùng video hay cùng dải). Ceiling: video đổi vị trí giữa shot."""
    n = 0
    caps: list[dict[str, int] | None] = []
    for seg in segments:
        bb = seg.get("bbox")
        lay = str(seg.get("layout") or "")
        cy = _bbox_cy_frac(bb if isinstance(bb, dict) else None, fh)
        if lay in ("mid", "horizontal") and isinstance(bb, dict) and cy is not None:
            caps.append(
                {
                    "x": int(bb["x"]),
                    "y": int(bb["y"]),
                    "w": int(bb["w"]),
                    "h": int(bb["h"]),
                }
            )
        else:
            caps.append(None)

    def nearest(i: int) -> dict[str, int] | None:
        for d in range(1, len(segments)):
            for j in (i - d, i + d):
                if 0 <= j < len(caps) and caps[j] is not None:
                    return caps[j]
        return None

    for i, seg in enumerate(segments):
        lay = str(seg.get("layout") or "horizontal")
        if lay in ("vertical", "label"):
            continue
        src = str(seg.get("source") or "")
        if _cjk_len(src) < 1:
            continue
        bb = seg.get("bbox")
        if isinstance(bb, dict) and bb.get("w") and bb.get("h"):
            continue
        donor = nearest(i)
        if not donor:
            continue
        # mượn nguyên bbox mid neighbor — không scale hẹp (che sai nửa hardsub)
        cy = donor["y"] + donor["h"] * 0.5
        seg["bbox"] = dict(donor)
        seg["layout"] = _layout_from_cy(cy, fh)
        seg.pop("captionLayout", None)
        n += 1
    return n


def attach_speech_hardsub_boxes(
    video: Path | str,
    segments: list[dict[str, Any]],
    *,
    only_missing: bool = True,
    project_id: str | None = None,
) -> int:
    """Whisper = gợi ý thời gian; OCR đo trái/phải + tinh chỉnh start/end theo lúc chữ trên khung.

    Không nới bbox theo bản dịch — chỉ poly OCR khớp đủ dòng CJK.
    """
    import cv2

    path = Path(video)
    if not path.is_file() or not segments:
        return 0
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return 0
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fw <= 0 or fh <= 0:
        cap.release()
        return 0

    # Sửa layout lệch từ lần trước (bbox mid mà layout trống/horizontal)
    for seg in segments:
        _retag_layout_from_bbox(seg, fh)

    filtered: list[dict[str, Any]] = []
    # ponytail: cache frame theo grid 0.5s — giảm seek/đecode từ ~10k → ~4k cho 2h video.
    # Ceiling: motion nhanh có thể nhầm frame giữa 2 bucket; chỉ dùng cho giai đoạn seeds/walk.
    _frame_cache: dict[tuple[int, int], Any] = {}
    _CACHE_GRID = 0.5
    for seg in segments:
        lay = str(seg.get("layout") or "horizontal")
        if lay in ("vertical", "label"):
            continue
        src = str(seg.get("source") or "")
        if _cjk_len(src) < 1:
            continue
        bb = seg.get("bbox")
        if only_missing and isinstance(bb, dict) and bb.get("w") and bb.get("h"):
            cy = _bbox_cy_frac(bb, fh)
            # giữ nếu đã có bbox đủ ngang; bbox hẹp/lệch → OCR lại
            if cy is not None and _bbox_fits_source(bb, src, fw):
                _retag_layout_from_bbox(seg, fh)
                continue
        filtered.append(seg)
    if not filtered:
        n_inh = _inherit_caption_bboxes(segments, fh)
        cap.release()
        return n_inh

    try:
        ocr = rapidocr_labels()
    except ImportError:
        cap.release()
        return _inherit_caption_bboxes(segments, fh)

    def _read_probe(
        t: float, src: str
    ) -> tuple[float, float, tuple[int, int, int, int], str] | None:
        # Cache theo bucket 0.5s: hai timestamp cùng bucket trả cùng frame
        key = int(t // _CACHE_GRID)
        frame = _frame_cache.get(key)
        if frame is None:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
            ok, frame = cap.read()
            if not ok:
                return None
            _frame_cache[key] = frame
        hit = _probe_mid_hardsub(frame, ocr, source=src)
        if not hit:
            return None
        score, box, text = hit
        return (float(t), float(score), box, text)

    def _report(done: int, total: int) -> None:
        if not project_id or total <= 0:
            return
        # cập nhật theo số câu — không khóa theo progress 95..98 (0/67 và 20/67 cùng pct)
        prev = getattr(_report, "_prev_done", -1)
        if done == prev:
            return
        _report._prev_done = done
        pct = 95 + min(3, int(3 * done / total))
        from ..core.project import set_status

        set_status(
            project_id,
            step="translate",
            progress=pct,
            message=f"Định vị OCR {done}/{total} câu…",
            running=True,
        )

    attached = 0
    total = len(filtered)
    try:
        for si, seg in enumerate(filtered):
            # báo trước khi OCR câu này — UI thấy 1/67 ngay, không đứng 0/67
            _report(si + 1, total)
            s0 = float(seg.get("start") or 0)
            e0 = float(seg.get("end") or s0)
            if e0 <= s0:
                e0 = s0 + 0.4
            src = str(seg.get("source") or "").strip()
            dur = max(0.2, e0 - s0)
            # 1) ít mốc — dừng sớm khi khớp chắc
            seeds = [s0 + dur * f for f in (0.45, 0.70, 0.25)]
            hits: list[tuple[float, float, tuple[int, int, int, int], str]] = []
            for t in seeds:
                p = _read_probe(t, src)
                if not p:
                    continue
                hits.append(p)
                if p[1] >= 35:
                    break
            # 2) nới tìm nếu Whisper lệch
            if not hits:
                for t in (s0 - 0.45, e0 + 0.35, s0 + dur * 0.55):
                    if t < 0:
                        continue
                    p = _read_probe(t, src)
                    if p:
                        hits.append(p)
                        break
            # 3) lỏng: cùng độ dài CJK
            if not hits and src:
                for t in seeds[:2]:
                    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                    ok, frame = cap.read()
                    if not ok:
                        continue
                    loose = _probe_mid_hardsub(frame, ocr, source="")
                    if not loose:
                        continue
                    sc, box, tx = loose
                    if _similar_hardsub_len(tx or "", src) and _bbox_fits_source(
                        {"w": box[2] - box[0], "h": box[3] - box[1]}, src, fw
                    ):
                        hits.append((t, sc * 0.5, box, tx))
                        break
            if not hits:
                continue

            # walk biên ngắn — đủ tune; tránh OCR hàng chục khung/câu
            best = max(hits, key=lambda x: x[1])
            t_anchor = best[0]
            # ponytail: 2 bước thay vì 4 (chỉ ±0.4), vẫn đủ tune cho caption đứng yên
            for step in (-0.4, 0.4):
                t = t_anchor + step
                if t < max(0.0, s0 - 0.8) or t > e0 + 0.8:
                    continue
                p = _read_probe(t, src)
                if p:
                    hits.append(p)

            times = sorted({round(h[0], 3) for h in hits})
            chosen = None
            for h in sorted(hits, key=lambda x: -x[1]):
                box = h[2]
                probe_bb = {"w": box[2] - box[0], "h": box[3] - box[1]}
                if _bbox_fits_source(probe_bb, src, fw):
                    chosen = h
                    break
            if chosen is None:
                continue
            best = chosen
            new_s = max(0.0, times[0])
            new_e = max(new_s + 0.25, times[-1] + 0.12)
            new_s = max(s0 - 1.2, min(s0 + 1.0, new_s))
            new_e = max(e0 - 1.0, min(e0 + 1.2, new_e))
            if new_e <= new_s + 0.2:
                new_s, new_e = s0, e0
            seg["start"] = round(new_s, 3)
            seg["end"] = round(new_e, 3)
            _apply_caption_box(seg, best[2], fw, fh)
            attached += 1
            # Xóa: _report(total, total) — bug: args ngược, vô dụng
    finally:
        cap.release()
    attached += _inherit_caption_bboxes(segments, fh)
    for seg in segments:
        _retag_layout_from_bbox(seg, fh)
    return attached
