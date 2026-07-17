"""Job cancel flags + killable subprocess runner."""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Any

# cancel thật: Event + kill subprocess đang chạy
_cancel_flags: dict[str, threading.Event] = {}
_job_procs: dict[str, list[subprocess.Popen]] = {}
_job_gen: dict[str, int] = {}
_lock = threading.Lock()


class Cancelled(Exception):
    """Job bị user huỷ."""


def begin_job(project_id: str) -> int:
    """Bắt đầu job mới. Giữ cancel nếu user đã bấm Huỷ lúc còn Queued."""
    with _lock:
        gen = int(_job_gen.get(project_id, 0)) + 1
        _job_gen[project_id] = gen
        prev = _cancel_flags.get(project_id)
        already = prev is not None and prev.is_set()
        ev = threading.Event()
        if already:
            ev.set()
        _cancel_flags[project_id] = ev
        old = list(_job_procs.get(project_id, []))
        _job_procs[project_id] = []
    for p in old:
        try:
            p.kill()
        except OSError:
            pass
    return gen


def arm_job(project_id: str) -> int:
    """Gắn flag cancel sớm (Queued) — Huỷ trước begin_job vẫn ăn."""
    with _lock:
        if project_id not in _cancel_flags or _cancel_flags[project_id].is_set():
            # job mới hoặc vừa huỷ xong → event sạch
            if project_id not in _job_gen:
                _job_gen[project_id] = 0
            if project_id not in _cancel_flags or (
                _cancel_flags.get(project_id) and _cancel_flags[project_id].is_set()
                and project_id not in _job_procs
            ):
                # nếu đang cancelled và không có proc → chuẩn bị job mới
                if project_id not in _cancel_flags or _cancel_flags[project_id].is_set():
                    # only reset if not mid-flight without gen bump
                    pass
        if project_id not in _cancel_flags:
            _cancel_flags[project_id] = threading.Event()
            _job_gen.setdefault(project_id, 0)
        elif _cancel_flags[project_id].is_set():
            # job cũ đã huỷ — arm job mới
            _cancel_flags[project_id] = threading.Event()
        return int(_job_gen.get(project_id, 0))


def request_cancel(project_id: str) -> bool:
    """Đánh dấu huỷ. Luôn tạo flag nếu chưa có (Queued / trước begin_job)."""
    with _lock:
        ev = _cancel_flags.get(project_id)
        if not ev:
            ev = threading.Event()
            _cancel_flags[project_id] = ev
            _job_gen.setdefault(project_id, 0)
        ev.set()
        procs = list(_job_procs.get(project_id, []))
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass
    return True


def clear_job(project_id: str, gen: int | None = None) -> None:
    """Xóa flag. gen → chỉ clear đúng generation."""
    with _lock:
        if gen is not None and _job_gen.get(project_id) != gen:
            return
        _cancel_flags.pop(project_id, None)
        _job_procs.pop(project_id, None)


def check_cancel(project_id: str | None, gen: int | None = None) -> None:
    if not project_id:
        return
    with _lock:
        if gen is not None and _job_gen.get(project_id) != gen:
            return
        ev = _cancel_flags.get(project_id)
        if ev and ev.is_set():
            raise Cancelled()


def job_generation(project_id: str) -> int | None:
    with _lock:
        return _job_gen.get(project_id)


def is_cancelled(project_id: str | None) -> bool:
    if not project_id:
        return False
    with _lock:
        ev = _cancel_flags.get(project_id)
        return bool(ev and ev.is_set())


def run_cmd(project_id: str | None, cmd: list[str], **kwargs: Any) -> None:
    """subprocess có thể kill khi huỷ."""
    check_cancel(project_id)
    kw = dict(kwargs)
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    try:
        from .winproc import hide_console_kwargs

        for k, v in hide_console_kwargs().items():
            kw.setdefault(k, v)
    except Exception:
        pass
    p = subprocess.Popen(cmd, **kw)
    if project_id:
        with _lock:
            _job_procs.setdefault(project_id, []).append(p)
    try:
        while p.poll() is None:
            check_cancel(project_id)
            time.sleep(0.12)
        if p.returncode not in (0, None):
            check_cancel(project_id)
            raise subprocess.CalledProcessError(p.returncode or 1, cmd)
    finally:
        if project_id:
            with _lock:
                if project_id in _job_procs:
                    _job_procs[project_id] = [
                        x for x in _job_procs[project_id] if x is not p
                    ]
