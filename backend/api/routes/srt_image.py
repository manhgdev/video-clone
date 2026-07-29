"""Ghép ảnh/video theo timeline prompt và SRT."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from pipeline.srt_image import ROOT, cancel, create_job, get_job, list_jobs, output_path, pause, start

router = APIRouter()
MEDIA_SUFFIXES = {
    ".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
}


def _natural_name(path: Path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


@router.get("/api/srt-image/jobs")
def jobs():
    return list_jobs()


@router.post("/api/srt-image/jobs")
async def create(
    images: list[UploadFile] | None = File(None),
    media_folder: str = Form(""),
    timeline: UploadFile | None = File(None),
    srt: UploadFile | None = File(None),
    audio: UploadFile | None = File(None),
    watermark: UploadFile | None = File(None),
    timeline_path: str = Form(""),
    srt_path: str = Form(""),
    audio_path: str = Form(""),
    watermark_path: str = Form(""),
    options: str = Form("{}"),
    output_name: str = Form("ghep-anh-srt.mp4"),
    output_path: str = Form(""),
):
    work = ROOT / f"upload_{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True)
    image_paths: list[Path] = []
    if media_folder.strip():
        folder = Path(media_folder.strip()).expanduser().resolve()
        if not folder.is_dir():
            raise HTTPException(400, "Thư mục ảnh/video không tồn tại")
        image_paths = sorted(
            (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES),
            key=_natural_name,
        )
    else:
        for index, upload in enumerate(images or []):
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in MEDIA_SUFFIXES:
                continue
            target = work / f"{index:05d}{suffix}"
            with target.open("wb") as stream:
                shutil.copyfileobj(upload.file, stream)
            image_paths.append(target)
    if not image_paths:
        raise HTTPException(400, "Chưa chọn ảnh hoặc video hợp lệ")
    def copy_input(raw_path: str, upload: UploadFile | None, target_name: str,
                   suffixes: set[str], required: bool = False) -> Path | None:
        if raw_path.strip():
            source = Path(raw_path.strip()).expanduser().resolve()
            if not source.is_file() or source.suffix.lower() not in suffixes:
                raise HTTPException(400, f"File {target_name} không hợp lệ hoặc không còn tồn tại")
            target = work / f"{target_name}{source.suffix.lower()}"
            shutil.copy2(source, target)
            return target
        if upload and upload.filename:
            suffix = Path(upload.filename).suffix.lower()
            if suffix not in suffixes:
                raise HTTPException(400, f"File {target_name} không đúng định dạng")
            target = work / f"{target_name}{suffix}"
            with target.open("wb") as stream:
                shutil.copyfileobj(upload.file, stream)
            return target
        if required:
            raise HTTPException(400, f"Chưa chọn file {target_name}")
        return None

    timeline_file = copy_input(timeline_path, timeline, "timeline", {".txt"}, True)
    srt_file = copy_input(srt_path, srt, "subtitles", {".srt"}, True)
    audio_file = copy_input(
        audio_path, audio, "audio", {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"},
    )
    watermark_file = copy_input(
        watermark_path, watermark, "watermark", {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp"},
    )
    try:
        opts = json.loads(options)
    except json.JSONDecodeError:
        opts = {}
    output_target = None
    if output_path.strip():
        output_target = Path(output_path.strip()).expanduser().resolve()
        if output_target.suffix.lower() != ".mp4":
            raise HTTPException(400, "File xuất phải có đuôi .mp4")
    job = create_job(
        output_name, work, image_paths, audio_file, timeline_file, srt_file,
        opts, watermark_file, output_target,
    )
    start(job["id"])
    return job


@router.get("/api/srt-image/jobs/{job_id}")
def status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job không tồn tại")
    return job


@router.post("/api/srt-image/jobs/{job_id}/cancel")
def stop(job_id: str):
    if not cancel(job_id):
        raise HTTPException(404, "Job không tồn tại")
    return {"ok": True}


@router.post("/api/srt-image/jobs/{job_id}/pause")
def set_paused(job_id: str, paused: bool = True):
    if not pause(job_id, paused):
        raise HTTPException(409, "Không thể đổi trạng thái tiến trình")
    return {"ok": True, "paused": paused}


@router.post("/api/srt-image/open-folder")
def open_folder(job_id: str = ""):
    job = get_job(job_id) if job_id else None
    output = Path(job["output"]) if job else None
    folder = output.parent if output else ROOT
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # /select mở cửa sổ Explorer mới và chọn đúng video, thay vì tái dùng cửa sổ ẩn phía sau APP.
        argument = f'/select,"{output}"' if output and output.exists() else f'/n,"{folder}"'
        subprocess.Popen(["explorer.exe", argument])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
    return {"ok": True}


@router.post("/api/srt-image/jobs/{job_id}/open")
def open_video(job_id: str):
    path = output_path(job_id)
    if not path:
        raise HTTPException(404, "Video chưa hoàn thành")
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    except OSError as exc:
        raise HTTPException(500, f"Không mở được video: {exc}") from exc
    return {"ok": True, "path": str(path)}


@router.get("/api/srt-image/jobs/{job_id}/file")
def file(job_id: str):
    path = output_path(job_id)
    if not path:
        raise HTTPException(404, "Video chưa hoàn thành")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
