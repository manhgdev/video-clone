"""Headless Clone: same run_pipeline + run_export as interactive Clone."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pipeline.clone_run.open_source import open_local_video
from pipeline.core.jobs import check_cancel
from pipeline.core.project import load_meta, out_final, save_meta, set_status
from pipeline.orchestrate.asr_translate import run_pipeline
from pipeline.orchestrate.export_job import run_export
from pipeline.queue.paths import output_name
from pipeline.queue.store import mutate


def run_clone_job(job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job["id"])
    src = str(job.get("source") or "")
    settings = dict(job.get("settings_snapshot") or {})
    mutate(job_id, {"stage": "open", "progress": 0.05})
    project_id = open_local_video(src, kind="clone")
    from pipeline.core.jobs import share_cancel
    share_cancel(job_id, project_id)
    mutate(job_id, {"projectId": project_id, "stage": "clone", "progress": 0.1})
    meta = load_meta(project_id) or {}
    pipe_settings = {**(meta.get("settings") or {}), **settings, "previewSec": 0}
    meta["settings"] = pipe_settings
    save_meta(project_id, meta)
    check_cancel(job_id)
    set_status(project_id, step="asr", progress=1, message="Clone hàng loạt…", running=True)
    run_pipeline(project_id, pipe_settings)
    check_cancel(job_id)
    mutate(job_id, {"stage": "render", "progress": 0.75, "checkpoints": ["pipeline"]})
    out = run_export(project_id, nested=True)
    dest = _copy_output(out, src, settings, job)
    mutate(job_id, {"stage": "done", "progress": 1.0, "output": dest, "projectId": project_id})
    return {"output": dest, "projectId": project_id, "cacheRefs": {"projectId": project_id}}


def _copy_output(produced: Path | None, src: str, settings: dict[str, Any], job: dict[str, Any]) -> str:
    if produced is None:
        raise RuntimeError("Export không tạo file đầu ra")
    produced = Path(produced)
    if not produced.is_file():
        raise RuntimeError(f"Export không tạo file đầu ra: {produced}")
    dest_dir = Path(str(settings.get("outputDir") or produced.parent)).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = job.get("outputName") or output_name(Path(src), str(settings.get("naming") or "{name}_clone"), settings)
    dest = dest_dir / name
    policy = str(settings.get("overwrite") or "rename")
    if dest.exists() and policy == "skip":
        return str(dest)
    if dest.exists() and policy != "overwrite":
        stem, ext = dest.stem, dest.suffix
        n = 2
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{ext}"
            n += 1
    shutil.copy2(produced, dest)
    return str(dest)
