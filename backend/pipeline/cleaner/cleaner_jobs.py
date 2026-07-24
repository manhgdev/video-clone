"""In-memory video cleaner jobs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from pipeline.core.config import DATA, PUBLIC_DATA
from pipeline.core.jobs import kill_process_tree

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_PROCS: dict[str, subprocess.Popen] = {}

CLEANER_TEMP_DIR = PUBLIC_DATA / "upload"
CLEANER_OUT_DIR = DATA / "cleaner_out"

def ensure_dirs():
    CLEANER_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    CLEANER_OUT_DIR.mkdir(parents=True, exist_ok=True)

ensure_dirs()

def list_jobs() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_JOBS.values())

def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return _JOBS.get(job_id)

def create_job(filename: str, method: str, options: dict, input_path: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:8]
    
    ext = Path(filename).suffix or ".mp4"
    if method == "reencode" and options.get("container"):
        ext = f".{options['container']}"
        
    output_path = CLEANER_OUT_DIR / f"{Path(filename).stem}_{job_id}{ext}"
    
    job = {
        "id": job_id,
        "filename": filename,
        "method": method,
        "status": "queued",
        "progress": 0,
        "outputSize": None,
        "startedAt": None,
        "finishedAt": None,
        "error": None,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "options": options,
    }
    with _LOCK:
        _JOBS[job_id] = job
    return job

def update_job(job_id: str, updates: dict[str, Any]) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(updates)

def register_proc(job_id: str, proc: subprocess.Popen) -> None:
    with _LOCK:
        _PROCS[job_id] = proc

def unregister_proc(job_id: str) -> None:
    with _LOCK:
        _PROCS.pop(job_id, None)

def cancel_job(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return False
        if job["status"] in ("done", "error", "cancelled"):
            return True
        job["status"] = "cancelled"
        
    proc = _PROCS.get(job_id)
    if proc:
        kill_process_tree(proc.pid)
    
    _cleanup_temp_files(job_id)
    return True

def delete_job(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.pop(job_id, None)
        if not job:
            return False
        
    proc = _PROCS.pop(job_id, None)
    if proc:
        kill_process_tree(proc.pid)
        
    if job.get("input_path") and os.path.isfile(job["input_path"]):
        try: os.remove(job["input_path"])
        except OSError: pass
    if job.get("output_path") and os.path.isfile(job["output_path"]):
        try: os.remove(job["output_path"])
        except OSError: pass
    return True

def _cleanup_temp_files(job_id: str) -> None:
    job = get_job(job_id)
    if job and job.get("input_path") and os.path.isfile(job["input_path"]):
        try: os.remove(job["input_path"])
        except OSError: pass

def get_job_output_path(job_id: str) -> Path | None:
    job = get_job(job_id)
    if not job or job.get("status") != "done":
        return None
    p = Path(job["output_path"])
    return p if p.is_file() else None

def reveal_job_file(job_id: str) -> bool:
    p = get_job_output_path(job_id)
    if not p:
        return False
    import platform
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif system == "Darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
        return True
    except OSError:
        return False
