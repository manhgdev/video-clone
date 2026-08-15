"""Chuyển TextOverlay editor (text/logo/effect) thành cue burn."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.core.media import retime_timeline_time


def _logo_schedule(item: dict[str, Any], st: float, en: float, x: float, y: float) -> list[tuple[float, float, float, float, float]]:
    if str(item.get("motion") or "fixed") != "random":
        return [(st, en, x, y, 0.0)]
    frames = item.get("positionKeyframes") or [{"at": st, "x": x, "y": y}]
    visible = max(0.5, float(item.get("visibleSec") or 4))
    fade = min(max(0.0, float(item.get("fadeSec") or 0.5)), visible / 2)
    return [
        (fst, min(en, fst + visible), float(frame.get("x") or 0), float(frame.get("y") or 0), fade)
        for frame in frames
        if (fst := max(st, float(frame.get("at") if frame.get("at") is not None else st))) < en
    ]


def build_text_overlay_cues(
    meta: dict[str, Any],
    root: Path,
    project_id: str,
    *,
    export_start: float,
    vid_dur: float,
    source_timeline_duration: float,
    source_timeline_segments: list[dict[str, Any]],
    retime_base: float,
) -> list[dict[str, Any]]:
    """Free text + effect + logo — cùng hệ tọa độ pixel với segment burn."""
    # Free text + effect regions (làm mờ tự do): cùng hệ tọa độ pixel.
    text_overlays: list[dict[str, Any]] = []
    settings = meta.get("settings") or {}
    for item in meta.get("overlays") or []:
        # Automatic watermark clips follow the main "cover original logo"
        # switch. A user-created effect remains independent.
        if item.get("watermarkSource") and not bool(settings.get("coverLogo")):
            continue
        item_start = float(item.get("start") or 0) - export_start
        if item_start >= vid_dur:
            continue
        kind = str(item.get("kind") or "text").lower()
        x = float(item.get("x") or 0)
        y = float(item.get("y") or 0)
        w = float(item.get("w") or 0)
        h = float(item.get("h") or 0)
        if w < 4 or h < 4:
            continue
        st = retime_timeline_time(
            item_start,
            source_timeline_duration,
            source_timeline_segments,
            base_speed=retime_base,
        )
        en = retime_timeline_time(
            float(item.get("end") or 0) - export_start,
            source_timeline_duration,
            source_timeline_segments,
            base_speed=retime_base,
        )
        if kind == "logo":
            asset_path = ""
            asset_url = str(item.get("assetUrl") or "")
            if asset_url.startswith(f"/data/{project_id}/"):
                candidate = (root / asset_url.split(f"/data/{project_id}/", 1)[1]).resolve()
                if root.resolve() in candidate.parents and candidate.is_file():
                    asset_path = str(candidate)
            for index, (fst, fen, fx, fy, fade) in enumerate(_logo_schedule(item, st, en, x, y)):
                if fen <= fst:
                    continue
                source = str(item.get("logoSource") or "text")
                text = str(item.get("text") or "Logo").strip() or "Logo"
                fs = int(item.get("fontSize") or 42)
                text_overlays.append({
                    "id": f"logo-{item.get('id', '')}-{index}", "start": fst, "end": fen,
                    "translation": text if source == "text" else "logo", "source": "", "layout": "horizontal",
                    "fontSize": fs, "fontFamily": str(item.get("fontFamily") or "system"),
                    "textColor": str(item.get("color") or "#ffffff"),
                    "bbox": {"x": fx, "y": fy, "w": w, "h": h},
                    "captionLayout": {"x": fx, "y": fy, "w": w, "h": h, "lines": [text], "fontSize": fs},
                    "skipCoverMask": True, "logoText": source == "text",
                    "logoAssetPath": asset_path if source != "text" else "",
                    "logoOpacity": max(0, min(100, int(item.get("opacity") or 85))) / 100,
                    "logoFadeInEnd": fst + fade, "logoFadeOutStart": fen - fade,
                })
            continue
        if kind == "effect":
            # Vùng hiệu ứng: chỉ mask, không chữ
            text_overlays.append(
                {
                    "id": f"fx-{item.get('id', '')}",
                    "start": st,
                    "end": en,
                    "coverStart": st,
                    "coverEnd": en,
                    "translation": "",
                    "source": "",
                    "layout": "horizontal",
                    "bbox": {"x": x, "y": y, "w": w, "h": h},
                    "maskOnly": True,
                    "skipCoverMask": False,
                    "coverMaskStyle": str(item.get("maskStyle") or "blur"),
                    "coverMaskColor": str(item.get("maskColor") or "#4c1d95"),
                    "coverMaskOpacity": int(item.get("maskOpacity") if item.get("maskOpacity") is not None else 40),
                }
            )
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        fs = int(item.get("fontSize") or 42)
        lines = [ln if ln.strip() else " " for ln in text.splitlines()] or [text]
        text_overlays.append(
            {
                "id": f"overlay-{item.get('id', '')}",
                "start": st,
                "end": en,
                "translation": text,
                "source": "",
                "layout": "horizontal",
                "fontSize": fs,
                "textColor": str(item.get("color") or "#ffffff"),
                "bbox": {"x": x, "y": y, "w": w, "h": h},
                "captionLayout": {
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "lines": lines,
                    "fontSize": fs,
                },
                # Preview không blur dưới free-text — không mask khi burn
                "skipCoverMask": True,
                # textarea preview: top-align + line-height 1.25 (css mode overlay)
                "overlayText": True,
            }
        )
    return text_overlays
