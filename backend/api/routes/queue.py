"""Unified job queue API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pipeline.core.config import DATA, safe_child
from pipeline.queue.engine import enqueue, get_engine, job_action, list_jobs
from pipeline.queue.store import get as get_job, mutate

router = APIRouter()


class EnqueueIn(BaseModel):
    type: str = "clone"
    sources: list[str] = []
    settings: dict[str, Any] = {}
    recursive: bool = True


class QueueActionIn(BaseModel):
    op: str


class JobSettingsIn(BaseModel):
    settings: dict[str, Any]


def resolve_job_file(job: dict[str, Any], part: int | None = None) -> Path:
    if part is not None:
        for item in job.get("parts") or []:
            if int(item.get("index") or 0) == int(part):
                path = Path(str(item.get("output") or ""))
                if path.is_file():
                    return path
                raise HTTPException(404, "Phần này chưa có file")
        raise HTTPException(404, "Không thấy phần")
    out = Path(str(job.get("output") or ""))
    if out.is_file():
        return out
    for item in reversed(list(job.get("parts") or [])):
        path = Path(str(item.get("output") or ""))
        if path.is_file():
            return path
    raise HTTPException(404, "Chưa có file video")


def _delete_review_part_cache(job: dict[str, Any], index: int) -> None:
    """Remove large intermediate files for one deleted Review part."""
    refs = job.get("cacheRefs") or {}
    root_raw = str(refs.get("root") or "")
    run_id = str(refs.get("run") or job.get("runId") or "")
    if not root_raw or not run_id:
        return
    try:
        root = Path(root_raw).resolve()
        review_cache = (DATA / "review_cache").resolve()
        root.relative_to(review_cache)
    except (OSError, ValueError):
        return
    if root == review_cache:
        return
    runs = root / "runs"
    run = safe_child(runs, run_id)
    if not run or not run.is_dir():
        return
    for stem, suffix in (
        ("raw_part", ".mp4"),
        ("burned_part", ".mp4"),
        ("part", ".mp4"),
        ("script", ".json"),
        ("plan", ".json"),
        ("voice", ".json"),
        ("final", ".json"),
    ):
        candidate = run / f"{stem}_{index:02d}{suffix}"
        if candidate.is_file():
            candidate.unlink()


@router.get("/api/queue")
def api_queue():
    get_engine()
    return list_jobs()


@router.post("/api/queue")
def api_queue_enqueue(body: EnqueueIn):
    if body.type not in {"clone", "review"}:
        raise HTTPException(422, "type phải là clone hoặc review")
    if not body.sources:
        raise HTTPException(422, "Thiếu nguồn video")
    jobs = enqueue(body.type, body.sources, body.settings, recursive=body.recursive)
    return {"ok": True, "jobs": jobs}


@router.post("/api/queue/action")
def api_queue_global(body: QueueActionIn):
    if body.op not in {"pause_all", "resume_all", "retry_failed", "clear_completed", "clear_logs"}:
        raise HTTPException(422, "Lệnh không hợp lệ")
    return job_action("*", body.op)


@router.get("/api/queue/{job_id}/file")
def api_queue_file(job_id: str, part: int | None = None, download: int = 0):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Không thấy job")
    path = resolve_job_file(job, part)
    name = path.name if part is None else f"part_{part:02d}.mp4"
    if download:
        return FileResponse(path, filename=name, media_type="video/mp4")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@router.delete("/api/queue/{job_id}/parts/{index}")
def api_queue_delete_part(job_id: str, index: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Không thấy job")
    parts = list(job.get("parts") or [])
    found = False
    for item in parts:
        if int(item.get("index") or 0) != int(index):
            continue
        found = True
        path = Path(str(item.get("output") or ""))
        if path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                raise HTTPException(500, str(exc)) from exc
        try:
            _delete_review_part_cache(job, int(index))
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
        item["status"] = "cancelled"
        item["output"] = ""
    if not found:
        raise HTTPException(404, "Không thấy phần")
    mutate(job_id, {"parts": parts})
    return list_jobs()


@router.post("/api/queue/{job_id}/action")
def api_queue_job(job_id: str, body: QueueActionIn):
    try:
        return job_action(job_id, body.op)
    except KeyError:
        raise HTTPException(404, "Không thấy job") from None


@router.patch("/api/queue/{job_id}/settings")
def api_queue_settings(job_id: str, body: JobSettingsIn):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Không thấy job")
    if job.get("status") in {"running", "queued"}:
        raise HTTPException(422, "Không thể đổi cài đặt khi job đang chạy")
    settings = dict(body.settings)
    source = str(settings.get("source") or job.get("source") or "").strip()
    if not source:
        raise HTTPException(422, "Thiếu nguồn video")
    mutate(job_id, {
        "source": source,
        "settings_snapshot": settings,
        "outputDir": str(settings.get("outputDir") or job.get("outputDir") or ""),
    })
    return list_jobs()
