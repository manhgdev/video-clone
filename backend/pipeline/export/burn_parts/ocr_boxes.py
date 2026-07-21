"""Hardsub cover + caption burn — ocr_boxes."""
from __future__ import annotations

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
from pipeline.core.resources import adaptive_workers, progress_msg
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

from .layout_geo import _cover_box_fit, _cover_max_h, _tight_cover_box
from pipeline.export.cover_mask import _apply_cover_mask, _blur_region

def _blur_hardsubs(
    frame_bgr: Any, boxes: list[tuple[int, int, int, int]]
) -> Any:
    """Che hardsub sát chữ: blur ROI hẹp (không quét nửa khung)."""
    h, w = frame_bgr.shape[:2]
    box = _tight_cover_box(frame_bgr, boxes if boxes else None)
    if box is None:
        return frame_bgr
    return _blur_region(frame_bgr, box)


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
                    from pipeline.ocr.labels import expand_label_column, union_boxes

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
        from pipeline.ocr.labels import expand_label_column

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
    """1–cap theo setting; 0 = auto CPU (paint/blur). Job GPU dùng gpu_job_cap riêng."""
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
                    message=progress_msg("Định vị chữ", done, n, workers=workers),
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
    """Che hardsub: cover=True → tất cả; cover=False (below/above) → chỉ watermark dọc/nhãn.

    Mid/horizontal khi below/above: không che (chữ dịch neo phía trên/dưới OCR).
    """
    if cover:
        return True
    return (layout or "horizontal") in ("vertical", "label")


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


__all__ = [
    '_clamp_label_box',
    '_pick_label_box',
    '_ocr_mid_labels',
    '_ocr_mid_vertical',
    '_ocr_mid_hardsub_boxes',
    '_blur_hardsubs',
    '_is_corner_ui_box',
    '_filter_subtitle_boxes',
    '_merge_ocr_samples',
    '_ocr_cue_boxes',
    '_resolve_workers',
    '_precompute_cue_boxes',
    '_fill_hardsub_flat',
    '_erase_hardsubs',
    '_ocr_norm',
    '_ocr_matches_subtitle',
    '_ocr_subtitle_boxes',
    '_row_ink_mask',
    '_widen_boxes_to_ink',
    '_merge_boxes',
    '_union_box',
    '_expand_vertical_watermark_cover',
    '_segment_bbox_override',
    '_box_cy',
    '_bbox_looks_bottom',
    '_editor_layout_locked',
    '_should_paint_cover_mask',
    '_stored_cover_should_relocate',
    '_caption_layout_looks_bottom',
    '_hardsub_ink_mask',
    '_cover_box_from_ink',
    '_ocr_band_subs',
    '_match_cue_index',
]
