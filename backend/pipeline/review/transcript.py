"""Transcript: embedded/external SRT first, Whisper ASR in chunks as fallback."""
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


def load_transcript(
    source: Path,
    cache_dir: Path,
    *,
    job_id: str | None = None,
    duration: float = 0,
    sidecar: str = "",
    source_lang: str = "auto",
) -> list[dict[str, Any]]:
    lang = (source_lang or "auto").strip() or "auto"
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
    wav = cache_dir / "audio_full.wav"
    extract_audio(source, wav)
    if job_id:
        check_cancel(job_id)
    rows = asr_whisper(wav, source_lang or "auto", workers=4, project_id=job_id)
    return [_row(r) for r in rows]
