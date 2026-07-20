"""Job cancel flags + killable subprocess runner."""
from __future__ import annotations

import subprocess
import sys
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


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Dừng cả process con; p.kill() một mình để sót ffmpeg/Demucs trên Windows."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)),
            check=False,
        )
    try:
        proc.kill()
    except OSError:
        pass


def register_process(project_id: str | None, proc: subprocess.Popen) -> None:
    if project_id:
        with _lock:
            _job_procs.setdefault(project_id, []).append(proc)


def unregister_process(project_id: str | None, proc: subprocess.Popen) -> None:
    if project_id:
        with _lock:
            current = _job_procs.get(project_id)
            if current is not None:
                _job_procs[project_id] = [item for item in current if item is not proc]


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
        kill_process_tree(p)
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
        kill_process_tree(p)
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


def short_cmd_error(exc: BaseException, *, limit: int = 280) -> str:
    """Rút gọn CalledProcessError — không dump cả argv ffmpeg vào UI."""
    if isinstance(exc, subprocess.CalledProcessError):
        code = exc.returncode
        cmd = exc.cmd
        head = ""
        if isinstance(cmd, (list, tuple)) and cmd:
            # Chỉ binary + vài flag đầu
            parts = [str(x) for x in cmd[:4]]
            head = " ".join(parts)
            if len(cmd) > 4:
                head += " …"
        elif cmd:
            head = str(cmd)[:120]
        msg = f"Lệnh thất bại (exit {code})"
        if head:
            msg += f": {head}"
        # WinError 206 / path dài
        err = getattr(exc, "strerror", None) or ""
        if "206" in str(code) or "too long" in str(exc).lower():
            msg = "Đường dẫn/lệnh quá dài (WinError 206) — đã rút filter; restart backend rồi xuất lại."
        return msg[:limit]
    text = str(exc).strip() or type(exc).__name__
    # Cắt khối Command '[ffmpeg'… khổng lồ
    if "Command '" in text or "Command \"" in text:
        if "206" in text or "too long" in text.lower():
            return "Đường dẫn/lệnh quá dài (WinError 206) — restart backend rồi xuất lại."
        if "ffmpeg" in text.lower():
            # Lấy exit status nếu có
            import re

            m = re.search(r"exit status (-?\d+)", text, re.I)
            code = m.group(1) if m else "?"
            return f"ffmpeg thất bại (exit {code}). Xem log backend."
        return text[:limit]
    return text[:limit]


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
    register_process(project_id, p)
    try:
        while p.poll() is None:
            check_cancel(project_id)
            time.sleep(0.12)
        if p.returncode not in (0, None):
            check_cancel(project_id)
            raise subprocess.CalledProcessError(p.returncode or 1, cmd)
    finally:
        unregister_process(project_id, p)
