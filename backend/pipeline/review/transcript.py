"""Transcript: local subtitles/Whisper or CapCut cloud recognition."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from pipeline.asr.whisper import asr_whisper
from pipeline.core.jobs import check_cancel
from pipeline.core.media import _ff_bin, extract_audio
from pipeline.subtitles import subtitle_segments

_FF_LANG = {
    "zh": ("zh", "chi", "zho", "cmn", "zh-cn", "zh-tw", "zh-hans", "zh-hant"),
    "en": ("en", "eng"),
    "ja": ("ja", "jpn"),
    "ko": ("ko", "kor"),
    "vi": ("vi", "vie"),
}


def _detect_filename_lang(source: Path) -> str:
    name = source.name
    if re.search(r"[\u4e00-\u9fff]", name):
        return "zh"
    if re.search(r"[\u3040-\u30ff]", name):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", name):
        return "ko"
    return "auto"


def load_transcript(
    source: Path,
    cache_dir: Path,
    *,
    job_id: str | None = None,
    duration: float = 0,
    sidecar: str = "",
    source_lang: str = "auto",
    recognition_engine: str = "whisper",
    target_lang: str = "vi",
) -> list[dict[str, Any]]:
    engine = (recognition_engine or "whisper").strip().lower()
    if engine == "capcut":
        return _capcut_transcript(source, job_id=job_id, source_lang=source_lang, target_lang=target_lang)
    lang = (source_lang or "auto").strip() or "auto"
    if lang == "auto":
        detected = _detect_filename_lang(source)
        if detected != "auto":
            lang = detected
    for cand in _subtitle_candidates(source, sidecar):
        try:
            rows = subtitle_segments(cand)
        except OSError:
            continue
        if rows:
            mapped = [_row(r) for r in rows]
            if _script_matches_source(" ".join(x["text"] for x in mapped[:40]), lang):
                return mapped
    extracted = cache_dir / f"embedded_{lang}.srt"
    if _extract_embedded(source, extracted, lang=lang) and extracted.is_file():
        try:
            rows = subtitle_segments(extracted)
            if rows:
                mapped = [_row(r) for r in rows]
                if _script_matches_source(" ".join(x["text"] for x in mapped[:40]), lang):
                    return mapped
        except OSError:
            pass
    return _whisper_chunks(source, cache_dir, job_id=job_id, duration=duration, source_lang=lang)


def _capcut_transcript(source: Path, *, job_id: str | None, source_lang: str, target_lang: str) -> list[dict[str, Any]]:
    """Use cloud CapCut STT only — never load/extract audio for Whisper."""
    def note(message: str) -> None:
        if not job_id:
            return
        try:
            from pipeline.review.run import _note
            _note(job_id, message)
        except Exception:
            pass

    from pipeline.capcut_stt import transcribe_and_translate

    note("Transcript: đang gửi video cho CapCut…")
    rows, _translated = transcribe_and_translate(
        source, source_lang or "auto", target_lang or "vi", require_translation=False,
        cancelled=(lambda: _check_review_cancel(job_id)), progress=note,
    )
    note(f"Transcript: CapCut hoàn tất · {len(rows)} câu")
    return rows


def _check_review_cancel(job_id: str | None) -> bool:
    if not job_id:
        return False
    try:
        check_cancel(job_id)
    except Exception:
        return True
    return False


def _row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": float(item.get("start") or 0),
        "end": float(item.get("end") or 0),
        "text": str(item.get("source") or item.get("text") or "").strip(),
    }


def _script_matches_source(text: str, lang: str) -> bool:
    """Skip English/CJK-mismatched SRT when the user set an explicit source language."""
    if lang in ("", "auto"):
        return True
    blob = (text or "").strip()
    if not blob:
        return False
    n = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", blob))
    if lang in {"zh", "ja", "ko"}:
        return n >= 8 or n * 20 >= len(blob)
    if lang in {"vi", "en"}:
        return n < max(8, len(blob) // 6)
    return True


def _subtitle_candidates(source: Path, sidecar: str) -> list[Path]:
    out: list[Path] = []
    if sidecar:
        out.append(Path(sidecar))
    for ext in (".srt", ".vtt"):
        out.append(source.with_suffix(ext))
    return [p for p in out if p.is_file()]


def _subtitle_stream_index(source: Path, lang: str) -> int | None:
    if lang in ("", "auto"):
        return None
    aliases = _FF_LANG.get(lang, (lang,))
    cmd = [
        _ff_bin("ffprobe"), "-v", "error", "-show_streams",
        "-select_streams", "s", "-of", "json", str(source),
    ]
    try:
        data = json.loads(subprocess.check_output(cmd, text=True, timeout=30))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    for s in data.get("streams") or []:
        tag = str((s.get("tags") or {}).get("language") or "").lower()
        if tag in aliases:
            try:
                return int(s["index"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _extract_embedded(source: Path, dest: Path, *, lang: str = "auto") -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    idx = _subtitle_stream_index(source, lang)
    # Explicit source lang + no matching track → Whisper, don't grab the wrong SRT.
    if lang not in ("", "auto") and idx is None:
        return False
    mapped = f"0:{idx}" if idx is not None else "0:s:0"
    try:
        proc = subprocess.run(
            [_ff_bin("ffmpeg"), "-y", "-i", str(source), "-map", mapped, str(dest)],
            capture_output=True,
            timeout=120,
        )
        return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 8
    except (OSError, subprocess.SubprocessError):
        dest.unlink(missing_ok=True)
        return False


def _whisper_chunks(
    source: Path,
    cache_dir: Path,
    *,
    job_id: str | None,
    duration: float,
    source_lang: str = "auto",
) -> list[dict[str, Any]]:
    total_duration = max(0.0, float(duration or 0))
    last_percent = -1

    def note(message: str) -> None:
        if not job_id:
            return
        try:
            from pipeline.review.run import _note
            _note(job_id, message)
        except Exception:
            pass

    wav = cache_dir / "audio_full.wav"
    note("Transcript: đang chuẩn bị audio cho Whisper…")
    extract_audio(source, wav, project_id=job_id)
    if job_id:
        check_cancel(job_id)
    note("Transcript: audio đã sẵn sàng · Whisper đang nhận dạng…")

    def on_progress(count: int, second: float) -> None:
        nonlocal last_percent
        if count:
            # This is Whisper's own progress (decoded audio time / source
            # duration), never the enclosing Review pipeline percentage.
            percent = min(100, max(0, round(float(second or 0) * 100 / total_duration))) if total_duration else None
            if percent is not None and percent == last_percent and count > 1:
                return
            if percent is not None:
                last_percent = percent
            progress = f" · {percent}%" if percent is not None else ""
            note(f"Transcript: Whisper đã nhận {count} câu{progress} · ~{second:.0f}s")
        else:
            note("Transcript: Whisper đang khởi động…")

    rows = asr_whisper(
        wav, source_lang or "auto", workers=4, project_id=job_id,
        on_progress=on_progress,
    )
    note(f"Transcript: Whisper hoàn tất · {len(rows)} câu")
    return [_row(r) for r in rows]
