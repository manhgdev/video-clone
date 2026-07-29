"""API for the standalone subtitle exporter."""
from __future__ import annotations

import shutil
import urllib.request
import uuid
from pathlib import Path

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
):
    kind = source_kind if source_kind in {"media", "caption", "manual", "url"} else "media"
    if kind == "manual":
        if not manual_text.strip():
            raise HTTPException(400, "Chưa nhập nội dung caption")
        suffix, filename = ".txt", "caption-input.txt"
    elif kind == "url":
        if not source_url.strip() or not source_url.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "URL phải bắt đầu bằng http:// hoặc https://")
        suffix = Path(source_url.split("?", 1)[0]).suffix.lower() or ".mp3"
        if suffix not in (_CAPTION_EXTS | _MEDIA_EXTS):
            raise HTTPException(400, "URL không có định dạng audio, video hoặc caption được hỗ trợ")
        filename = Path(source_url.split("?", 1)[0]).name or f"source{suffix}"
    else:
        if not file or not file.filename:
            raise HTTPException(400, "Chưa chọn file")
        suffix = Path(file.filename).suffix.lower()
        if suffix not in (_CAPTION_EXTS if kind == "caption" else _MEDIA_EXTS):
            raise HTTPException(400, "Định dạng file không phù hợp")
        filename = file.filename
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
    job = create_job(filename, temp, "caption" if kind == "manual" or suffix in _CAPTION_EXTS else "media")
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
