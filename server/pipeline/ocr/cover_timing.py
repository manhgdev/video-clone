"""Cửa sổ che chữ — khớp LivePreviewEditor.coverWindow / burn cues.

Lead nhỏ ở đầu (tránh bbox/mask hiện trước chữ).
Tail / t_after: kéo dài che tới khi chữ thật sự biến mất.
"""
from __future__ import annotations

from typing import Any


def cover_lead_tail(layout: str, *, start: float, end: float, source: str = "") -> tuple[float, float]:
    """(lead, tail) giây nới quanh [start,end) theo layout."""
    lay = layout if layout in ("horizontal", "vertical", "label", "mid") else "horizontal"
    s0, e0 = float(start), float(end)
    dur = max(0.0, e0 - s0)
    if lay == "label":
        # OCR first-hit hay trễ hơn chữ — lead đủ che sớm
        return 0.28, 0.12
    if lay == "mid":
        return 0.35, 0.22
    if lay == "vertical":
        # watermark: OCR hay trễ nhẹ
        return (0.28 if dur >= 2.5 else 0.15, 0.0)
    src_cjk = sum(1 for c in (source or "") if "\u4e00" <= c <= "\u9fff")
    lead = 0.25  # hardsub ASR hay sớm hơn mực
    tail = 1.05 if dur <= 0.75 and src_cjk <= 4 else 0.45
    return lead, max(0.4, tail)


def default_cover_window(
    *,
    start: float,
    end: float,
    layout: str = "horizontal",
    source: str = "",
    video_end: float | None = None,
) -> tuple[float, float]:
    """Fallback cover khi segment chưa có coverStart/coverEnd."""
    s0, e0 = float(start), float(end)
    lead, tail = cover_lead_tail(layout, start=s0, end=e0, source=source)
    cs = max(0.0, s0 - lead)
    if layout == "vertical":
        ce = max(e0, cs + 0.04)
    elif layout == "label":
        ce = max(e0 + tail, cs + 0.20)
    elif layout == "mid":
        ce = max(e0 + tail, cs + 0.12)
    else:
        ce = max(e0 + tail, cs + 0.20)
    if video_end is not None:
        ce = min(float(video_end), ce)
    return round(cs, 3), round(max(ce, cs + 0.04), 3)


def resolve_cover_window(seg: dict[str, Any], *, video_end: float | None = None) -> tuple[float, float]:
    """Ưu tiên coverStart/coverEnd đã lưu; không thì default theo layout.

    Mid/label: nếu coverStart kéo sớm quá (OCR cũ → mốc trống trước) thì kẹp gần start.
    """
    s0 = float(seg.get("start") or 0)
    e0 = float(seg.get("end") or 0)
    layout = str(seg.get("layout") or "horizontal")
    source = str(seg.get("source") or "")
    cs_raw, ce_raw = seg.get("coverStart"), seg.get("coverEnd")
    if cs_raw is not None and ce_raw is not None:
        try:
            cs = float(cs_raw)
            ce = float(ce_raw)
            if ce > cs + 1e-6:
                # OCR cũ kéo coverStart về mốc trống quá sớm → kẹp, nhưng cho lead ~0.35 (OCR trễ)
                if layout in ("mid", "label") and cs < s0 - 0.45:
                    cs = max(0.0, s0 - 0.35)
                # ponytail: coverEnd lưu có thể ngắn hơn clip — luôn phủ hết [start,end)+tail
                _lead, tail = cover_lead_tail(layout, start=s0, end=e0, source=source)
                ce = max(ce, e0, cs + 0.04)
                if layout in ("mid", "label"):
                    ce = max(ce, e0 + tail)
                if video_end is not None:
                    ce = min(float(video_end), ce)
                return round(max(0.0, cs), 3), round(max(ce, cs + 0.04), 3)
        except (TypeError, ValueError):
            pass
    return default_cover_window(
        start=s0,
        end=float(seg.get("end") or 0),
        layout=layout,
        source=str(seg.get("source") or ""),
        video_end=video_end,
    )


def attach_cover_times(
    seg: dict[str, Any],
    *,
    t_before: float | None = None,
    t_after: float | None = None,
    video_end: float | None = None,
    neighbor_empty_before: bool = False,
    neighbor_empty_after: bool = False,
) -> dict[str, Any]:
    """Gán coverStart/coverEnd.

    - Đầu: lead trước start (OCR hay trễ); nếu mốc trước trống → kéo coverStart về gần t_before.
    - Cuối: khi mốc kế trống → kéo coverEnd tới t_after (+tail) để hết lộ chữ cũ.
    """
    s0 = float(seg.get("start") or 0)
    e0 = float(seg.get("end") or 0)
    layout = str(seg.get("layout") or "horizontal")
    source = str(seg.get("source") or "")
    _lead, tail = cover_lead_tail(layout, start=s0, end=e0, source=source)
    vend = float(video_end) if video_end is not None else None

    cs, ce = default_cover_window(
        start=s0, end=e0, layout=layout, source=source, video_end=vend
    )
    # Mid/label: giữ lead mặc định. Mốc trống trước → che sớm hơn first-hit (chữ đã có trong khe).
    if (
        layout in ("mid", "label", "vertical")
        and neighbor_empty_before
        and t_before is not None
        and float(t_before) < s0 - 0.05
    ):
        early = max(0.0, float(t_before) + 0.08)
        cs = min(cs, early)
        # không sớm hơn t_before; có thể sớm hơn start (đúng: chữ xuất trong khe)

    if neighbor_empty_after and t_after is not None and float(t_after) > e0 + 1e-3:
        # chữ còn tới ~mốc trống — kéo coverEnd tới đó + tail nhỏ
        ce = max(ce, float(t_after) + (0.0 if layout == "vertical" else min(tail, 0.22)))
        if vend is not None:
            ce = min(vend, ce)

    cs = max(0.0, round(cs, 3))
    ce = round(max(ce, cs + 0.04, e0), 3)
    if vend is not None:
        ce = min(vend, ce)
    seg["coverStart"] = cs
    seg["coverEnd"] = ce
    return seg
