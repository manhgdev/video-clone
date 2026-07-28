"""Detect one persistent edge logo from a few OCR anchor frames."""
from __future__ import annotations

import math
from pathlib import Path
from statistics import median
from typing import Any

from pipeline.core.jobs import check_cancel

_LOGO_DETECTION_VERSION = 1


def _logo_candidates(
    frame_bgr: Any,
    ocr: Any,
    sample: int,
    exclude_texts: set[str] | None = None,
) -> list[dict[str, Any]]:
    h, w = frame_bgr.shape[:2]
    result, _ = ocr(frame_bgr)
    out: list[dict[str, Any]] = []
    for row in result or []:
        try:
            poly, text = row[0], str(row[1] or "").strip()
            confidence = float(row[2]) if len(row) > 2 else 0.5
            xs = [float(point[0]) for point in poly]
            ys = [float(point[1]) for point in poly]
        except (IndexError, TypeError, ValueError):
            continue
        if not text:
            continue
        normalized = "".join(text.lower().split())
        if exclude_texts and (
            normalized in exclude_texts
            or any(
                len(normalized) >= 2
                and (normalized in source or source in normalized)
                for source in exclude_texts
                if len(source) >= 2
            )
        ):
            continue
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        bw, bh = x1 - x0, y1 - y0
        if bw < 4 or bh < 4:
            continue
        area = bw * bh / max(1.0, float(w * h))
        cx, cy = (x0 + x1) * 0.5 / w, (y0 + y1) * 0.5 / h
        edge = cx <= 0.28 or cx >= 0.72 or cy <= 0.20 or cy >= 0.80
        if not edge or area < 0.00008 or area > 0.08 or bw > w * 0.45 or bh > h * 0.30:
            continue
        out.append(
            {
                "box": (int(x0), int(y0), int(x1), int(y1)),
                "text": text,
                "confidence": max(0.0, min(1.0, confidence)),
                "sample": sample,
            }
        )
    return out


def _same_logo_box(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
    fw: int,
    fh: int,
) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    acx, acy = (ax0 + ax1) * 0.5, (ay0 + ay1) * 0.5
    bcx, bcy = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5
    aw, ah = max(1, ax1 - ax0), max(1, ay1 - ay0)
    bw, bh = max(1, bx1 - bx0), max(1, by1 - by0)
    return (
        abs(acx - bcx) <= max(fw * 0.035, max(aw, bw) * 0.45)
        and abs(acy - bcy) <= max(fh * 0.035, max(ah, bh) * 0.45)
        and max(aw, bw) / min(aw, bw) <= 1.8
        and max(ah, bh) / min(ah, bh) <= 1.8
    )


def pick_logo_detection(
    samples: list[list[dict[str, Any]]], fw: int, fh: int
) -> dict[str, Any] | None:
    """Cluster geometrically stable edge OCR boxes and return the best one."""
    clusters: list[list[dict[str, Any]]] = []
    for detections in samples:
        for item in detections:
            match = next(
                (
                    cluster
                    for cluster in clusters
                    if _same_logo_box(cluster[0]["box"], item["box"], fw, fh)
                    and all(existing["sample"] != item["sample"] for existing in cluster)
                ),
                None,
            )
            if match is None:
                clusters.append([item])
            else:
                match.append(item)

    required = max(2, math.ceil(len(samples) * 0.60))
    eligible = [cluster for cluster in clusters if len(cluster) >= required]
    if not eligible:
        return None

    def rank(cluster: list[dict[str, Any]]) -> tuple[float, float, float]:
        boxes = [item["box"] for item in cluster]
        cx = median((box[0] + box[2]) * 0.5 / fw for box in boxes)
        cy = median((box[1] + box[3]) * 0.5 / fh for box in boxes)
        edge_distance = min(cx, 1 - cx, cy, 1 - cy)
        confidence = sum(float(item["confidence"]) for item in cluster) / len(cluster)
        return len(cluster), confidence, -edge_distance

    best = max(eligible, key=rank)
    boxes = [item["box"] for item in best]
    x0, y0 = median(box[0] for box in boxes), median(box[1] for box in boxes)
    x1, y1 = median(box[2] for box in boxes), median(box[3] for box in boxes)
    pad_x = max(4, round((x1 - x0) * 0.15))
    pad_y = max(4, round((y1 - y0) * 0.18))
    x0, y0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
    x1, y1 = min(fw, x1 + pad_x), min(fh, y1 + pad_y)
    texts = [str(item["text"]) for item in best]
    text = max(texts, key=lambda value: (texts.count(value), len(value)))
    return {
        "version": _LOGO_DETECTION_VERSION,
        "bbox": {
            "x": round(x0 / fw, 6),
            "y": round(y0 / fh, 6),
            "w": round((x1 - x0) / fw, 6),
            "h": round((y1 - y0) / fh, 6),
        },
        "samples": len(best),
        "total": len(samples),
        "confidence": round(sum(float(item["confidence"]) for item in best) / len(best), 4),
        "text": text,
    }


def detect_logo_bbox_inprocess(
    video: Path | str,
    *,
    project_id: str | None = None,
    segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    from pipeline.core.runtime_site import ensure_cv2, prepare_cv2_import_path
    from pipeline.ocr.locate import _decode_frames_batch, rapidocr_labels

    prepare_cv2_import_path()
    cv2 = ensure_cv2()
    path = Path(video)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    try:
        fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()
    if fw <= 0 or fh <= 0 or frames <= 0:
        return None
    duration = frames / fps
    fractions = (0.08, 0.50, 0.92) if duration < 600 else (0.05, 0.25, 0.50, 0.75, 0.95)
    times = [duration * fraction for fraction in fractions]
    try:
        from pipeline.core.media import nvdec_available

        use_cuda = bool(nvdec_available(path))
    except Exception:
        use_cuda = False
    ocr = rapidocr_labels()
    exclude_texts = {
        "".join(str(segment.get("source") or "").lower().split())
        for segment in (segments or [])
        if str(segment.get("source") or "").strip()
    }
    samples: list[list[dict[str, Any]]] = []
    for sample, (_idx, frame) in enumerate(
        _decode_frames_batch(path, times, fps, fw, fh, use_cuda=use_cuda)
    ):
        check_cancel(project_id)
        samples.append(_logo_candidates(frame, ocr, sample, exclude_texts))
    return pick_logo_detection(samples, fw, fh)


__all__ = ["detect_logo_bbox_inprocess", "pick_logo_detection"]
