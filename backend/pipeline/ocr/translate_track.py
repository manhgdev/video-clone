"""Independent OCR → translate track; never mutates speech subtitle cues."""
from __future__ import annotations

import uuid
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from pipeline.core.jobs import begin_job, check_cancel, clear_job
from pipeline.core.project import append_job_event, ensure_layout, load_meta, save_meta, set_status
from pipeline.core.resources import adaptive_workers
from pipeline.ocr.extract import asr_paddleocr
from pipeline.translate import translate_segments

_active_projects: set[str] = set()
_active_lock = threading.Lock()


def _norm_text(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def _align_cues_to_caption_track(
    cues: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Make OCR Caption 2 a one-to-one comparison with the main caption track.

    Full-frame OCR can keep a partial read alive across several dialogue cues.
    Selecting the best overlapping read for each existing caption prevents
    those stale windows from becoming simultaneous Caption 2 clips.
    """
    anchors = [
        segment for segment in segments
        if not segment.get("isCompound")
        and float(segment.get("end") or 0) > float(segment.get("start") or 0)
        and _norm_text(segment.get("source"))
    ]
    if not anchors:
        ordered = sorted((dict(cue) for cue in cues), key=lambda cue: (float(cue.get("start") or 0), float(cue.get("end") or 0)))
        for index, cue in enumerate(ordered[:-1]):
            next_start = float(ordered[index + 1].get("start") or 0)
            cue["end"] = max(float(cue.get("start") or 0) + 0.1, min(float(cue.get("end") or 0), next_start - 0.02))
        return ordered

    aligned: list[dict[str, Any]] = []
    for anchor in sorted(anchors, key=lambda item: float(item.get("start") or 0)):
        start = float(anchor.get("start") or 0)
        end = float(anchor.get("end") or start)
        anchor_text = _norm_text(anchor.get("source"))
        candidates: list[tuple[float, dict[str, Any]]] = []
        for cue in cues:
            cue_start = float(cue.get("start") or 0)
            cue_end = float(cue.get("end") or cue_start)
            overlap = max(0.0, min(end, cue_end) - max(start, cue_start))
            if overlap <= 0.02:
                continue
            cue_text = _norm_text(cue.get("source"))
            similarity = SequenceMatcher(None, anchor_text, cue_text).ratio() if cue_text else 0.0
            overlap_ratio = overlap / max(0.1, min(end - start, cue_end - cue_start))
            candidates.append((similarity * 2.0 + overlap_ratio, cue))
        if not candidates:
            continue
        score, best = max(candidates, key=lambda pair: pair[0])
        # Reject unrelated screen labels which only happen to share the time.
        if score < 0.75:
            continue
        aligned.append({**best, "start": start, "end": end})
    return aligned


def claim_ocr_translate(project_id: str) -> bool:
    with _active_lock:
        if project_id in _active_projects:
            return False
        _active_projects.add(project_id)
        return True


def release_ocr_translate(project_id: str) -> None:
    with _active_lock:
        _active_projects.discard(project_id)


def run_ocr_translate_track(project_id: str, settings: dict[str, Any]) -> None:
    generation = begin_job(project_id)
    try:
        meta = load_meta(project_id)
        video = Path(str(meta.get("workVideo") or meta.get("videoPath") or ""))
        if not video.is_file():
            raise RuntimeError("Không tìm thấy video nguồn cho OCR")
        set_status(project_id, step="asr", progress=8, message="OCR track độc lập…", running=True, error=None)
        cues = asr_paddleocr(video, project_id, workers=adaptive_workers(int(settings.get("workers") or 0), kind="cpu", cap=12), source_lang=str(settings.get("sourceLang") or "auto"), analysis_region=settings.get("analysisRegion"), stable=bool(settings.get("stableCaptionLocate")))
        check_cancel(project_id)
        cues = _align_cues_to_caption_track(cues, list(meta.get("segments") or []))
        texts = [str(item.get("source") or "") for item in cues]
        translated = translate_segments(texts, str(settings.get("targetLang") or "vi"), project_id=project_id, source_lang=str(settings.get("sourceLang") or "auto"), translator=str(settings.get("translator") or "google"), workers=adaptive_workers(int(settings.get("workers") or 0), kind="network", cap=12)) if texts else []
        overlays: list[dict[str, Any]] = []
        for cue, text in zip(cues, translated):
            box = cue.get("bbox") or {}
            overlays.append({"id": f"ocr-{uuid.uuid4().hex[:12]}", "start": float(cue.get("start") or 0), "end": max(float(cue.get("end") or 0), float(cue.get("start") or 0) + 0.1), "text": str(text or cue.get("source") or ""), "x": int(box.get("x") or 0), "y": int(box.get("y") or 0), "w": max(20, int(box.get("w") or 300)), "h": max(20, int(box.get("h") or 80)), "fontSize": int(cue.get("fontSize") or 36), "color": "#ffffff", "kind": "text", "track": "ocr", "ocrSource": str(cue.get("source") or "")})
        # Replace only prior OCR-track overlays; manual text/logo/watermark stay.
        meta = load_meta(project_id)
        existing = [item for item in meta.get("overlays") or [] if isinstance(item, dict) and item.get("track") != "ocr"]
        meta["overlays"] = existing + overlays
        meta["ocrOverlays"] = overlays
        save_meta(project_id, meta)
        append_job_event(project_id, "OCR_CHUNK_READY", {"overlays": overlays})
        set_status(project_id, step="translate", progress=100, message=f"Đã tạo {len(overlays)} OCR overlay", running=False)
    except Exception as exc:
        set_status(project_id, step="translate", progress=0, message=f"OCR track lỗi: {exc}", running=False, error=str(exc))
        raise
    finally:
        clear_job(project_id, generation)
        release_ocr_translate(project_id)
