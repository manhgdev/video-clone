"""Paddle/RapidOCR hardsub extract — merge."""
from __future__ import annotations

"""RapidOCR extract — hardsub đáy + mid/vertical/labels.

Tách khỏi asr.py (Whisper) và đường dịch/phụ đề burn layout.
Không sửa logic — chỉ di chuyển.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

from pipeline.core.jobs import check_cancel, run_cmd
from pipeline.core.project import cache_frames, set_status
from pipeline.core.resources import adaptive_workers

# giới hạn tổng luồng OCR phụ — tránh 100% CPU (để UI/OS ~5–10%)
_ocr_sem: threading.Semaphore | None = None
_ocr_sem_n: int = 0


from .textutil import *  # noqa: F403

def _fold_duplicate_watermark_labels(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Label cùng cột/watermark với vertical → gộp timing, bỏ label mảnh."""
    verts = [s for s in segs if str(s.get("layout") or "") == "vertical"]
    if not verts:
        return segs
    out: list[dict[str, Any]] = []
    for s in segs:
        if str(s.get("layout") or "") != "label":
            out.append(s)
            continue
        src = s.get("source") or ""
        folded = False
        for v in verts:
            vs = v.get("source") or ""
            if not (_ocr_same(src, vs) or _ocr_sim(src, vs) >= 0.65):
                continue
            # cùng cột (bbox) hoặc thiếu bbox vẫn gộp nếu text khớp
            vb, sb = v.get("bbox"), s.get("bbox")
            near_col = True
            if isinstance(vb, dict) and isinstance(sb, dict):
                vcx = float(vb["x"]) + float(vb["w"]) * 0.5
                scx = float(sb["x"]) + float(sb["w"]) * 0.5
                near_col = abs(vcx - scx) < 90
            if not near_col:
                continue
            v["start"] = min(float(v.get("start") or 0), float(s.get("start") or 0))
            v["end"] = max(float(v.get("end") or 0), float(s.get("end") or 0))
            # nới bbox xuống nếu label cao hơn (thường kèm Latin dưới CJK)
            if isinstance(vb, dict) and isinstance(sb, dict):
                y0 = min(float(vb["y"]), float(sb["y"]))
                y1 = max(
                    float(vb["y"]) + float(vb["h"]),
                    float(sb["y"]) + float(sb["h"]),
                )
                x0 = min(float(vb["x"]), float(sb["x"]))
                x1 = max(
                    float(vb["x"]) + float(vb["w"]),
                    float(sb["x"]) + float(sb["w"]),
                )
                v["bbox"] = {
                    "x": int(x0),
                    "y": int(y0),
                    "w": max(8, int(x1 - x0)),
                    "h": max(8, int(y1 - y0)),
                }
            folded = True
            break
        if not folded:
            out.append(s)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _trim_vertical_ocr_tail(src: str) -> str:
    """Bỏ đuôi 1-glyph dính cụm watermark (≥3 CJK)."""
    n = _ocr_norm(src)
    if len(n) <= 3:
        return src
    stem, rest = n[:3], n[3:]
    if rest and all(_is_cjk(c) for c in rest) and len(rest) <= 2:
        return stem
    return src


