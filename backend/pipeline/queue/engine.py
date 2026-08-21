"""Unified Job Queue + resource scheduler for clone and review."""
from __future__ import annotations

import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from pipeline.core.app_log import append_log
from pipeline.core.jobs import Cancelled, arm_job, begin_job, check_cancel, request_cancel
from pipeline.core.resources import gpu_job_cap
from pipeline.gpu.manager import assign_device, diagnostics, vram_free_mb
from pipeline.queue import store
from pipeline.queue.paths import output_name, scan_videos

_engine_lock = threading.Lock()
_engine: "QueueEngine | None" = None

DISK_PAUSE_BYTES = 2 * 1024 ** 3
ERROR_TYPES = {
    "SOURCE_ERROR", "FFMPEG_ERROR", "MODEL_ERROR", "GPU_OOM", "DRIVER_ERROR",
    "TTS_ERROR", "ASR_ERROR", "VISION_ERROR", "DISK_FULL", "RENDER_ERROR",
    "CANCELLED", "UNKNOWN",
}


def classify_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, Cancelled) or type(exc).__name__ == "Cancelled":
        return "CANCELLED"
    if "oom" in text or "out of memory" in text:
        return "GPU_OOM"
    if "disk" in text or "no space" in text:
        return "DISK_FULL"
    if "ffmpeg" in text:
        return "FFMPEG_ERROR"
    if "whisper" in text or "asr" in text:
        return "ASR_ERROR"
    if "tts" in text or "vieneu" in text:
        return "TTS_ERROR"
    if "vision" in text or "vlm" in text:
        return "VISION_ERROR"
    if "model" in text or "ollama" in text:
        return "MODEL_ERROR"
    if "driver" in text:
        return "DRIVER_ERROR"
    if "source" in text or "not found" in text:
        return "SOURCE_ERROR"
    if "render" in text or "export" in text:
        return "RENDER_ERROR"
    return "UNKNOWN"


def _with_part_status(job: dict[str, Any], current: set[str], status: str) -> dict[str, Any]:
    parts = []
    for raw in job.get("parts") or []:
        part = dict(raw)
        if str(part.get("status") or "pending") in current:
            part["status"] = status
        parts.append(part)
    return {"parts": parts} if parts else {}


def _source_key(job: dict[str, Any]) -> str:
    source = str(job.get("source") or "").strip()
    if not source or source.startswith(("http://", "https://")):
        return source
    return str(Path(source).expanduser().resolve())


