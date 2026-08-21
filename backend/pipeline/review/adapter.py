"""EditPlan → existing project timeline/segments so the shared Editor can open it."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.core.project import load_meta, save_meta, set_status
from pipeline.review.match import resolve_build_mode


def caption_export_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Map Review captionMode onto Clone export flags, including no captions."""
    lang = str(settings.get("language") or "vi")
    on = bool(settings.get("subtitle", True))
    mode = str(settings.get("captionMode") or "cover")
    if mode not in {"off", "cover", "below", "above"}:
        mode = "cover"
    if not on or mode == "off":
        return {
            "targetLang": "none",
            "burnSubs": False,
            "coverHardsubs": False,
            "captionPlacement": "below",
        }
    return {
        "targetLang": lang,
        # The transcript-led Review script is the localized caption track.
        # Burn it in the detected source-subtitle lane for the cover mode too,
        # so exported video and Live Preview show the same translated caption.
        "burnSubs": True,
        "coverHardsubs": mode == "cover",
        "captionPlacement": "above" if mode == "above" else "below",
    }


def locate_review_captions(
    project_id: str,
    compiled: Path,
    segments: list[dict[str, Any]],
    settings: dict[str, Any],
) -> int:
    """OCR-locate original hardsubs on the compiled review video (same helper as Clone)."""
    flags = caption_export_settings(settings)
    if not segments or not (flags["burnSubs"] or flags["coverHardsubs"]):
        return 0
    from pipeline.ocr.locate import attach_speech_hardsub_boxes

    n = attach_speech_hardsub_boxes(
        compiled,
        segments,
        only_missing=False,
        project_id=project_id,
        stable=bool(settings.get("stableCaptionLocate", False)),
        analysis_region=settings.get("analysisRegion"),
    )
    if flags["coverHardsubs"]:
        # Review narration is translated text, so matching it against the
        # source-language hardsub often returns zero boxes. Do not make export
        # OCR every cue a second time: use one stable lower-third band for
        # missing boxes. Users can still adjust it later in the shared editor.
        from pipeline.core.media import video_size

        width, height = video_size(compiled)
        fallback = _fallback_review_bbox(width, height)
        for segment in segments:
            if not isinstance(segment.get("bbox"), dict):
                segment["bbox"] = dict(fallback)
                segment["bboxInherited"] = True
                segment["layout"] = "horizontal"
    meta = load_meta(project_id) or {}
    meta["segments"] = segments
    meta["bboxLocateVersion"] = 3
    save_meta(project_id, meta)
    return int(n or 0)


def _fallback_review_bbox(width: int, height: int) -> dict[str, int]:
    """Fixed bottom band for Review; no per-caption OCR is required."""
    width, height = max(64, int(width or 1920)), max(64, int(height or 1080))
    return {
        "x": 0,
        "y": round(height * 0.80),
        "w": width,
        "h": max(24, height - round(height * 0.80)),
    }


def _review_caption_box_from_boxes(
    boxes: list[tuple[int, int, int, int]], width: int, height: int
) -> dict[str, int]:
    """Return a padded OCR box that covers only the visible source subtitle."""
    fallback = _fallback_review_bbox(width, height)
    if not boxes:
        return fallback
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    pad_x = max(round(width * 0.015), round(max(1, x1 - x0) * 0.04))
    pad_y = max(round(height * 0.012), round(max(1, y1 - y0) * 0.60))
    left, top = max(0, x0 - pad_x), max(0, y0 - pad_y)
    right, bottom = min(width, x1 + pad_x), min(height, y1 + pad_y)
    return {"x": left, "y": top, "w": max(24, right - left), "h": max(24, bottom - top)}


def locate_review_caption_bands(video: Path) -> list[tuple[float, dict[str, int]]]:
    """Sample start/middle/end subtitle boxes and reuse them without per-cue OCR."""
    from pipeline.core.media import ffprobe_duration, video_size

    width, height = video_size(video)
    fallback = _fallback_review_bbox(width, height)
    try:
        import cv2
        from pipeline.ocr.extract import _rapidocr_labels
        from pipeline.ocr.locate import ocr_mid_hardsub_boxes

        duration = max(0.1, float(ffprobe_duration(video) or 0))
        ocr = _rapidocr_labels()
        cap = cv2.VideoCapture(str(video))
        bands: list[tuple[float, dict[str, int]]] = []
        try:
            for fraction in (0.12, 0.31, 0.50, 0.69, 0.88):
                cap.set(cv2.CAP_PROP_POS_MSEC, duration * fraction * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                found, _text = ocr_mid_hardsub_boxes(frame, ocr)
                if found:
                    bands.append((fraction, _review_caption_box_from_boxes(found, width, height)))
        finally:
            cap.release()
        return bands or [(0.5, fallback)]
    except Exception:
        return [(0.5, fallback)]


def apply_edit_plan(
    project_id: str,
    compiled: Path,
    plan: dict[str, Any],
    *,
    settings: dict[str, Any],
    voice: str,
) -> dict[str, Any]:
    meta = load_meta(project_id) or {}
    segments: list[dict[str, Any]] = []
    for i, seg in enumerate(plan.get("segments") or []):
        start = float(seg.get("voice_start") or 0)
        end = float(seg.get("voice_end") or start)
        audio = str(seg.get("audio") or "")
        text = str(seg.get("text") or "")
        item = {
            "id": str(seg.get("voice_id") or f"v{i:03d}"),
            "index": i,
            "start": start,
            "end": end,
            # Review narration has no source-language subtitle counterpart.
            # Keeping the same text in both fields made Editor show a fake
            # “original + translation” duplicate.
            "source": "",
            "translation": text,
            "sourceSubtitle": "",
            "dubSubtitle": text,
            "voice": voice,
            "dub": True,
            "layout": "horizontal",
        }
        if audio:
            item["audioFile"] = Path(audio).name
            item["audioUrl"] = f"/api/projects/{project_id}/tts/{Path(audio).name}"
            item["audioDuration"] = max(0.0, end - start)
        segments.append(item)
    meta["videoPath"] = str(compiled)
    meta["duration"] = float(plan.get("duration") or 0)
    meta["kind"] = "review"
    meta["editPlan"] = plan
    meta["segments"] = segments
    meta["settings"] = {
        **(meta.get("settings") or {}),
        **settings,
        **caption_export_settings(settings),
        "previewSec": 0,
        "exportVideo": True,
        "matchDuration": "preferAudio" if resolve_build_mode(settings) == "stretch" else "preferVideo",
        "processOriginalAudio": True,
        "originalAudioMode": "mute" if float(settings.get("originalAudioPct") or 0) <= 0.5 else "original",
        "originalAudioVolume": int(max(0, min(100, float(settings.get("originalAudioPct") or 0)))),
    }
    save_meta(project_id, meta)
    set_status(project_id, step="export", progress=80, message="Đã dựng timeline Review", running=True)
    return meta
