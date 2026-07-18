"""Compound segment expand for export/preview."""
from __future__ import annotations
from typing import Any

def expand_compound_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bung compound shell → children absolute time (export/burn/mux)."""
    out: list[dict[str, Any]] = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        if not s.get("isCompound"):
            out.append(dict(s))
            continue
        t0 = float(s.get("start") or 0)
        children = s.get("compoundChildren") or []
        if not isinstance(children, list) or not children:
            # shell không children — bỏ (tránh burn text "Compound · N")
            continue
        for ch in children:
            if not isinstance(ch, dict):
                continue
            item = dict(ch)
            st = float(item.get("start") or 0)
            en = float(item.get("end") or st)
            item["start"] = t0 + st
            item["end"] = t0 + en
            if item.get("coverStart") is not None:
                try:
                    item["coverStart"] = t0 + float(item["coverStart"])
                except (TypeError, ValueError):
                    pass
            if item.get("coverEnd") is not None:
                try:
                    item["coverEnd"] = t0 + float(item["coverEnd"])
                except (TypeError, ValueError):
                    pass
            item.pop("isCompound", None)
            item.pop("compoundChildren", None)
            out.append(item)
    out.sort(key=lambda x: (float(x.get("start") or 0), float(x.get("end") or 0)))
    for i, s in enumerate(out):
        s["index"] = i
    return out


