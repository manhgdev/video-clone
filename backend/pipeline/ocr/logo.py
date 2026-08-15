"""Detect one persistent edge logo from a few OCR anchor frames."""
from __future__ import annotations

import math
from pathlib import Path
from statistics import median
from typing import Any

from pipeline.core.jobs import check_cancel

_LOGO_DETECTION_VERSION = 1


def _branding_text(text: str) -> bool:
    """Only promote explicit watermark-like text to a moving logo track.

    A normal subtitle can sit at the edge too.  The static detector below is
    still useful for graphical logos, while this deliberately conservative
    path handles the common ``@account`` / ``AI generated`` watermarks.
    """
    compact = "".join(str(text or "").split()).casefold()
    return compact.startswith("@") or "生成" in compact


def _padded_normalized_box(
    box: tuple[int, int, int, int], fw: int, fh: int
) -> dict[str, float]:
    x0, y0, x1, y1 = box
    pad_x = max(4, round((x1 - x0) * 0.15))
    pad_y = max(4, round((y1 - y0) * 0.18))
    x0, y0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
    x1, y1 = min(fw, x1 + pad_x), min(fh, y1 + pad_y)
    return {
        "x": round(x0 / fw, 6),
        "y": round(y0 / fh, 6),
        "w": round((x1 - x0) / fw, 6),
        "h": round((y1 - y0) / fh, 6),
    }


def _moving_branding_tracks(
    samples: list[list[dict[str, Any]]], times: list[float], fw: int, fh: int
) -> list[dict[str, Any]]:
    """Return short, time-bound masks for watermarks that change position."""
    if not samples or len(samples) != len(times):
        return []
    detections: list[tuple[int, dict[str, Any]]] = []
    for sample_index, items in enumerate(samples):
        for item in items:
            if _branding_text(str(item.get("text") or "")):
                detections.append((sample_index, item))
    if not detections:
        return []

    def key(item: dict[str, Any]) -> str:
        text = str(item.get("text") or "").strip()
        # A handle may have one OCR-misread glyph as it moves; it is still the
        # same platform watermark for tracking purposes.
        return "@handle" if text.startswith("@") else "generated"

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for detection in detections:
        grouped.setdefault(key(detection[1]), []).append(detection)

    probe_gap = (times[1] - times[0]) if len(times) > 1 else 0.25
    tracks: list[dict[str, Any]] = []
    for group in grouped.values():
        group.sort(key=lambda item: item[0])
        runs: list[list[tuple[int, dict[str, Any]]]] = []
        for item in group:
            if not runs or times[item[0]] - times[runs[-1][-1][0]] > probe_gap * 2.2:
                runs.append([item])
            else:
                runs[-1].append(item)
        for run in runs:
            # A one-frame OCR false-positive must not create a logo mask.
            if len(run) < 2:
                continue
            for index, (sample_index, item) in enumerate(run):
                prev_index = run[index - 1][0] if index else sample_index
                next_index = run[index + 1][0] if index + 1 < len(run) else sample_index
                # A brand visible in the very first probe can already be on
                # frame 0.  Do not leave the initial half-probe uncovered.
                start = (
                    0.0
                    if index == 0 and sample_index == 0
                    else max(0.0, times[sample_index] - probe_gap * 0.5)
                ) if index == 0 else (times[prev_index] + times[sample_index]) * 0.5
                end = min(times[-1] + probe_gap * 0.5, times[sample_index] + probe_gap * 0.5) if index + 1 == len(run) else (times[sample_index] + times[next_index]) * 0.5
                # Cover the union between adjacent positions.  This costs a little
                # more background area, but prevents a fast TikTok watermark from
                # escaping between two otherwise correct tracking probes.
                boxes = [item["box"]]
                if index + 1 < len(run):
                    boxes.append(run[index + 1][1]["box"])
                x0 = min(box[0] for box in boxes)
                y0 = min(box[1] for box in boxes)
                x1 = max(box[2] for box in boxes)
                y1 = max(box[3] for box in boxes)
                tracks.append(
                    {
                        "start": round(start, 3),
                        "end": round(max(start + 0.04, end), 3),
                        "bbox": _padded_normalized_box((x0, y0, x1, y1), fw, fh),
                        "text": str(item.get("text") or ""),
                        "confidence": round(float(item.get("confidence") or 0.0), 4),
                    }
                )
    return tracks


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
    texts = [str(item["text"]) for item in best]
    text = max(texts, key=lambda value: (texts.count(value), len(value)))
    return {
        "version": _LOGO_DETECTION_VERSION,
        "bbox": _padded_normalized_box((int(x0), int(y0), int(x1), int(y1)), fw, fh),
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
    # More probes are needed for platform watermarks which deliberately move
    # around the frame.  Keep long videos bounded to avoid slowing export.
    # Short clips are the common preview/export case.  Probe them densely so a
    # bouncing platform watermark cannot move to an uncovered position.
    probe_count = min(81, max(12, int(math.ceil(duration / 0.25)) + 1)) if duration <= 30 else (12 if duration <= 90 else (5 if duration < 600 else 7))
    edge = min(0.5, duration * 0.025)
    if probe_count == 1:
        times = [duration * 0.5]
    else:
        times = [edge + (duration - edge * 2) * index / (probe_count - 1) for index in range(probe_count)]
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
    requested = [max(0, int(round(t * fps))) for t in times]
    time_by_frame = {frame_index: time for frame_index, time in zip(requested, times)}
    sample_by_frame: dict[int, list[dict[str, Any]]] = {}
    for frame_index, frame in (
        _decode_frames_batch(path, times, fps, fw, fh, use_cuda=use_cuda)
    ):
        check_cancel(project_id)
        sample_by_frame[frame_index] = _logo_candidates(
            frame, ocr, frame_index, exclude_texts
        )
    ordered_frames = sorted(time_by_frame)
    times = [time_by_frame[frame_index] for frame_index in ordered_frames]
    samples = [sample_by_frame.get(frame_index, []) for frame_index in ordered_frames]
    static = pick_logo_detection(samples, fw, fh)
    tracks = _moving_branding_tracks(samples, times, fw, fh)
    if not static and not tracks:
        return None
    result: dict[str, Any] = static or {
        "version": _LOGO_DETECTION_VERSION,
        "bbox": None,
        "samples": 0,
        "total": len(samples),
        "confidence": 0.0,
        "text": "",
    }
    if tracks:
        result["tracks"] = tracks
    return result


__all__ = ["detect_logo_bbox_inprocess", "pick_logo_detection"]