def _fold_vertical_column_flickers(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OCR dọc flicker (花木紫/花水業/…) cùng cột + overlap → 1 segment."""
    verts = [s for s in segs if str(s.get("layout") or "") == "vertical"]
    others = [s for s in segs if str(s.get("layout") or "") != "vertical"]
    if not verts:
        return segs

    verts = sorted(verts, key=lambda s: float(s.get("start") or 0))
    merged: list[dict[str, Any]] = []
    for v in verts:
        src0 = _trim_vertical_ocr_tail(str(v.get("source") or ""))
        cjk = sum(1 for c in src0 if _is_cjk(c))
        if cjk < 2:
            continue
        v = {**v, "source": src0}
        vs, ve = float(v.get("start") or 0), float(v.get("end") or 0)
        vb = v.get("bbox")
        hit = None
        for m in merged:
            ms, me = float(m.get("start") or 0), float(m.get("end") or 0)
            if ve < ms - 0.3 or vs > me + 0.3:
                continue
            mb = m.get("bbox")
            near = True
            if isinstance(vb, dict) and isinstance(mb, dict):
                vcx = float(vb["x"]) + float(vb["w"]) * 0.5
                mcx = float(mb["x"]) + float(mb["w"]) * 0.5
                near = abs(vcx - mcx) < 100
            if near:
                hit = m
                break
        if hit is None:
            merged.append(dict(v))
            continue
        hit["start"] = min(float(hit.get("start") or 0), vs)
        hit["end"] = max(float(hit.get("end") or 0), ve)
        hit["source"] = _trim_vertical_ocr_tail(
            _ocr_pick_best(
                [str(hit.get("source") or ""), str(v.get("source") or "")]
            )
        )
        hb, vb2 = hit.get("bbox"), v.get("bbox")
        if isinstance(hb, dict) and isinstance(vb2, dict):
            x0 = min(float(hb["x"]), float(vb2["x"]))
            y0 = min(float(hb["y"]), float(vb2["y"]))
            x1 = max(float(hb["x"]) + float(hb["w"]), float(vb2["x"]) + float(vb2["w"]))
            y1 = max(float(hb["y"]) + float(hb["h"]), float(vb2["y"]) + float(vb2["h"]))
            hit["bbox"] = {
                "x": int(x0),
                "y": int(y0),
                "w": max(8, int(x1 - x0)),
                "h": max(8, int(y1 - y0)),
            }
    out = others + merged
    out.sort(key=lambda s: float(s.get("start") or 0))
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _drop_mid_in_watermark_column(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mid 1 glyph trong cột watermark dọc → nhiễu OCR (vd. 尔 dưới 花木紫), bỏ burn."""
    verts = [s for s in segs if str(s.get("layout") or "") == "vertical"]
    if not verts:
        return segs
    out: list[dict[str, Any]] = []
    for s in segs:
        if str(s.get("layout") or "") != "mid":
            out.append(s)
            continue
        bb = s.get("bbox")
        if not isinstance(bb, dict):
            out.append(s)
            continue
        src = re.sub(r"\s+", "", s.get("source") or "")
        cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
        if cjk != 1 or cjk != len(src):
            out.append(s)
            continue
        mcx = float(bb["x"]) + float(bb["w"]) * 0.5
        mcy = float(bb["y"]) + float(bb["h"]) * 0.5
        ss, se = float(s.get("start") or 0), float(s.get("end") or 0)
        drop = False
        for v in verts:
            vb = v.get("bbox")
            if not isinstance(vb, dict):
                continue
            vs, ve = float(v.get("start") or 0), float(v.get("end") or 0)
            if se <= vs + 0.05 or ss >= ve - 0.05:
                continue
            pad_x = max(12.0, float(vb["w"]) * 0.35)
            pad_y_bot = max(36.0, float(vb["h"]) * 0.12)
            vx0 = float(vb["x"]) - pad_x
            vx1 = float(vb["x"]) + float(vb["w"]) + pad_x
            vy0 = float(vb["y"]) - 8.0
            vy1 = float(vb["y"]) + float(vb["h"]) + pad_y_bot
            if vx0 <= mcx <= vx1 and vy0 <= mcy <= vy1:
                drop = True
                break
        if not drop:
            out.append(s)
    if len(out) != len(segs):
        for i, s in enumerate(out, start=1):
            s["index"] = i
    return out


def _ocr_segments_from_timeline(
    timed: list[tuple[float, str]], video_end: float
) -> list[dict[str, Any]]:
    """Mỗi đoạn = chuỗi khung cùng chữ; hardsub đáy = horizontal."""
    segs: list[dict[str, Any]] = []
    i = 0
    n = len(timed)
    while i < n:
        t0, text = timed[i]
        if not text:
            i += 1
            continue
        j = i + 1
        window = [text]
        while j < n:
            nxt = timed[j][1]
            if not nxt:
                k = j + 1
                while k < n and not timed[k][1]:
                    k += 1
                gap = (timed[k][0] if k < n else video_end) - timed[j][0]
                if k < n and gap <= 0.6 and _ocr_same(text, timed[k][1]):
                    j = k
                    window.append(timed[k][1])
                    text = _ocr_pick_best(window)
                    j += 1
                    continue
                break
            if _ocr_same(text, nxt) or _ocr_same(window[-1], nxt):
                window.append(nxt)
                text = _ocr_pick_best(window)
                j += 1
                continue
            break
        end = timed[j][0] if j < n else video_end
        end = max(float(end), float(t0) + 0.35)
        segs.append(
            _ocr_seg(len(segs) + 1, t0, end, _ocr_pick_best(window), layout="horizontal")
        )
        i = j

    merged: list[dict[str, Any]] = []
    for seg in segs:
        if (
            merged
            and _ocr_same(merged[-1]["source"], seg["source"])
            and float(seg["start"]) - float(merged[-1]["end"]) <= 0.85
        ):
            prev = merged[-1]
            prev["end"] = max(float(prev["end"]), float(seg["end"]))
            prev["source"] = _ocr_pick_best([prev["source"], seg["source"]])
        else:
            merged.append(seg)
    for i, s in enumerate(merged, start=1):
        s["index"] = i
    # Hardsub hay lộ trước/sau khung OCR 2fps — nới start/end trên timeline
    return _ocr_pad_hardsub_windows(merged, video_end)


def _ocr_pad_hardsub_windows(
    segs: list[dict[str, Any]], video_end: float
) -> list[dict[str, Any]]:
    """Nới nhẹ cửa sổ hardsub ngang để khớp lúc che/xuất (ASR 2fps dễ cắt sớm/muộn)."""
    if not segs:
        return segs
    out = [dict(s) for s in segs]
    for i, seg in enumerate(out):
        if str(seg.get("layout") or "horizontal") != "horizontal":
            continue
        s0 = float(seg.get("start") or 0)
        e0 = float(seg.get("end") or s0)
        prev_end = float(out[i - 1].get("end") or 0) if i > 0 else 0.0
        next_start = (
            float(out[i + 1].get("start") or video_end)
            if i + 1 < len(out)
            else float(video_end)
        )
        lead = 0.18
        tail = 0.22
        src = str(seg.get("source") or "")
        src_cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
        if (e0 - s0) <= 0.75 and src_cjk <= 4:
            tail = max(tail, 0.45)
        new_start = max(0.0 if i == 0 else prev_end + 0.03, s0 - lead)
        new_end = min(next_start - 0.03 if i + 1 < len(out) else video_end, e0 + tail)
        if new_end > new_start + 0.12:
            seg["start"] = round(new_start, 3)
            seg["end"] = round(new_end, 3)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _ocr_cluster_hits(
    timed: list[Any],
    *,
    video_end: float,
    step: float,
    layout: str,
    gap: float = 0.45,
    min_hold: float = 0.2,
) -> list[dict[str, Any]]:
    """timed: (t, text) hoặc (t, text, bbox|None)."""
    normed: list[tuple[float, str, dict[str, int] | None]] = []
    for row in timed:
        if not row:
            continue
        if len(row) >= 3:
            t, tx, box = float(row[0]), str(row[1] or ""), row[2]
            bb = box if isinstance(box, dict) else None
        else:
            t, tx = float(row[0]), str(row[1] or "")
            bb = None
        if tx:
            normed.append((t, tx, bb))

    segs: list[dict[str, Any]] = []
    i = 0
    while i < len(normed):
        t0, tx0, box0 = normed[i]
        window = [tx0]
        boxes = [box0] if box0 else []
        j = i + 1
        while j < len(normed):
            t1, tx1, box1 = normed[j]
            if t1 - normed[j - 1][0] > gap:
                break
            same = (
                _ocr_same(tx0, tx1)
                or _ocr_same(window[-1], tx1)
                or _ocr_sim(tx0, tx1) >= 0.72
            )
            # nhãn: cột nguyên liệu OCR nhấp nháy → gộp nếu token chồng ≥50%
            if not same and layout == "label":
                same = (
                    _ocr_label_overlap(tx0, tx1) >= 0.5
                    or _ocr_label_overlap(window[-1], tx1) >= 0.5
                )
            # watermark dọc: OCR hay lệch 1 glyph (紫/業/荣) — cùng độ dài ≥3 → gộp
            if not same and layout == "vertical":
                n0, n1 = _ocr_norm(tx0), _ocr_norm(tx1)
                same = (
                    len(n0) == len(n1) >= 3
                    and (
                        _ocr_sim(tx0, tx1) >= 0.45
                        or (n0 and n1 and n0[0] == n1[0])
                    )
                )
            if same:
                window.append(tx1)
                if box1:
                    boxes.append(box1)
                tx0 = _ocr_pick_best(window)
                j += 1
                continue
            break
        best = _ocr_pick_best(window)
        if not best or sum(1 for c in best if _is_cjk(c)) < 1:
            i = j
            continue
        t_start = t0
        # Vertical watermark: OCR hit muộn hơn lúc mực hiện — kéo sớm nhẹ
        if layout == "vertical":
            t_start = max(0.0, t0 - min(0.55, max(0.25, step * 1.5)))
        t_end = normed[j - 1][0] + step
        t_end = min(video_end, max(t_end, t_start + min_hold))
        # bbox: ưu tiên khớp text best; không thì union
        bb: dict[str, int] | None = None
        for k in range(i, j):
            if _ocr_same(normed[k][1], best) and normed[k][2]:
                bb = normed[k][2]
                break
        if bb is None and boxes:
            fw = max((b["x"] + b["w"] for b in boxes), default=1080)
            fh = max((b["y"] + b["h"] for b in boxes), default=1920)
            # union trong không gian pixel ước (fw/fh chỉ clamp)
            bb = _union_bbox(boxes, max(fw, 1080), max(fh, 1920))
        segs.append(_ocr_seg(len(segs) + 1, t_start, t_end, best, layout=layout, bbox=bb))
        i = j

    # nhãn: gộp segment chồng thời gian / gần nhau (tránh 0.3s mảnh)
    if layout == "label" and len(segs) > 1:
        segs = _merge_label_segments(segs)
    return segs


def _merge_label_segments(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gộp nhãn cùng chỗ + gần thời gian; giữ bbox; không gộp 2 nhãn xa nhau."""
    ordered = sorted(segs, key=lambda s: float(s.get("start") or 0))
    out: list[dict[str, Any]] = []

    def _near(a: dict[str, Any], b: dict[str, Any]) -> bool:
        ba, bb = a.get("bbox"), b.get("bbox")
        if not isinstance(ba, dict) or not isinstance(bb, dict):
            return True
        acx = float(ba["x"]) + float(ba["w"]) * 0.5
        acy = float(ba["y"]) + float(ba["h"]) * 0.5
        bcx = float(bb["x"]) + float(bb["w"]) * 0.5
        bcy = float(bb["y"]) + float(bb["h"]) * 0.5
        # cùng chỗ (màn 1080≈) — nhãn khác cột không gộp
        return abs(acx - bcx) < 80 and abs(acy - bcy) < 90

    for s in ordered:
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        gap = float(s["start"]) - float(prev["end"])
        ov = _ocr_label_overlap(prev.get("source") or "", s.get("source") or "")
        if gap <= 0.55 and ov >= 0.55 and _near(prev, s):
            prev["end"] = max(float(prev["end"]), float(s["end"]))
            prev["source"] = _ocr_pick_best(
                [prev.get("source") or "", s.get("source") or ""]
            )
            if not prev.get("bbox") and s.get("bbox"):
                prev["bbox"] = s["bbox"]
            pcs, pce = prev.get("coverStart"), prev.get("coverEnd")
            scs, sce = s.get("coverStart"), s.get("coverEnd")
            if pcs is not None or scs is not None:
                prev["coverStart"] = min(
                    float(pcs if pcs is not None else prev["start"]),
                    float(scs if scs is not None else s["start"]),
                )
            if pce is not None or sce is not None:
                prev["coverEnd"] = max(
                    float(pce if pce is not None else prev["end"]),
                    float(sce if sce is not None else s["end"]),
                )
            continue
        if gap < 0 and ov >= 0.45 and _near(prev, s):
            prev["end"] = max(float(prev["end"]), float(s["end"]))
            prev["source"] = _ocr_pick_best(
                [prev.get("source") or "", s.get("source") or ""]
            )
            if not prev.get("bbox") and s.get("bbox"):
                prev["bbox"] = s["bbox"]
            pcs, pce = prev.get("coverStart"), prev.get("coverEnd")
            scs, sce = s.get("coverStart"), s.get("coverEnd")
            if pcs is not None or scs is not None:
                prev["coverStart"] = min(
                    float(pcs if pcs is not None else prev["start"]),
                    float(scs if scs is not None else s["start"]),
                )
            if pce is not None or sce is not None:
                prev["coverEnd"] = max(
                    float(pce if pce is not None else prev["end"]),
                    float(sce if sce is not None else s["end"]),
                )
            continue
        out.append(s)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _merge_mid_segments(
    segs: list[dict[str, Any]],
    *,
    max_gap: float = 2.5,
) -> list[dict[str, Any]]:
    """Gộp mid cùng chữ gần nhau — chữ xuyên màn không bị tách mỗi mốc coarse."""
    ordered = sorted(segs, key=lambda s: float(s.get("start") or 0))
    out: list[dict[str, Any]] = []
    for s in ordered:
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        gap = float(s.get("start") or 0) - float(prev.get("end") or 0)
        ps, ss = prev.get("source") or "", s.get("source") or ""
        same = _ocr_same(ps, ss) or _ocr_sim(ps, ss) >= 0.72
        if not same or gap > max_gap:
            out.append(s)
            continue
        prev["end"] = max(float(prev.get("end") or 0), float(s.get("end") or 0))
        prev["source"] = _ocr_pick_best([ps, ss])
        # gộp cửa sổ che nếu có
        pcs, pce = prev.get("coverStart"), prev.get("coverEnd")
        scs, sce = s.get("coverStart"), s.get("coverEnd")
        if pcs is not None or scs is not None:
            prev["coverStart"] = min(
                float(pcs if pcs is not None else prev["start"]),
                float(scs if scs is not None else s.get("start") or 0),
            )
        if pce is not None or sce is not None:
            prev["coverEnd"] = max(
                float(pce if pce is not None else prev["end"]),
                float(sce if sce is not None else s.get("end") or 0),
            )
        pb, sb = prev.get("bbox"), s.get("bbox")
        if isinstance(pb, dict) and isinstance(sb, dict):
            ub = _union_bbox([pb, sb], 1080, 1920)
            if ub:
                prev["bbox"] = ub
        elif not pb and sb:
            prev["bbox"] = sb
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _merge_whisper_hardsub_fragments(
    segs: list[dict[str, Any]],
    *,
    max_gap: float = 0.35,
    max_shard_cjk: int = 8,
    max_combined_cjk: int = 18,
) -> list[dict[str, Any]]:
    """Chỉ gộp mảnh Whisper rất ngắn + sát — không dính hai câu dịch liền nhau."""
    ordered = sorted(segs, key=lambda s: float(s.get("start") or 0))
    out: list[dict[str, Any]] = []
    for s in ordered:
        lay = str(s.get("layout") or "")
        if lay in ("vertical", "label"):
            out.append(s)
            continue
        src = (s.get("source") or "").strip()
        if _cjk_len(src) < 1:
            out.append(s)
            continue
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        if str(prev.get("layout") or "") in ("vertical", "label"):
            out.append(s)
            continue
        ps = (prev.get("source") or "").strip()
        if _cjk_len(ps) < 1:
            out.append(s)
            continue
        gap = float(s.get("start") or 0) - float(prev.get("end") or 0)
        pc, sc = _cjk_len(ps), _cjk_len(src)
        # ponytail: chỉ mảnh ngắn + gap/overlap rất sát — tránh ghép 2 dòng hardsub thành 1
        if (
            gap <= max_gap
            and pc <= max_shard_cjk
            and sc <= max_shard_cjk
            and pc + sc <= max_combined_cjk
        ):
            prev["end"] = max(float(prev.get("end") or 0), float(s.get("end") or 0))
            prev["source"] = _join_whisper_sources(ps, src)
            prev.pop("translation", None)
            prev.pop("captionLayout", None)
            pb, sb = prev.get("bbox"), s.get("bbox")
            if isinstance(pb, dict) and isinstance(sb, dict):
                ub = _union_bbox([pb, sb], 1080, 1920)
                if ub:
                    prev["bbox"] = ub
            elif not pb and sb:
                prev["bbox"] = sb
            continue
        out.append(s)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _merge_horizontal_vertical(
    horiz: list[dict[str, Any]], vert: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ghép tiêu đề dọc / nhãn + hardsub đáy; tránh trùng chữ."""
    out = list(horiz)
    for v in vert:
        vs = v.get("source") or ""
        vlay = str(v.get("layout") or "vertical")
        matches: list[dict[str, Any]] = []
        for h in out:
            hs = h.get("source") or ""
            hlay = str(h.get("layout") or "horizontal")
            same = _ocr_same(vs, hs)
            # OCR dọc hay lệch đúng 1 glyph (紫/業). Nhãn quét toàn clip ổn
            # định hơn, nên coi chuỗi 3+ glyph cùng độ dài là một title.
            if not same and {vlay, hlay} <= {"vertical", "label"}:
                vn, hn = _ocr_norm(vs), _ocr_norm(hs)
                same = len(vn) == len(hn) >= 3 and _ocr_sim(vs, hs) >= 0.65
            if same:
                matches.append(h)
        if matches:
            # đã có segment cùng chữ — chỉ absorb khi nhánh dưới thật sự nới/ghép.
            absorbed = False
            for h in matches:
                hs = h.get("source") or ""
                hlay = str(h.get("layout") or "horizontal")
                if (
                    vlay == "label"
                    and hlay == "vertical"
                ):
                    # Label pass quét suốt video và cho text/timing đáng tin hơn.
                    h["source"] = vs
                    h["start"] = min(
                        float(h.get("start") or 0), float(v.get("start") or 0)
                    )
                    h["end"] = max(
                        float(h.get("end") or 0), float(v.get("end") or 0)
                    )
                    absorbed = True
                elif vlay == "vertical" and hlay == "vertical":
                    h["start"] = min(
                        float(h.get("start") or 0), float(v.get("start") or 0)
                    )
                    h["end"] = max(
                        float(h.get("end") or 0), float(v.get("end") or 0)
                    )
                    absorbed = True
                elif vlay == "label" and hlay == "mid":
                    # Label mảnh cùng chữ mid (vd. 挖果) — nới cửa sổ nếu kề nhau.
                    # Không nối flash xa (tránh 尔 5s dính 尔 19s).
                    hs0 = float(h.get("start") or 0)
                    he0 = float(h.get("end") or 0)
                    vs0 = float(v.get("start") or 0)
                    ve0 = float(v.get("end") or 0)
                    if vs0 <= he0 + 1.2 and ve0 >= hs0 - 1.2:
                        h["start"] = min(hs0, vs0)
                        h["end"] = max(he0, ve0)
                        if not h.get("bbox") and v.get("bbox"):
                            h["bbox"] = v["bbox"]
                        absorbed = True
                elif vlay == "mid" and hlay == "horizontal":
                    # Crop đáy quá cao hay nuốt mid → giữ mid, bỏ nhãn Caption trùng
                    if _ocr_same(vs, hs) or _ocr_sim(vs, hs) >= 0.72:
                        h["layout"] = "mid"
                        h["source"] = _ocr_pick_best([hs, vs])
                        h["start"] = min(
                            float(h.get("start") or 0), float(v.get("start") or 0)
                        )
                        h["end"] = max(
                            float(h.get("end") or 0), float(v.get("end") or 0)
                        )
                        if v.get("bbox"):
                            h["bbox"] = v["bbox"]
                        absorbed = True
                elif vlay == "mid" and hlay == "mid":
                    hs0 = float(h.get("start") or 0)
                    he0 = float(h.get("end") or 0)
                    vs0 = float(v.get("start") or 0)
                    ve0 = float(v.get("end") or 0)
                    # chỉ gộp mid kề (≤0.85s) — flash rời không nối xuyên clip
                    if vs0 <= he0 + 0.85 and ve0 >= hs0 - 0.85:
                        h["start"] = min(hs0, vs0)
                        h["end"] = max(he0, ve0)
                        absorbed = True
                if (
                    vlay == "label"
                    and _ocr_same(vs, hs)
                    and len(_ocr_norm(vs)) > len(_ocr_norm(hs))
                ):
                    h["source"] = vs
                    absorbed = True
                if (
                    _ocr_same(vs, h.get("source") or "")
                    and float(h.get("start") or 0) < 2.0
                    and vlay == "vertical"
                ):
                    h["layout"] = "vertical"
                    absorbed = True
            if absorbed:
                continue
        out.append(v)
    # Bỏ mảnh title thiếu 1 glyph nằm trọn trong title dọc đầy đủ cùng thời gian.
    compacted: list[dict[str, Any]] = []
    for s in out:
        if str(s.get("layout") or "horizontal") != "vertical":
            compacted.append(s)
            continue
        sn = _ocr_norm(s.get("source") or "")
        merged_into: dict[str, Any] | None = None
        for prev in compacted:
            if str(prev.get("layout") or "horizontal") != "vertical":
                continue
            pn = _ocr_norm(prev.get("source") or "")
            overlap = min(
                float(s.get("end") or 0), float(prev.get("end") or 0)
            ) - max(float(s.get("start") or 0), float(prev.get("start") or 0))
            if overlap >= 0 and min(len(sn), len(pn)) >= 2 and (sn in pn or pn in sn):
                merged_into = prev
                break
        if merged_into is None:
            compacted.append(s)
            continue
        if len(sn) > len(_ocr_norm(merged_into.get("source") or "")):
            merged_into["source"] = s.get("source") or ""
        merged_into["start"] = min(
            float(merged_into.get("start") or 0), float(s.get("start") or 0)
        )
        merged_into["end"] = max(
            float(merged_into.get("end") or 0), float(s.get("end") or 0)
        )
    out = compacted
    out.sort(key=lambda s: float(s.get("start") or 0))
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


__all__ = [
    '_ocr_sem',
    '_ocr_sem_n',
    '_fold_duplicate_watermark_labels',
    '_trim_vertical_ocr_tail',
    '_fold_vertical_column_flickers',
    '_drop_mid_in_watermark_column',
    '_ocr_segments_from_timeline',
    '_ocr_pad_hardsub_windows',
    '_ocr_cluster_hits',
    '_merge_label_segments',
    '_merge_mid_segments',
    '_merge_whisper_hardsub_fragments',
    '_merge_horizontal_vertical',
]