class QueueEngine:
    def __init__(self) -> None:
        self._pause_all = False
        self._active: dict[str, threading.Thread] = {}
        self._guard = threading.Lock()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="unified-queue")
        store.mark_interrupted()
        self._thread.start()

    def kick(self) -> None:
        self._wake.set()

    def snapshot(self) -> dict[str, Any]:
        jobs = store.load_all()
        return {
            "jobs": jobs,
            "pauseAll": self._pause_all,
            "active": list(self._active),
            "diagnostics": diagnostics(),
        }

    def enqueue_many(
        self,
        *,
        job_type: str,
        sources: list[str],
        settings: dict[str, Any],
        recursive: bool = True,
    ) -> list[dict[str, Any]]:
        files = scan_videos(sources, recursive=recursive)
        created: list[dict[str, Any]] = []
        now = time.time()
        dest_dir = str(settings.get("outputDir") or "")
        template = str(settings.get("naming") or "{name}_{type}")
        for src in files:
            job = {
                "id": uuid.uuid4().hex[:12],
                "type": job_type,
                "mode": "batch",
                "source": src,
                "settings_snapshot": dict(settings),
                "status": "queued",
                "stage": "queued",
                "progress": 0.0,
                "checkpoints": [],
                "cacheRefs": {},
                "output": "",
                "outputName": output_name(Path(src), template, {**settings, "type": job_type}),
                "outputDir": dest_dir,
                "error": None,
                "errorType": None,
                "log": [],
                "projectId": None,
                "createdAt": now,
                "updatedAt": now,
            }
            store.insert(job)
            created.append(job)
        self.kick()
        return created

    def action(self, job_id: str, op: str) -> dict[str, Any]:
        job = store.get(job_id)
        if not job and op not in {"pause_all", "resume_all", "retry_failed", "clear_completed"}:
            raise KeyError(job_id)
        if op == "pause":
            patch = {"status": "paused", "error": None, "errorType": None}
            patch.update(_with_part_status(job or {}, {"running"}, "paused"))
            store.mutate(job_id, patch, log="Đã dừng")
            request_cancel(job_id)
        elif op == "resume":
            arm_job(job_id)
            patch = {"status": "queued", "error": None, "errorType": None}
            patch.update(_with_part_status(job or {}, {"paused", "interrupted", "running"}, "pending"))
            store.mutate(job_id, patch, log="Tiếp tục")
            self.kick()
        elif op == "cancel":
            patch = {"status": "cancelled", "errorType": "CANCELLED"}
            patch.update(_with_part_status(job or {}, {"pending", "running", "paused", "interrupted"}, "cancelled"))
            store.mutate(job_id, patch)
            request_cancel(job_id)
        elif op == "retry":
            arm_job(job_id)
            patch = {"status": "queued", "error": None, "errorType": None, "progress": 0}
            patch.update(_with_part_status(job or {}, {"cancelled", "failed", "paused", "interrupted"}, "pending"))
            store.mutate(job_id, patch, log="Thử lại")
            self.kick()
        elif op == "remove":
            request_cancel(job_id)
            store.replace_all([j for j in store.load_all() if j.get("id") != job_id])
        elif op == "pause_all":
            self._pause_all = True
            for item in store.load_all():
                if item.get("status") in {"running", "queued"}:
                    patch = {"status": "paused", "error": None, "errorType": None}
                    patch.update(_with_part_status(item, {"running"}, "paused"))
                    store.mutate(item["id"], patch, log="Đã dừng")
                    request_cancel(str(item["id"]))
        elif op == "resume_all":
            self._pause_all = False
            for item in store.load_all():
                if item.get("status") in {"paused", "interrupted"}:
                    arm_job(str(item["id"]))
                    patch = {"status": "queued", "error": None, "errorType": None}
                    patch.update(_with_part_status(item, {"paused", "interrupted", "running"}, "pending"))
                    store.mutate(item["id"], patch, log="Tiếp tục")
            self.kick()
        elif op == "retry_failed":
            for item in store.load_all():
                if item.get("status") in {"failed", "interrupted"}:
                    arm_job(str(item["id"]))
                    store.mutate(item["id"], {"status": "queued", "error": None, "errorType": None})
            self.kick()
        elif op == "clear_completed":
            store.replace_all([j for j in store.load_all() if j.get("status") != "done"])
        return self.snapshot()

    def _loop(self) -> None:
        while True:
            self._wake.wait(timeout=1.5)
            self._wake.clear()
            try:
                self._schedule()
            except Exception as exc:
                append_log(f"[queue] schedule error: {exc}")

    def _disk_ok(self, path: str) -> bool:
        try:
            target = Path(path) if path else Path.home()
            if not target.exists():
                target = target.parent if target.parent.exists() else Path.home()
            return shutil.disk_usage(str(target)).free >= DISK_PAUSE_BYTES
        except OSError:
            return True

    def _capacity(self) -> int:
        cap = gpu_job_cap(per_job_mb=1800, reserve_mb=800, hard_max=4)
        free = vram_free_mb(0)
        if free is not None and free < 1500:
            cap = min(cap, 1)
        return max(1, cap)

    def _schedule(self) -> None:
        if self._pause_all:
            return
        with self._guard:
            live = {jid: th for jid, th in self._active.items() if th.is_alive()}
            self._active = live
            running = len(live)
        jobs = store.load_all()
        # Interrupted jobs wait for an explicit Resume (do not auto-queue —
        # that blocked settings edits and hid the continue button).
        cap = self._capacity()
        live_sources = {
            _source_key(item)
            for item in jobs
            if str(item.get("id")) in live and _source_key(item)
        }
        for job in jobs:
            if running >= cap:
                return
            if job.get("status") not in {"queued"}:
                continue
            dest = str(job.get("outputDir") or "")
            if not self._disk_ok(dest):
                store.mutate(job["id"], {
                    "status": "paused",
                    "error": "DISK_FULL: dung lượng đĩa thấp, tạm dừng job mới",
                    "errorType": "DISK_FULL",
                })
                continue
            jid = str(job["id"])
            if jid in live:
                continue
            source_key = _source_key(job)
            if source_key and source_key in live_sources:
                continue
            th = threading.Thread(target=self._run_job, args=(jid,), daemon=True, name=f"q-{jid}")
            with self._guard:
                self._active[jid] = th
            th.start()
            running += 1
            if source_key:
                live_sources.add(source_key)

    def _run_job(self, job_id: str) -> None:
        begin_job(job_id)
        job = store.get(job_id) or {}
        store.mutate(job_id, {"status": "running", "stage": "start", "progress": 0.01, "error": None},
                     log=f"Bắt đầu job {job.get('type') or '?'} · {Path(str(job.get('source') or '')).name or job.get('source')}")
        job = store.get(job_id) or job
        try:
            check_cancel(job_id)
            device = assign_device("clone_ai" if job.get("type") == "clone" else "vision")
            store.mutate(job_id, {"device": device, "stage": "assigned"}, log=f"GPU/device: {device}")
            if job.get("type") == "review":
                from pipeline.review.run import run_review_job
                result = run_review_job(job)
            else:
                from pipeline.clone_run.headless import run_clone_job
                result = run_clone_job(job)
            store.mutate(job_id, {
                "status": "done",
                "stage": "done",
                "progress": 1.0,
                "output": result.get("output") or "",
                "projectId": result.get("projectId"),
                "cacheRefs": result.get("cacheRefs") or {},
            }, log=f"Xong · {result.get('output') or 'no output'}")
        except Cancelled:
            if (store.get(job_id) or {}).get("status") in {"paused", "queued"}:
                store.mutate(job_id, {"error": None, "errorType": None}, log="Đã dừng tại checkpoint")
            else:
                store.mutate(job_id, {"status": "cancelled", "errorType": "CANCELLED"}, log="Đã huỷ")
        except Exception as exc:
            kind = classify_error(exc)
            append_log(f"[queue:{job_id}] {kind}: {exc}\n{traceback.format_exc()[-1500:]}")
            if kind == "GPU_OOM":
                store.mutate(job_id, {"status": "queued", "error": str(exc)[:800], "errorType": kind, "oomRetry": True},
                             log=f"LỖI GPU_OOM — xếp lại hàng đợi: {exc}")
                time.sleep(2)
                self.kick()
                return
            store.mutate(job_id, {
                "status": "failed",
                "error": str(exc)[:1200],
                "errorType": kind,
            }, log=f"LỖI {kind}: {exc}\n{traceback.format_exc()[-2500:]}")
        finally:
            with self._guard:
                self._active.pop(job_id, None)
            self.kick()


def get_engine() -> QueueEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = QueueEngine()
        return _engine


def enqueue(job_type: str, sources: list[str], settings: dict[str, Any], recursive: bool = True) -> list[dict[str, Any]]:
    return get_engine().enqueue_many(job_type=job_type, sources=sources, settings=settings, recursive=recursive)


def list_jobs() -> dict[str, Any]:
    return get_engine().snapshot()


def job_action(job_id: str, op: str) -> dict[str, Any]:
    return get_engine().action(job_id, op)
