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
    """Slim, compact horizontal bottom band for Review (lower ~6.5%)."""
    width, height = max(64, int(width or 1920)), max(64, int(height or 1080))
    band_h = max(36, round(height * 0.065))
    return {
        "x": 0,
        "y": max(0, height - band_h - round(height * 0.025)),
        "w": width,
        "h": band_h,
    }


def _review_caption_box_from_boxes(
    boxes: list[tuple[int, int, int, int]], width: int, height: int
) -> dict[str, int]:
    """Return a slim, compact horizontal blur band hugging the subtitle lane closely."""
    fallback = _fallback_review_bbox(width, height)
    # Lọc chỉ lấy các box nằm ở dải phụ đề đáy (y >= 70% chiều cao khung hình)
    bottom_boxes = [box for box in boxes if box[1] >= height * 0.70]
    if not bottom_boxes:
        return fallback
    y0 = min(box[1] for box in bottom_boxes)
    y1 = max(box[3] for box in bottom_boxes)
    # Khoảng đệm gọn gàng ôm sát chữ (4-6px), không bị dư thừa chiều cao trên dưới
    pad_top = max(4, round(height * 0.006))
    pad_bottom = max(4, round(height * 0.005))
    top = max(round(height * 0.75), y0 - pad_top)
    bottom = min(height, y1 + pad_bottom)
    band_h = bottom - top
    return {
        "x": 0,
        "y": max(0, min(height - band_h, top)),
        "w": width,
        "h": band_h,
    }


def locate_review_caption_bands(video: Path) -> list[tuple[float, dict[str, int]]]:
    """Sample subtitle boxes strictly in the bottom subtitle lane (y >= 75%)."""
    from pipeline.core.media import ffprobe_duration, video_size

    width, height = video_size(video)
    fallback = _fallback_review_bbox(width, height)
    try:
        import cv2
        from pipeline.ocr.extract import _rapidocr_labels

        duration = max(0.1, float(ffprobe_duration(video) or 0))
        ocr = _rapidocr_labels()
        cap = cv2.VideoCapture(str(video))
        bands: list[tuple[float, dict[str, int]]] = []
        try:
            for fraction in (0.10, 0.25, 0.40, 0.55, 0.70, 0.85):
                cap.set(cv2.CAP_PROP_POS_MSEC, duration * fraction * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                h, w = frame.shape[:2]
                y_crop = int(h * 0.75)
                bottom_roi = frame[y_crop:h, 0:w]
                results, _ = ocr(bottom_roi)
                boxes: list[tuple[int, int, int, int]] = []
                if results:
                    for item in results:
                        pts = item[0]
                        bx0 = int(min(p[0] for p in pts))
                        by0 = int(min(p[1] for p in pts)) + y_crop
                        bx1 = int(max(p[0] for p in pts))
                        by1 = int(max(p[1] for p in pts)) + y_crop
                        boxes.append((bx0, by0, bx1, by1))
                if boxes:
                    bands.append((fraction, _review_caption_box_from_boxes(boxes, width, height)))
                else:
                    bands.append((fraction, fallback))
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
            item["audioDuration"] = float(seg.get("audio_duration") or max(0.0, end - start))
            item["ttsSpeed"] = float(seg.get("tts_speed") or 1.0)
            item["ttsBake"] = float(seg.get("tts_bake") or 1.0)
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
