"""OCR overlay — coarse full-ROI thưa; chỗ trống bỏ qua; biên pad từ coarse (0 OCR).

Chữ bất kỳ vị trí trên ROI: classify bbox (mid/vertical/label).
Dọc sticky (xuyên clip): 1 segment + pad biên — không OCR biên từng mốc.
Không lưới refine 0.12s. Hardsub đáy crop riêng (extract).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pathlib import Path
from typing import Any, Callable

from ..core.jobs import check_cancel
from ..core.resources import progress_msg
from .cover_timing import attach_cover_times
from .extract import (
    _merge_label_segments,
    _merge_mid_segments,
    _ocr_cluster_hits,
    _ocr_label_overlap,
    _ocr_overlay_boxes_from_frame,
    _ocr_pick_best,
    _ocr_pool_workers,
    _ocr_same,
    _ocr_seg,
    _ocr_semaphore,
    _ocr_sim,
    _rapidocr_labels,
)

# Biên timing: pad giữa mốc coarse trống↔hit (0 OCR khi đã biết từ quét).
# Fallback 1 probe giữa khoảng nếu mốc ngoài lưới. Mục tiêu nhanh; lệch ~½ step coarse.
_EDGE_EPS = 0.20
_CLUSTER_GAP = 1.5
# Gap rất ngắn cho text hoàn toàn khác — tránh gộp 2 caption riêng chỉ vì gap sát nhau
_CLUSTER_GAP_DIFF = 0.5
# Sticky dọc xuyên clip: ≥3 hit và span ≥5s → 1 segment + tối đa 2 biên
_STICKY_VERT_HITS = 3
_STICKY_VERT_SPAN = 5.0
_VERT_LONG_SEC = _STICKY_VERT_SPAN  # compat test / callers cũ


def adaptive_bottom_fps(video_end: float) -> float:
    """fps trích hardsub đáy — video dài thưa hơn."""
    if video_end >= 3600:
        return 0.5
    if video_end >= 900:
        return 1.0
    return 2.0


def adaptive_overlay_step(video_end: float) -> float:
    """Bước coarse — lướt nhanh chỗ trống; flash rất ngắn (<~0.5s) có thể sót."""
    if video_end >= 1800:
        return 2.2
    if video_end >= 600:
        return 2.0
    if video_end >= 120:
        return 2.0
    return 1.5


def _budget_stamps(video_end: float, *, budget: int = 28) -> list[float]:
    """Mốc coarse đều clip — cap 150 (không nổ refine sau)."""
    if video_end <= 0.05:
        return []
    step = adaptive_overlay_step(video_end)
    n = max(1, min(150, max(budget, int(video_end / step) + 1)))
    if n == 1:
        return [min(0.5, video_end * 0.5)]
    return [round(i * (video_end - 0.05) / (n - 1), 3) for i in range(n)]


Hit = tuple[float, str, dict[str, int] | None]
ProgressCb = Callable[[int, int, str], None] | None


def _cluster_hits(hits: list[Hit], *, gap: float = _CLUSTER_GAP) -> list[list[Hit]]:
    """Gom hit liên tiếp theo thời gian (cùng lane đã lọc sẵn)."""
    if not hits:
        return []
    ordered = sorted(hits, key=lambda h: float(h[0]))
    out: list[list[Hit]] = [[ordered[0]]]
    for row in ordered[1:]:
        if float(row[0]) - float(out[-1][-1][0]) <= gap:
            out[-1].append(row)
        else:
            out.append([row])
    return out


def _cluster_hits_overlay(
    hits: list[Hit],
    *,
    gap: float,
    coarse_step: float,
    layout: str,
) -> list[list[Hit]]:
    """Gom hit — cùng chữ được nới gap; bbox xa nhau → cluster riêng dù cùng t."""
    if not hits:
        return []
    ordered = sorted(hits, key=lambda h: float(h[0]))
    out: list[list[Hit]] = [[ordered[0]]]
    same_gap = max(gap, coarse_step * 1.8)
    for row in ordered[1:]:
        # Tìm cluster gần nhất về bbox (không được theo cluster cuối mà theo bbox lộn)
        placed = False
        dt = float(row[0]) - float(out[-1][-1][0])
        for cluster in reversed(out):  # ưu tiên cluster gần nhất (thường là cuối)
            prev = cluster[-1]
            dt = float(row[0]) - float(prev[0])
            same_text = _ocr_same(prev[1], row[1]) or _ocr_sim(prev[1], row[1]) >= 0.72
            if not same_text and layout == "label":
                same_text = _ocr_label_overlap(prev[1], row[1]) >= 0.5
            # Kiểm tra khoảng cách bbox: nếu 2 hit đều có bbox, và tâm cách xa thì là chữ khác chỗ
            bb_row, bb_prev = row[2], prev[2]
            bbox_far = False
            if bb_row and bb_prev:
                cx_r = bb_row.get("x", 0) + bb_row.get("w", 0) / 2
                cy_r = bb_row.get("y", 0) + bb_row.get("h", 0) / 2
                cx_p = bb_prev.get("x", 0) + bb_prev.get("w", 0) / 2
                cy_p = bb_prev.get("y", 0) + bb_prev.get("h", 0) / 2
                # Dùng 1080x1920 như chuẩn; bbox đã được normalize
                if abs(cx_r - cx_p) > 216 or abs(cy_r - cy_p) > 192:  # >20% W hoặc >10% H
                    bbox_far = True
            if bbox_far:
                # Bbox cách xa: chờ cluster cùng bbox vùng
                continue
            if same_text:
                max_gap_allow = same_gap
            elif _ocr_sim(prev[1], row[1]) < 0.30:
                max_gap_allow = _CLUSTER_GAP_DIFF  # text hoàn toàn khác: gap cực ngắn
            else:
                max_gap_allow = gap
            if dt <= max_gap_allow:
                cluster.append(row)
                placed = True
                break
        if not placed:
            out.append([row])
    return out


def _best_hit(cluster: list[Hit]) -> Hit:
    texts = [h[1] for h in cluster if h[1]]
    best_tx = _ocr_pick_best(texts) if texts else (cluster[0][1] or "")
    bb = None
    t_mid = float(cluster[len(cluster) // 2][0])
    for t, tx, box in cluster:
        if box and (_ocr_same(tx, best_tx) or _ocr_sim(tx, best_tx) >= 0.65):
            bb = box
            t_mid = float(t)
            break
    if bb is None:
        for t, _tx, box in cluster:
            if box:
                bb = box
                t_mid = float(t)
                break
    return (t_mid, best_tx, bb)


def _layout_present(
    boxes: dict[str, list[tuple[str, dict[str, int]]]],
    layout: str,
    seed: str,
) -> bool:
    rows = boxes.get(layout) or []
    if not rows:
        return False
    if not (seed or "").strip():
        return True
    for tx, _bb in rows:
        if _ocr_same(tx, seed) or _ocr_sim(tx, seed) >= 0.55:
            return True
    # cùng layout khác chữ vẫn coi là “có chữ” nếu seed yếu
    return len(seed.strip()) < 2


def _binary_edge_start(
    has_text: Callable[[float], bool],
    t_lo: float,
    t_hi: float,
    *,
    eps: float = _EDGE_EPS,
    max_iters: int = 8,
) -> float:
    """t_lo trống, t_hi có chữ → start (giữ cho test / fallback)."""
    lo, hi = float(t_lo), float(t_hi)
    if hi <= lo + eps:
        return hi
    n = 0
    while hi - lo > eps and n < max_iters:
        mid = (lo + hi) * 0.5
        if has_text(mid):
            hi = mid
        else:
            lo = mid
        n += 1
    return round(hi, 3)


def _binary_edge_end(
    has_text: Callable[[float], bool],
    t_lo: float,
    t_hi: float,
    *,
    eps: float = _EDGE_EPS,
    max_iters: int = 8,
) -> float:
    """t_lo có chữ, t_hi trống → end (giữ cho test / fallback)."""
    lo, hi = float(t_lo), float(t_hi)
    if hi <= lo + eps:
        return lo
    n = 0
    while hi - lo > eps and n < max_iters:
        mid = (lo + hi) * 0.5
        if has_text(mid):
            lo = mid
        else:
            hi = mid
        n += 1
    return round(lo, 3)


def _cheap_edge_start(has_text: Callable[[float], bool], t_lo: float, t_hi: float) -> float:
    """1–2 OCR trong trống→có. Bias sớm: midpoint trống hay để chữ lộ trước bbox."""
    lo, hi = float(t_lo), float(t_hi)
    if hi <= lo + _EDGE_EPS:
        return round(hi, 3)
    mid = (lo + hi) * 0.5
    if has_text(mid):
        q = lo + (hi - lo) * 0.25
        return round(q if has_text(q) else mid, 3)
    # chữ nằm nửa sau — lấy ¾ (sớm hơn nhảy thẳng về hi)
    return round((mid + hi) * 0.5, 3)


def _cheap_edge_end(has_text: Callable[[float], bool], t_lo: float, t_hi: float) -> float:
    lo, hi = float(t_lo), float(t_hi)
    if hi <= lo + _EDGE_EPS:
        return round(lo, 3)
    mid = (lo + hi) * 0.5
    if has_text(mid):
        q = lo + (hi - lo) * 0.75
        return round(q if has_text(q) else mid, 3)
    return round((lo + mid) * 0.5, 3)


def _pad_edge_start(t_lo: float, t_hi: float) -> float:
    """0 OCR — bias sớm trong khe trống→hit (midpoint hay trễ ~½ step coarse)."""
    lo, hi = float(t_lo), float(t_hi)
    if hi <= lo + _EDGE_EPS:
        return round(hi, 3)
    gap = hi - lo
    return round(lo + gap * 0.2, 3)


def _pad_edge_end(t_lo: float, t_hi: float) -> float:
    """0 OCR — bias muộn: chữ thường còn tới gần mốc trống."""
    lo, hi = float(t_lo), float(t_hi)
    if hi <= lo + _EDGE_EPS:
        return round(lo, 3)
    gap = hi - lo
    return round(lo + gap * 0.8, 3)


def _coarse_layout_absent(
    stamp_layouts: dict[float, set[str]],
    t: float,
    layout: str,
) -> bool | None:
    """True=coarse trống layout; False=có; None=không phải mốc coarse."""
    lays = stamp_layouts.get(t)
    if lays is None:
        for k, v in stamp_layouts.items():
            if abs(float(k) - float(t)) < 1e-3:
                lays = v
                break
    if lays is None:
        return None
    return layout not in lays


def _neighbor_empty(
    stamps: list[float],
    hit_t: float,
    *,
    before: bool,
    video_end: float,
) -> float:
    """Mốc coarse trống gần nhất trước/sau hit (fallback ± step)."""
    if before:
        prev = [t for t in stamps if t < hit_t - 1e-6]
        return float(prev[-1]) if prev else 0.0
    nxt = [t for t in stamps if t > hit_t + 1e-6]
    return float(nxt[0]) if nxt else float(video_end)


class _OverlayProbe:
    """Seek + OCR full ROI — TLS VideoCapture/OCR per thread (parallel-safe)."""

    def __init__(self, video: Path, *, project_id: str | None, workers: int) -> None:
        import cv2

        self._cv2 = cv2
        self.video = Path(video)
        self.project_id = project_id
        # Không kẹp 2 — pool theo GPU/CPU budget (caller đã pack)
        self._w = max(1, int(workers or 1))
        self._tls: Any = type("T", (), {})()
        self._sem = _ocr_semaphore()
        self._init_lock = threading.Lock()
        # Probe kích thước 1 lần
        cap0 = cv2.VideoCapture(str(self.video))
        self.ok = cap0.isOpened()
        self.vw = int(cap0.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080) if self.ok else 1080
        self.vh = int(cap0.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920) if self.ok else 1920
        # Giữ 1 cap chính cho refine tuần tự (edge) — thread chính
        self.cap = cap0 if self.ok else None

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def _engine(self):
        eng = getattr(self._tls, "ocr", None)
        if eng is None:
            with self._init_lock:
                eng = getattr(self._tls, "ocr", None)
                if eng is None:
                    try:
                        eng = _rapidocr_labels()
                    except Exception:
                        eng = _rapidocr_labels(use_cuda=False)
                    self._tls.ocr = eng
        return eng

    def _thread_cap(self):
        """Mỗi thread 1 VideoCapture — không share seek."""
        cap = getattr(self._tls, "cap", None)
        if cap is None or not cap.isOpened():
            cap = self._cv2.VideoCapture(str(self.video))
            self._tls.cap = cap
        return cap

    def ocr_at(self, t: float) -> dict[str, list[tuple[str, dict[str, int]]]]:
        check_cancel(self.project_id)
        if not self.ok:
            return {"mid": [], "vertical": [], "label": []}
        cap = self._thread_cap()
        if not cap.isOpened():
            return {"mid": [], "vertical": [], "label": []}
        cap.set(self._cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            return {"mid": [], "vertical": [], "label": []}
        snap = frame.copy()
        with self._sem:
            eng = self._engine()
            try:
                return _ocr_overlay_boxes_from_frame(snap, eng, self.vw, self.vh)
            except Exception:
                self._tls.ocr = _rapidocr_labels(use_cuda=False)
                eng = self._tls.ocr
                return _ocr_overlay_boxes_from_frame(snap, eng, self.vw, self.vh)


def _scan_overlay_stamps(
    video: Path,
    stamps: list[float],
    *,
    project_id: str | None,
    workers: int,
    on_progress: ProgressCb = None,
    progress_label: str = "OCR overlay",
) -> tuple[list[Hit], list[Hit], list[Hit], dict[float, set[str]]]:
    """OCR mốc coarse song song — mỗi worker 1 VideoCapture + 1 RapidOCR TLS."""
    if not stamps:
        return [], [], [], {}
    w = max(1, int(workers or 1))
    probe = _OverlayProbe(video, project_id=project_id, workers=w)
    mid_hits: list[Hit] = []
    vert_hits: list[Hit] = []
    lab_hits: list[Hit] = []
    stamp_layouts: dict[float, set[str]] = {}
    total = len(stamps)
    done = 0
    try:
        def _one(t: float) -> tuple[float, dict[str, list[tuple[str, dict[str, int]]]]]:
            return float(t), probe.ocr_at(float(t))

        if w <= 1 or total <= 1:
            results = [_one(t) for t in stamps]
        else:
            results = []
            with ThreadPoolExecutor(max_workers=min(w, total), thread_name_prefix="ocr-ov") as ex:
                futs = {ex.submit(_one, t): t for t in stamps}
                for fut in as_completed(futs):
                    results.append(fut.result())
            # giữ thứ tự theo stamps (ổn định cluster)
            order = {float(t): i for i, t in enumerate(stamps)}
            results.sort(key=lambda r: order.get(r[0], 0))

        for t, boxes in results:
            present: set[str] = set()
            for tx, bb in boxes.get("mid") or []:
                mid_hits.append((t, tx, bb))
                present.add("mid")
            for tx, bb in boxes.get("vertical") or []:
                vert_hits.append((t, tx, bb))
                present.add("vertical")
            for tx, bb in boxes.get("label") or []:
                lab_hits.append((t, tx, bb))
                present.add("label")
            stamp_layouts[float(t)] = present
            done += 1
            if on_progress and (done % max(1, total // 20 or 1) == 0 or done == total):
                on_progress(done, total, progress_label)
    finally:
        probe.close()
    return mid_hits, vert_hits, lab_hits, stamp_layouts


CoverHint = dict[str, Any]


def _edge_refine_cluster(
    probe: _OverlayProbe,
    cluster: list[Hit],
    layout: str,
    *,
    coarse: list[float],
    video_end: float,
    stamp_layouts: dict[float, set[str]] | None = None,
) -> tuple[list[Hit], CoverHint]:
    """Biên nhanh: pad 0-OCR từ coarse; trả cover hint (mốc trống kế)."""
    empty: CoverHint = {
        "t_before": 0.0,
        "t_after": float(video_end),
        "empty_before": False,
        "empty_after": False,
    }
    if not cluster:
        return [], empty
    t0 = float(cluster[0][0])
    t1 = float(cluster[-1][0])
    seed = _best_hit(cluster)[1]
    bb = _best_hit(cluster)[2]
    layouts = stamp_layouts or {}

    def has_at(t: float) -> bool:
        return _layout_present(probe.ocr_at(t), layout, seed)

    t_before = _neighbor_empty(coarse, t0, before=True, video_end=video_end)
    empty_before = False
    if t_before < t0 - 1e-3:
        abs_b = _coarse_layout_absent(layouts, t_before, layout)
        # đầu clip / không có mốc coarse → coi trống, pad 0-OCR
        if abs_b is None and t_before <= 0.05:
            abs_b = True
        if abs_b is False:
            # mốc trước vẫn có layout (chữ khác?) — kéo sớm hơn 50ms cố định
            start = max(0.0, t0 - 0.25)
        elif abs_b is True:
            start = _pad_edge_start(t_before, t0)
            empty_before = True
        else:
            start = _cheap_edge_start(has_at, t_before, t0)
    else:
        start = max(0.0, t0 - 0.2)

    t_after = _neighbor_empty(coarse, t1, before=False, video_end=video_end)
    empty_after = False
    if t_after > t1 + 1e-3:
        abs_a = _coarse_layout_absent(layouts, t_after, layout)
        if abs_a is None and abs(t_after - float(video_end)) <= 0.05:
            abs_a = True
        if abs_a is False:
            end = min(video_end, t1 + 0.05)
        elif abs_a is True:
            end = _pad_edge_end(t1, t_after)
            empty_after = True
        else:
            end = _cheap_edge_end(has_at, t1, t_after)
    else:
        end = min(video_end, t1 + 0.05)

    end = max(end, start + 0.08)
    mid_t = (start + end) * 0.5
    hits = [
        (round(start, 3), seed, bb),
        (round(mid_t, 3), seed, bb),
        (round(max(start, end - 0.05), 3), seed, bb),
    ]
    return hits, {
        "t_before": float(t_before),
        "t_after": float(t_after),
        "empty_before": empty_before,
        "empty_after": empty_after,
    }


def _is_sticky_vertical(cluster: list[Hit]) -> bool:
    """Cột dọc xuyên suốt — không edge-search từng mốc như flash."""
    if len(cluster) < _STICKY_VERT_HITS:
        return False
    span = float(cluster[-1][0]) - float(cluster[0][0])
    return span >= _STICKY_VERT_SPAN


def _partition_vert_clusters(
    clusters: list[list[Hit]],
) -> tuple[list[list[Hit]], list[list[Hit]]]:
    sticky: list[list[Hit]] = []
    flash: list[list[Hit]] = []
    for c in clusters:
        if _is_sticky_vertical(c):
            sticky.append(c)
        else:
            flash.append(c)
    return sticky, flash


def _sticky_vertical_edges(
    probe: _OverlayProbe,
    cluster: list[Hit],
    *,
    coarse: list[float],
    video_end: float,
    stamp_layouts: dict[float, set[str]] | None = None,
) -> tuple[list[Hit], CoverHint]:
    """Sticky dọc: pad biên từ coarse; trả cover hint."""
    empty: CoverHint = {
        "t_before": 0.0,
        "t_after": float(video_end),
        "empty_before": False,
        "empty_after": False,
    }
    if not cluster:
        return [], empty
    t0 = float(cluster[0][0])
    t1 = float(cluster[-1][0])
    seed = _best_hit(cluster)[1]
    bb = _best_hit(cluster)[2]
    vend = float(video_end)
    layouts = stamp_layouts or {}

    def has_at(t: float) -> bool:
        return _layout_present(probe.ocr_at(t), "vertical", seed)

    t_before = _neighbor_empty(coarse, t0, before=True, video_end=vend)
    empty_before = False
    if t_before < t0 - 1e-3:
        abs_b = _coarse_layout_absent(layouts, t_before, "vertical")
        if abs_b is False:
            start = max(0.0, t0 - 0.25)
        elif abs_b is True:
            start = _pad_edge_start(t_before, t0)
            empty_before = True
        elif t0 <= 2.5:
            start = 0.0
            empty_before = True
        else:
            start = _cheap_edge_start(has_at, t_before, t0)
    elif t0 <= 2.5:
        start = 0.0
        empty_before = True
    else:
        start = max(0.0, t0 - 0.2)

    t_after = _neighbor_empty(coarse, t1, before=False, video_end=vend)
    empty_after = False
    if t1 >= vend - 2.5:
        abs_tail = _coarse_layout_absent(layouts, t1, "vertical")
        if abs_tail is False or (t1 in layouts and "vertical" in layouts.get(t1, set())):
            end = vend
            empty_after = True
            t_after = vend
        else:
            te = min(vend, max(t1, vend - 0.15))
            end = vend if has_at(te) else _pad_edge_end(t1, max(t_after, te))
            empty_after = True
            t_after = vend
    elif t_after > t1 + 1e-3:
        abs_a = _coarse_layout_absent(layouts, t_after, "vertical")
        if abs_a is False:
            end = min(vend, t1 + 0.05)
        elif abs_a is True:
            end = _pad_edge_end(t1, t_after)
            empty_after = True
        else:
            end = _cheap_edge_end(has_at, t1, t_after)
    else:
        end = min(vend, t1 + 0.05)

    end = max(end, start + 0.15)
    mid_t = (start + end) * 0.5
    hits = [
        (round(start, 3), seed, bb),
        (round(mid_t, 3), seed, bb),
        (round(max(start, end - 0.05), 3), seed, bb),
    ]
    return hits, {
        "t_before": float(t_before),
        "t_after": float(t_after),
        "empty_before": empty_before,
        "empty_after": empty_after,
    }


# alias cũ
def _vertical_long_edges(*args: Any, **kwargs: Any) -> list[Hit]:
    hits, _hint = _sticky_vertical_edges(*args, **kwargs)
    return hits


def _seg_from_refined_hits(
    hits: list[Hit],
    *,
    layout: str,
    video_end: float,
    min_hold: float = 0.2,
    index: int = 1,
    cover_hint: CoverHint | None = None,
) -> dict[str, Any] | None:
    """1 cụm đã refine biên → 1 segment [start,end] + coverStart/coverEnd."""
    if not hits:
        return None
    _tm, text, bb = _best_hit(hits)
    if not (text or "").strip():
        return None
    t0 = float(hits[0][0])
    t1 = float(hits[-1][0])
    t1 = min(float(video_end), max(t1, t0 + min_hold))
    seg = _ocr_seg(index, t0, t1, text, layout=layout, bbox=bb)
    hint = cover_hint or {}
    attach_cover_times(
        seg,
        t_before=hint.get("t_before"),
        t_after=hint.get("t_after"),
        video_end=video_end,
        neighbor_empty_before=bool(hint.get("empty_before")),
        neighbor_empty_after=bool(hint.get("empty_after")),
    )
    return seg


def _ensure_cover_times(segs: list[dict[str, Any]], *, video_end: float) -> list[dict[str, Any]]:
    for s in segs:
        if s.get("coverStart") is None or s.get("coverEnd") is None:
            attach_cover_times(s, video_end=video_end)
    return segs


def _reindex_segs(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = sorted(segs, key=lambda s: float(s.get("start") or 0))
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def run_overlay_ocr(
    video: Path,
    *,
    project_id: str | None,
    video_end: float,
    workers: int = 2,
    set_status: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Coarse full-ROI thưa; hit → binary-search biên → 1 segment / cụm."""
    vend = max(0.0, float(video_end))
    coarse = _budget_stamps(vend, budget=28)
    step = adaptive_overlay_step(vend)

    mid: list[dict[str, Any]] = []
    vert: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []

    def _status(done: int, total: int, label: str, p0: int, p1: int) -> None:
        if not (set_status and project_id and total > 0):
            return
        pct = p0 + int((p1 - p0) * done / total)
        set_status(
            project_id,
            step="asr",
            progress=min(p1, max(p0, pct)),
            message=progress_msg(label, done, total, workers=max(1, int(workers or 1))),
            running=True,
        )

    try:
        mid_hits, vert_hits, lab_hits, stamp_layouts = _scan_overlay_stamps(
            video,
            coarse,
            project_id=project_id,
            workers=workers,
            on_progress=lambda d, t, lab: _status(d, t, lab, 38, 48),
            progress_label="OCR overlay quét",
        )

        mid_segs: list[dict[str, Any]] = []
        vert_segs: list[dict[str, Any]] = []
        lab_segs: list[dict[str, Any]] = []
        refined = False

        # Biên mid/nhãn: pad từ coarse — gần như không OCR thêm.
        # Chỉ mở probe khi neighbor ngoài lưới (hiếm).
        probe = _OverlayProbe(video, project_id=project_id, workers=workers)
        try:
            mid_clusters = _cluster_hits_overlay(
                mid_hits, gap=_CLUSTER_GAP, coarse_step=step, layout="mid"
            )
            lab_clusters = _cluster_hits_overlay(
                lab_hits, gap=_CLUSTER_GAP, coarse_step=step, layout="label"
            )
            vert_clusters = _cluster_hits(vert_hits, gap=max(_CLUSTER_GAP, step * 1.2))
            sticky_vert, flash_vert = _partition_vert_clusters(vert_clusters)

            if sticky_vert:
                n_sticky = len(sticky_vert)
                for si, cluster in enumerate(sticky_vert):
                    hits, hint = _sticky_vertical_edges(
                        probe,
                        cluster,
                        coarse=coarse,
                        video_end=vend,
                        stamp_layouts=stamp_layouts,
                    )
                    seg = _seg_from_refined_hits(
                        hits,
                        layout="vertical",
                        video_end=vend,
                        min_hold=0.15,
                        cover_hint=hint,
                    )
                    if seg:
                        vert_segs.append(seg)
                    _status(si + 1, n_sticky, "OCR dọc xuyên clip", 48, 50)
                refined = True

            edge_jobs: list[tuple[str, list[Hit]]] = []
            for c in mid_clusters:
                edge_jobs.append(("mid", c))
            for c in lab_clusters:
                edge_jobs.append(("label", c))
            for c in flash_vert:
                edge_jobs.append(("vertical", c))

            p0_edge, p1_edge = (50, 56) if sticky_vert else (48, 56)
            n_jobs = len(edge_jobs)
            for ji, (kind, cluster) in enumerate(edge_jobs):
                hits, hint = _edge_refine_cluster(
                    probe,
                    cluster,
                    kind,
                    coarse=coarse,
                    video_end=vend,
                    stamp_layouts=stamp_layouts,
                )
                hold = 0.3 if kind == "label" else (0.15 if kind == "vertical" else 0.2)
                seg = _seg_from_refined_hits(
                    hits,
                    layout=kind,
                    video_end=vend,
                    min_hold=hold,
                    cover_hint=hint,
                )
                if seg:
                    if kind == "mid":
                        mid_segs.append(seg)
                    elif kind == "label":
                        lab_segs.append(seg)
                    else:
                        vert_segs.append(seg)
                refined = True
                if n_jobs:
                    _status(ji + 1, n_jobs, "OCR biên mid/nhãn", p0_edge, p1_edge)
        finally:
            probe.close()

        if refined:
            merge_gap = max(2.2, step * 1.5)
            mid = _ensure_cover_times(
                _merge_mid_segments(_reindex_segs(mid_segs), max_gap=merge_gap),
                video_end=vend,
            )
            vert = _ensure_cover_times(_reindex_segs(vert_segs), video_end=vend)
            labels = _reindex_segs(lab_segs)
            if len(labels) > 1:
                labels = _merge_label_segments(labels)
                labels = _reindex_segs(labels)
            labels = _ensure_cover_times(labels, video_end=vend)
        else:
            # fallback coarse-only: gap ≈ step để không tách hit thưa
            gap_fb = max(1.5, step * 1.2)
            if mid_hits:
                mid = _ocr_cluster_hits(
                    mid_hits,
                    video_end=vend,
                    step=0.2,
                    layout="mid",
                    gap=gap_fb,
                    min_hold=0.2,
                )
            if vert_hits:
                vert = _ocr_cluster_hits(
                    vert_hits,
                    video_end=vend,
                    step=0.25,
                    layout="vertical",
                    gap=gap_fb,
                    min_hold=0.15,
                )
            if lab_hits:
                labels = _ocr_cluster_hits(
                    lab_hits,
                    video_end=vend,
                    step=0.2,
                    layout="label",
                    gap=gap_fb,
                    min_hold=0.3,
                )
            mid = _ensure_cover_times(mid, video_end=vend)
            vert = _ensure_cover_times(vert, video_end=vend)
            labels = _ensure_cover_times(labels, video_end=vend)
    except Exception:
        mid, vert, labels = [], [], []

    return mid, vert, labels


def _refine_stamps_gone() -> bool:
    """ponytail: self-check — không còn API lưới refine dày."""
    return not callable(globals().get("_refine_stamps"))
