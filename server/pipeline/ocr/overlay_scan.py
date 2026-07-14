"""OCR phụ (giữa / dọc / nhãn) — thưa vùng trống, refine chậm quanh hit.

Coarse thưa (nhanh khi không có chữ). Hit → refine dày hơn ± vùng
để timing + bbox chuẩn. Title dọc: đầu/cuối + refine khi có hit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.jobs import check_cancel
from .extract import (
    _ocr_cluster_hits,
    _ocr_label_items_from_frame,
    _ocr_mid_item_from_frame,
    _ocr_pool_workers,
    _ocr_same,
    _ocr_semaphore,
    _ocr_sim,
    _ocr_vertical_item_from_frame,
    _rapidocr_labels,
)


def adaptive_bottom_fps(video_end: float) -> float:
    """fps trích hardsub đáy — video dài thưa hơn."""
    if video_end >= 3600:
        return 0.5
    if video_end >= 900:
        return 1.0
    return 2.0


def adaptive_overlay_step(video_end: float) -> float:
    """Bước coarse thưa — vùng trống đi nhanh."""
    if video_end >= 1800:
        return 3.5
    if video_end >= 600:
        return 2.8
    if video_end >= 120:
        return 2.2
    return 1.6


def _budget_stamps(video_end: float, *, budget: int = 28) -> list[float]:
    """≤ budget mốc coarse đều clip — ít hơn; refine bổ sung quanh hit."""
    if video_end <= 0.05:
        return []
    n = max(1, min(budget, int(video_end / adaptive_overlay_step(video_end)) + 1))
    if n == 1:
        return [min(0.5, video_end * 0.5)]
    return [round(i * (video_end - 0.05) / (n - 1), 3) for i in range(n)]


def _refine_stamps(
    hits: list[tuple[float, Any, ...]],
    video_end: float,
    *,
    pad: float = 0.45,
    step: float = 0.12,
) -> list[float]:
    """Mốc dày quanh hit — chậm lại 1 chút để bbox/timing chuẩn."""
    if not hits:
        return []
    out: set[float] = set()
    for row in hits:
        t0 = float(row[0])
        t = max(0.0, t0 - pad)
        end = min(video_end, t0 + pad)
        while t <= end + 1e-9:
            out.add(round(t, 3))
            t += step
    return sorted(out)


def _scan_dual_mid_label(
    video: Path,
    stamps: list[float],
    *,
    project_id: str | None,
    workers: int,
) -> tuple[list[tuple[float, str, dict[str, int] | None]], list[tuple[float, str, dict[str, int] | None]]]:
    """1 seek/mốc → mid (1) + nhiều nhãn (bbox từng khối)."""
    if not stamps:
        return [], []
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return [], []
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
    mid_hits: list[tuple[float, str, dict[str, int] | None]] = []
    lab_hits: list[tuple[float, str, dict[str, int] | None]] = []
    _ = min(_ocr_pool_workers(workers, cap=min(2, workers or 1)), 2)
    _tls: Any = type("T", (), {})()
    sem = _ocr_semaphore()

    def _ocr_engine():
        eng = getattr(_tls, "ocr", None)
        if eng is None:
            _tls.ocr = _rapidocr_labels()
            eng = _tls.ocr
        return eng

    try:
        for t in stamps:
            check_cancel(project_id)
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
            ok, frame = cap.read()
            if not ok:
                continue
            snap = frame.copy()
            with sem:
                eng = _ocr_engine()
                try:
                    mid_tx, mid_bb = _ocr_mid_item_from_frame(snap, eng, vw, vh)
                    lab_items = _ocr_label_items_from_frame(snap, eng, vw, vh)
                except Exception:
                    _tls.ocr = _rapidocr_labels(use_cuda=False)
                    eng = _tls.ocr
                    mid_tx, mid_bb = _ocr_mid_item_from_frame(snap, eng, vw, vh)
                    lab_items = _ocr_label_items_from_frame(snap, eng, vw, vh)
            if mid_tx:
                mid_hits.append((t, mid_tx, mid_bb))
            for tx, bb in lab_items:
                lab_hits.append((t, tx, bb))
    finally:
        cap.release()
    return mid_hits, lab_hits


def _vertical_sparse(
    video: Path,
    *,
    project_id: str | None,
    video_end: float,
) -> list[dict[str, Any]]:
    """Title dọc đầu/cuối — coarse nhanh; có hit → refine dày hơn."""
    import cv2

    try:
        ocr = _rapidocr_labels()
    except ImportError:
        return []
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    hits: list[tuple[float, str, dict[str, int] | None]] = []
    try:
        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
        windows: list[tuple[float, float]] = [(0.0, min(3.0, video_end))]
        if video_end > 8.0:
            windows.append((max(0.0, video_end - 2.0), video_end))
        # coarse nhanh
        for w0, w1 in windows:
            t = w0
            while t <= w1 + 1e-6:
                check_cancel(project_id)
                with _ocr_semaphore():
                    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                    ok, frame = cap.read()
                    if not ok:
                        break
                    text, bbox = _ocr_vertical_item_from_frame(frame, ocr, vw, vh)
                if text:
                    hits.append((t, text, bbox))
                t += 0.55
        # có chữ → refine chậm quanh hit
        if hits:
            extra: list[tuple[float, str, dict[str, int] | None]] = []
            seen = {round(h[0], 2) for h in hits}
            for t0, tx0, bb0 in list(hits):
                for d in (-0.35, -0.2, -0.1, 0.1, 0.2, 0.35):
                    t = round(t0 + d, 3)
                    if t < 0 or t > video_end or round(t, 2) in seen:
                        continue
                    with _ocr_semaphore():
                        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                        ok, frame = cap.read()
                        if not ok:
                            continue
                        text, bbox = _ocr_vertical_item_from_frame(frame, ocr, vw, vh)
                    if text and (_ocr_same(text, tx0) or _ocr_sim(text, tx0) >= 0.72):
                        extra.append((t, text, bbox or bb0))
                        seen.add(round(t, 2))
            hits.extend(extra)
            hits.sort(key=lambda x: x[0])
    finally:
        cap.release()
    if not hits:
        return []
    return _ocr_cluster_hits(
        hits,
        video_end=video_end,
        step=0.25,
        layout="vertical",
        gap=0.45,
        min_hold=0.15,
    )


def run_overlay_ocr(
    video: Path,
    *,
    project_id: str | None,
    video_end: float,
    workers: int = 2,
    set_status: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Coarse nhanh vùng trống; hit → refine chậm (chuẩn bbox/timing)."""
    vend = max(0.0, float(video_end))
    coarse = _budget_stamps(vend, budget=28)
    step = adaptive_overlay_step(vend)

    mid: list[dict[str, Any]] = []
    vert: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []

    try:
        if set_status and project_id:
            set_status(
                project_id,
                step="asr",
                progress=38,
                message=f"OCR phụ mid+nhãn coarse ({len(coarse)} mốc)…",
                running=True,
            )
        mid_hits, lab_hits = _scan_dual_mid_label(
            video, coarse, project_id=project_id, workers=workers
        )
        # phần có chữ → scan dày hơn quanh hit
        refine: list[float] = []
        if mid_hits or lab_hits:
            refine = _refine_stamps(
                mid_hits + lab_hits, vend, pad=0.5, step=0.12
            )
            refine = [t for t in refine if t not in set(coarse)]
            if refine:
                if set_status and project_id:
                    set_status(
                        project_id,
                        step="asr",
                        progress=42,
                        message=f"OCR phụ refine ({len(refine)} mốc quanh chữ)…",
                        running=True,
                    )
                m2, l2 = _scan_dual_mid_label(
                    video, refine, project_id=project_id, workers=workers
                )
                mid_hits.extend(m2)
                lab_hits.extend(l2)
                mid_hits.sort(key=lambda x: x[0])
                lab_hits.sort(key=lambda x: x[0])

        cluster_step = 0.15 if (mid_hits or lab_hits) else step
        if mid_hits:
            mid = _ocr_cluster_hits(
                mid_hits,
                video_end=vend,
                step=cluster_step,
                layout="mid",
                gap=max(0.35, cluster_step * 2),
                min_hold=0.2,
            )
        if lab_hits:
            labels = _ocr_cluster_hits(
                lab_hits,
                video_end=vend,
                step=cluster_step,
                layout="label",
                gap=max(0.4, cluster_step * 2),
                min_hold=0.3,
            )
    except Exception:
        mid, labels = [], []

    try:
        if set_status and project_id:
            set_status(
                project_id,
                step="asr",
                progress=50,
                message="OCR phụ: title dọc…",
                running=True,
            )
        vert = _vertical_sparse(video, project_id=project_id, video_end=vend)
    except Exception:
        vert = []

    return mid, vert, labels
