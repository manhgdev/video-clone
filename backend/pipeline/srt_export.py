"""Standalone subtitle export jobs for audio, video and caption files."""
from __future__ import annotations

import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from pipeline.asr.whisper import asr_whisper
from pipeline.core.config import DATA
from pipeline.core.media import extract_audio
from pipeline.export.srt import SRT_STYLES, _split_for_style, parse_srt, style_params, wrap_capcut_text, write_subtitle

ROOT = DATA / "srt_export"
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def _update(job_id: str, **values: Any) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(values)


def list_jobs() -> list[dict[str, Any]]:
    with _LOCK:
        return sorted(_JOBS.values(), key=lambda job: float(job["createdAt"]), reverse=True)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return _JOBS.get(job_id)


def create_job(filename: str, input_path: Path, source_kind: str) -> dict[str, Any]:
    ROOT.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:10]
    work = ROOT / job_id
    work.mkdir(parents=True)
    copied = work / f"input{input_path.suffix.lower()}"
    shutil.move(str(input_path), copied)
    job = {
        "id": job_id, "filename": filename, "sourceKind": source_kind,
        "status": "queued", "progress": 0, "message": "Đang chờ xử lý",
        "error": None, "createdAt": time.time(), "outputDir": str(work),
        "inputPath": str(copied), "files": [], "cancelled": False,
    }
    with _LOCK:
        _JOBS[job_id] = job
    return job


def cancel_job(job_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    _update(job_id, cancelled=True, status="cancelled", message="Đã hủy")
    return True


def _caption_cues(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    cues = parse_srt(raw)
    if cues:
        return cues
    lines = [line.strip() for line in raw.splitlines() if line.strip() and line.strip().upper() != "WEBVTT"]
    return [{"start": index * 3.0, "end": index * 3.0 + 3.0, "text": line} for index, line in enumerate(lines)]


def _styled(cues: list[dict[str, Any]], style: str) -> list[dict[str, Any]]:
    params = style_params(style)
    result: list[dict[str, Any]] = []
    for cue in cues:
        start, end = float(cue["start"]), max(float(cue["end"]), float(cue["start"]) + 0.06)
        pieces = _split_for_style(str(cue.get("text") or ""), params)
        max_pieces = max(1, int((end - start) / 0.06))
        if len(pieces) > max_pieces:
            # ponytail: a very short original cue cannot safely host many 60ms captions.
            pieces = pieces[: max_pieces - 1] + [" ".join(pieces[max_pieces - 1 :])]
        weights = [max(1, len(piece)) for piece in pieces]
        total = sum(weights) or 1
        cursor = start
        for index, piece in enumerate(pieces):
            duration = (end - start) * weights[index] / total if index < len(pieces) - 1 else end - cursor
            next_cursor = max(cursor + 0.06, cursor + duration)
            result.append({"start": cursor, "end": min(end, next_cursor), "text": wrap_capcut_text(piece, params.wrap_line)})
            cursor = next_cursor
    return result


def _run(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    try:
        _update(job_id, status="processing", progress=5, message="Đang đọc đầu vào")
        source = Path(job["inputPath"])
        if job["sourceKind"] == "caption":
            cues = _caption_cues(source)
        else:
            _update(job_id, progress=12, message="Đang tách audio")
            wav = source.with_suffix(".wav")
            extract_audio(source, wav)
            if get_job(job_id).get("cancelled"):
                return
            _update(job_id, progress=25, message="Whisper đang nhận dạng")
            segments = asr_whisper(wav, "auto", workers=0)
            cues = [{"start": row["start"], "end": row["end"], "text": row["source"]} for row in segments]
        if not cues:
            raise RuntimeError("Không tìm thấy nội dung phụ đề")
        if get_job(job_id).get("cancelled"):
            return
        _update(job_id, progress=70, message="Đang tạo các định dạng phụ đề")
        work = Path(job["outputDir"])
        files: list[str] = []
        for style in SRT_STYLES:
            name = f"subtitles-{style}.srt"
            write_subtitle(work / name, _styled(cues, style), "srt", capcut=False)
            files.append(name)
        write_subtitle(work / "subtitles.vtt", _styled(cues, "hard"), "vtt", capcut=False)
        write_subtitle(work / "subtitles.txt", cues, "txt")
        files.extend(["subtitles.vtt", "subtitles.txt"])
        with zipfile.ZipFile(work / "subtitles-all.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name in files:
                archive.write(work / name, name)
        files.append("subtitles-all.zip")
        _update(job_id, status="done", progress=100, message=f"Đã xuất {len(files)} file", files=files)
    except Exception as exc:
        if not get_job(job_id).get("cancelled"):
            _update(job_id, status="error", error=str(exc), message="Xuất phụ đề thất bại")


def start(job_id: str) -> None:
    threading.Thread(target=_run, args=(job_id,), name=f"srt-export-{job_id}", daemon=True).start()
