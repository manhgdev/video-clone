"""EditPlan → existing project timeline/segments so the shared Editor can open it."""
from __future__ import annotations

from statistics import median
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
        # Cover replaces the original subtitle in its own lane. "below" here
        # placed the translated caption outside the blur band and could push it
        # toward the middle after a clip was resized.
        "captionPlacement": "over" if mode == "cover" else ("above" if mode == "above" else "below"),
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
    """Return one subtitle row, never the union of unrelated OCR labels."""
    fallback = _fallback_review_bbox(width, height)
    # OCR can see a lower-third subtitle together with a corner logo/badge.
    # Group words into rows first; taking one giant y-union was what made the
    # cover jump to a label when both happened to be in the lower quarter.
    bottom_boxes = [box for box in boxes if box[1] >= height * 0.70]
    if not bottom_boxes:
        return fallback
    rows: list[list[tuple[int, int, int, int]]] = []
    y_tolerance = max(8, round(height * 0.022))
    for box in sorted(bottom_boxes, key=lambda item: (item[1] + item[3]) / 2):
        center = (box[1] + box[3]) / 2
        for row in rows:
            row_center = median((item[1] + item[3]) / 2 for item in row)
            if abs(center - row_center) <= y_tolerance:
                row.append(box)
                break
        else:
            rows.append([box])

    def row_score(row: list[tuple[int, int, int, int]]) -> tuple[float, float]:
        x0 = min(item[0] for item in row)
        x1 = max(item[2] for item in row)
        center_x = (x0 + x1) / 2
        span = x1 - x0
        # A small edge-only label is normally a watermark, not a subtitle.
        edge_label = span < width * 0.12 and (center_x < width * 0.24 or center_x > width * 0.76)
        if edge_label:
            return (-1.0, 0.0)
        center_y = median((item[1] + item[3]) / 2 for item in row)
        return (min(1.0, span / max(1, width)) + min(0.25, len(row) * 0.05) + center_y / max(1, height) * 0.10, center_y)

    chosen = max(rows, key=row_score)
    if row_score(chosen)[0] < 0:
        return fallback
    y0 = min(box[1] for box in chosen)
    y1 = max(box[3] for box in chosen)
    # OCR thường chỉ trả phần ruột ký tự và bỏ stroke/viền ngoài. Cover phải
    # tràn lên trên đủ xa để không còn lộ viền subtitle gốc, nhất là CJK có
    # outline dày; vẫn giữ dải dưới cùng để không bắt nhầm watermark ở giữa.
    pad_top = max(8, round(height * 0.018))
    pad_bottom = max(7, round(height * 0.012))
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
    """Collect actual subtitle candidates from the start, middle and end.

    Missing OCR is deliberately omitted instead of represented by ``fallback``:
    a real subtitle row can legitimately sit at exactly the fallback position.
    """
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
            # Three samples per temporal third make a transient title card or
            # animated brand label lose to the persistent subtitle lane.
            for fraction in (0.06, 0.16, 0.28, 0.42, 0.50, 0.58, 0.72, 0.84, 0.94):
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
        finally:
            cap.release()
        return bands
    except Exception:
        return []


def locate_review_caption_band(video: Path) -> dict[str, int]:
    """Resolve one stable Review subtitle lane from all sampled frames.

    Review narration is intentionally fixed on screen.  Choosing an OCR box
    per cue makes both the blur mask and translated caption jump whenever OCR
    sees a different subtitle line or a false positive.
    """
    from pipeline.core.media import video_size

    width, height = video_size(video)
    fallback = _fallback_review_bbox(width, height)
    candidates = locate_review_caption_bands(video)
    if not candidates:
        return fallback

    # Cluster rows by geometry. A winning group must recur in at least two of
    # the start/middle/end thirds; this rejects an animated label, scene title
    # or logo that only occurs during one part of the movie.
    groups: list[list[tuple[float, dict[str, int]]]] = []
    y_tolerance = max(10, round(height * 0.028))
    h_tolerance = max(10, round(height * 0.030))
    for sample in sorted(candidates, key=lambda item: item[1]["y"]):
        fraction, box = sample
        for group in groups:
            gy = median(int(item[1]["y"]) for item in group)
            gh = median(int(item[1]["h"]) for item in group)
            if abs(int(box["y"]) - gy) <= y_tolerance and abs(int(box["h"]) - gh) <= h_tolerance:
                group.append(sample)
                break
        else:
            groups.append([sample])

    stable_groups = [
        group for group in groups
        if len(group) >= 2 and len({min(2, int(fraction * 3)) for fraction, _box in group}) >= 2
    ]
    if not stable_groups:
        return fallback

    def group_score(group: list[tuple[float, dict[str, int]]]) -> tuple[int, int, float]:
        thirds = len({min(2, int(fraction * 3)) for fraction, _box in group})
        # For equally persistent rows, subtitles are normally lower than UI.
        y = median(int(box["y"]) for _fraction, box in group)
        return (thirds, len(group), y)

    winner = max(stable_groups, key=group_score)
    return {
        "x": 0,
        "y": int(round(median(int(box["y"]) for _fraction, box in winner))),
        "w": max(64, int(width or fallback["w"])),
        "h": max(24, int(round(median(int(box["h"]) for _fraction, box in winner)))),
    }


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
