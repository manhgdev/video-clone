"""Completed video exports and their cached thumbnails."""
from __future__ import annotations

import os
import re
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pipeline.core.config import PUBLIC_DATA
from pipeline.core.media import ffprobe_duration, video_size

router = APIRouter()
_RENDER_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class RenderRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _render_path(render_id: str) -> Path | None:
    if not _RENDER_ID.fullmatch(render_id):
        return None
    path = PUBLIC_DATA / "exports" / f"{render_id}.mp4"
    return path if path.is_file() else None


def list_rendered_videos() -> list[dict[str, Any]]:
    exports = PUBLIC_DATA / "exports"
    if not exports.is_dir():
        return []
    paths = [path for path in exports.glob("*.mp4") if _RENDER_ID.fullmatch(path.stem)]
    archived_projects = {path.stem.split("-", 1)[0] for path in paths if "-" in path.stem}
    items: list[dict[str, Any]] = []
    for output in paths:
        render_id = output.stem
        try:
            sidecar = output.with_suffix(".json")
            saved = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
            project_id = saved.get("projectId") or render_id.split("-", 1)[0]
            if render_id == project_id and project_id in archived_projects:
                continue
            name = str(saved.get("name") or "").strip() or f"Render {project_id}"
            width, height = video_size(output)
            duration = ffprobe_duration(output)
            stat = output.stat()
            if width <= 0 or height <= 0 or duration <= 0:
                continue
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        items.append({
            "renderId": render_id,
            "projectId": project_id,
            "name": name,
            "createdAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sizeBytes": stat.st_size,
            "duration": duration,
            "width": width,
            "height": height,
            "videoUrl": f"/api/renders/{render_id}/video",
            "downloadUrl": f"/api/renders/{render_id}/video?download=1",
            "thumbnailUrl": f"/api/renders/{render_id}/thumbnail",
        })
    return sorted(items, key=lambda item: item["createdAt"], reverse=True)


def ensure_thumbnail(render_id: str) -> Path:
    output = _render_path(render_id)
    if output is None:
        raise FileNotFoundError(render_id)
    thumbnail = PUBLIC_DATA / "exports" / "thumbnails" / f"{render_id}.jpg"
    if thumbnail.is_file() and thumbnail.stat().st_mtime >= output.stat().st_mtime:
        return thumbnail
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    temp = thumbnail.with_name(f"{render_id}.{uuid.uuid4().hex}.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "1",
             "-i", str(output), "-frames:v", "1", "-vf", "scale=640:-2",
             "-q:v", "3", str(temp)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        temp.replace(thumbnail)
        return thumbnail
    finally:
        temp.unlink(missing_ok=True)


@router.get("/api/renders")
def api_renders():
    desktop = os.environ.get("VIDEO_CLONE_DESKTOP") == "1" or bool(getattr(sys, "frozen", False))
    return {"items": list_rendered_videos(), "canReveal": desktop}


@router.put("/api/renders/{render_id}")
def api_rename_render(render_id: str, body: RenderRenameIn):
    path = _render_path(render_id)
    if path is None:
        raise HTTPException(404, "Khong tim thay file render")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Ten render khong duoc de trong")
    sidecar = path.with_suffix(".json")
    sidecar.write_text(
        json.dumps({"name": name, "projectId": render_id.split("-", 1)[0]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"renderId": render_id, "name": name}


@router.delete("/api/renders/{render_id}")
def api_delete_render(render_id: str):
    path = _render_path(render_id)
    if path is None:
        raise HTTPException(404, "Khong tim thay file render")
    exports = PUBLIC_DATA / "exports"
    path.unlink()
    path.with_suffix(".json").unlink(missing_ok=True)
    (exports / "thumbnails" / f"{render_id}.jpg").unlink(missing_ok=True)
    # Bản "dễ tìm" <project>.mp4 chỉ bị ẩn khi còn bản lưu trữ <project>-*.mp4;
    # xóa bản lưu trữ cuối cùng thì dọn luôn để video không hiện lại trong tab.
    project_id = render_id.split("-", 1)[0]
    if "-" in render_id and not any(exports.glob(f"{project_id}-*.mp4")):
        (exports / f"{project_id}.mp4").unlink(missing_ok=True)
        (exports / f"{project_id}.json").unlink(missing_ok=True)
        (exports / "thumbnails" / f"{project_id}.jpg").unlink(missing_ok=True)
    return {"ok": True}


@router.get("/api/renders/{render_id}/video")
def api_render_video(render_id: str, download: bool = False):
    path = _render_path(render_id)
    if path is None:
        raise HTTPException(404)
    if download:
        return FileResponse(path, filename=f"video-clone-{render_id}.mp4", media_type="video/mp4")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@router.get("/api/renders/{render_id}/thumbnail")
def api_render_thumbnail(render_id: str):
    try:
        path = ensure_thumbnail(render_id)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        raise HTTPException(404, "Khong tao duoc thumbnail") from None
    return FileResponse(path, media_type="image/jpeg")


@router.post("/api/renders/{render_id}/reveal")
def api_reveal_render(render_id: str):
    import platform

    path = _render_path(render_id)
    if path is None:
        raise HTTPException(404, "Khong tim thay file render")
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True, "path": str(path.resolve())}
