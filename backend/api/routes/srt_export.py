"""API for the standalone subtitle exporter."""
from __future__ import annotations

import shutil
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from pipeline.srt_export import ROOT, cancel_job, create_job, get_job, list_jobs, start

router = APIRouter()
_CAPTION_EXTS = {".srt", ".vtt", ".txt"}
_MEDIA_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


@router.get("/api/srt-export/jobs")
def jobs():
    return list_jobs()


@router.post("/api/srt-export/jobs")
async def create(
    file: UploadFile | None = File(None),
    source_kind: str = Form("media"),
    manual_text: str = Form(""),
    source_url: str = Form(""),
    output_mode: str = Form("original"),
    source_lang: str = Form("auto"),
    target_lang: str = Form("vi"),
    translator: str = Form("google"),
    workers: int = Form(0),
    ollama_mode: str = Form("cloud"),
    ollama_model: str = Form("minimax-m3:cloud"),
    ollama_local_tier: str = Form("balanced"),
    recognition_engine: str = Form("whisper"),
):
    kind = source_kind if source_kind in {"media", "caption", "manual", "url"} else "media"
    if kind == "manual":
        if not manual_text.strip():
            raise HTTPException(400, "Chưa nhập nội dung caption")
        suffix, filename = ".txt", "caption-input.txt"
    elif kind == "url":
        if not source_url.strip() or not source_url.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "URL phải bắt đầu bằng http:// hoặc https://")
        host = (urlparse(source_url.strip()).hostname or "").lower()
        if not host:
            raise HTTPException(400, "URL không hợp lệ")
        suffix = Path(source_url.split("?", 1)[0]).suffix.lower()
        filename = host or (Path(source_url.split("?", 1)[0]).name or f"source{suffix}")
    else:
        if not file or not file.filename:
            raise HTTPException(400, "Chưa chọn file")
        suffix = Path(file.filename).suffix.lower()
        if suffix not in (_CAPTION_EXTS if kind == "caption" else _MEDIA_EXTS):
            raise HTTPException(400, "Định dạng file không phù hợp")
        filename = file.filename
    options = {
        "outputMode": output_mode if output_mode in {"original", "translated", "bilingual"} else "original",
        "sourceLang": source_lang or "auto", "targetLang": target_lang or "vi",
        "translator": translator or "google", "workers": max(0, workers),
        "ollamaMode": ollama_mode, "ollamaModel": ollama_model,
        "ollamaLocalTier": ollama_local_tier,
        "recognitionEngine": recognition_engine if recognition_engine in {"whisper", "capcut"} else "whisper",
    }
    if kind == "url":
        job = create_job(filename, None, "platform", source_url=source_url.strip(), options=options)
        start(job["id"])
        return job

    ROOT.mkdir(parents=True, exist_ok=True)
    temp = ROOT / f"upload-{uuid.uuid4().hex}{suffix}"
    try:
        if kind == "manual":
            temp.write_text(manual_text, encoding="utf-8")
        elif kind == "url":
            request = urllib.request.Request(source_url.strip(), headers={"User-Agent": "ZM-Tool/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response, temp.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        else:
            with temp.open("wb") as stream:
                shutil.copyfileobj(file.file, stream)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise HTTPException(400, f"Không thể đọc nguồn: {exc}") from exc
    job = create_job(filename, temp, "caption" if kind == "manual" or suffix in _CAPTION_EXTS else "media", options=options)
    start(job["id"])
    return job


@router.get("/api/srt-export/jobs/{job_id}")
def status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Không thấy job")
    return job


@router.post("/api/srt-export/jobs/{job_id}/cancel")
def cancel(job_id: str):
    if not cancel_job(job_id):
        raise HTTPException(404, "Không thấy job")
    return {"ok": True}


@router.get("/api/srt-export/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str):
    job = get_job(job_id)
    if not job or name not in job.get("files", []):
        raise HTTPException(404, "Không thấy file")
    path = Path(job["outputDir"]) / name
    if not path.is_file():
        raise HTTPException(404, "Không thấy file")
    return FileResponse(path, filename=name)
