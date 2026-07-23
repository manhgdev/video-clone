"""Video cleaner API routes."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile, File

from pipeline.cleaner.cleaner_jobs import (
    CLEANER_TEMP_DIR,
    cancel_job,
    create_job,
    delete_job,
    get_job,
    list_jobs,
    reveal_job_file,
)
from pipeline.cleaner.cleaner_ffmpeg import start_cleaner_job

router = APIRouter()

@router.get("/api/cleaner/jobs")
def api_cleaner_list():
    return list_jobs()

@router.post("/api/cleaner/jobs")
async def api_cleaner_start(
    files: list[UploadFile] = File(...),
    method: str = Form(...),
    options: str = Form(...)
):
    try:
        opts_dict = json.loads(options)
    except Exception:
        opts_dict = {}

    created_jobs = []
    
    for upload in files:
        if not upload.filename:
            continue
            
        ext = Path(upload.filename).suffix or ".mp4"
        temp_path = CLEANER_TEMP_DIR / f"temp_{uuid.uuid4().hex[:8]}{ext}"
        
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(upload.file, f)
            
        job = create_job(
            filename=upload.filename,
            method=method,
            options=opts_dict,
            input_path=str(temp_path)
        )
        created_jobs.append(job)
        start_cleaner_job(job["id"])
        
    return created_jobs

@router.get("/api/cleaner/jobs/{job_id}")
def api_cleaner_get(job_id: str):
    j = get_job(job_id)
    if not j:
        raise HTTPException(404, "Job không tồn tại")
    return j

@router.post("/api/cleaner/jobs/{job_id}/cancel")
def api_cleaner_cancel(job_id: str):
    if not cancel_job(job_id):
        raise HTTPException(404, "Job không tồn tại")
    return {"ok": True}

@router.delete("/api/cleaner/jobs/{job_id}")
def api_cleaner_delete(job_id: str):
    if not delete_job(job_id):
        raise HTTPException(404, "Job không tồn tại")
    return {"ok": True}

@router.post("/api/cleaner/jobs/{job_id}/reveal")
def api_cleaner_reveal(job_id: str):
    if not reveal_job_file(job_id):
        raise HTTPException(404, "Không tìm thấy file hoặc chưa hoàn thành")
    return {"ok": True}

from fastapi.responses import FileResponse
from pipeline.cleaner.cleaner_jobs import get_job_output_path

@router.get("/api/cleaner/jobs/{job_id}/file")
def api_cleaner_file(job_id: str):
    p = get_job_output_path(job_id)
    if not p:
        raise HTTPException(404, "Chưa có file")
    
    media = "video/mp4"
    if p.suffix.lower() == ".webm":
        media = "video/webm"
    elif p.suffix.lower() == ".mkv":
        media = "video/x-matroska"
        
    return FileResponse(p, media_type=media)
