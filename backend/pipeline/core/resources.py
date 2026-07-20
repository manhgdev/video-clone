"""Tự chọn mức song song theo tài nguyên còn rảnh của máy."""
from __future__ import annotations

import os
import subprocess
import sys
import time


def _windows_idle_and_memory() -> tuple[float, float] | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        def sample() -> tuple[int, int, int]:
            idle = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                raise OSError("GetSystemTimes")

            def ticks(value: wintypes.FILETIME) -> int:
                return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

            return ticks(idle), ticks(kernel), ticks(user)

        first = sample()
        time.sleep(0.08)
        second = sample()
        idle_delta = max(0, second[0] - first[0])
        total_delta = max(1, second[1] - first[1] + second[2] - first[2])

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        memory = MemoryStatus()
        memory.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError("GlobalMemoryStatusEx")
        return min(1.0, idle_delta / total_delta), min(
            1.0, float(memory.ullAvailPhys) / max(1.0, float(memory.ullTotalPhys))
        )
    except Exception:
        return None


def _cpu_idle_and_memory() -> tuple[float, float]:
    try:
        import psutil  # type: ignore

        busy = float(psutil.cpu_percent(interval=0.08)) / 100.0
        memory_free = float(psutil.virtual_memory().available) / max(
            1.0, float(psutil.virtual_memory().total)
        )
        return max(0.0, min(1.0, 1.0 - busy)), max(0.0, min(1.0, memory_free))
    except Exception:
        windows = _windows_idle_and_memory()
        if windows is not None:
            return windows
        try:
            load = float(os.getloadavg()[0]) / max(1, os.cpu_count() or 1)
            return max(0.0, min(1.0, 1.0 - load)), 1.0
        except (AttributeError, OSError):
            return 0.75, 1.0


def _gpu_headroom() -> tuple[float, float] | None:
    """(GPU idle, VRAM free ratio); None nếu không có NVIDIA."""
    try:
        raw = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=1.5,
            stderr=subprocess.DEVNULL,
        ).splitlines()[0]
        util, free, total = (float(x.strip()) for x in raw.split(",")[:3])
        return max(0.0, min(1.0, 1.0 - util / 100.0)), max(
            0.0, min(1.0, free / max(1.0, total))
        )
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def gpu_job_cap(
    *,
    per_job_mb: int = 1200,
    reserve_mb: int = 700,
    hard_max: int = 12,
) -> int:
    """Số job GPU song song (OCR/engine) — auto gần full VRAM; full thì hạ nhẹ.

    Không còn trần cứng 1–4: máy 6GB thường ~3–4; 12GB+ có thể cao hơn.
    """
    try:
        raw = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=1.5,
            stderr=subprocess.DEVNULL,
        ).splitlines()[0]
        total, free, util = (float(x.strip()) for x in raw.split(",")[:3])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 4
    # Card rảnh: tính theo gần hết VRAM; đang full: theo free còn lại.
    if util < 75.0:
        usable = total * 0.90 - reserve_mb
    else:
        usable = free - reserve_mb * 0.5
    n = int(max(0.0, usable) // max(400, int(per_job_mb)))
    # Đang full util → chỉ giảm nhẹ (không về 1)
    if util >= 94.0:
        n = max(1, round(n * 0.85))
    elif util >= 85.0:
        n = max(1, round(n * 0.92))
    cores = max(1, os.cpu_count() or 4)
    return max(1, min(int(hard_max), n, cores))


def adaptive_workers(
    requested: int | None,
    *,
    kind: str = "cpu",
    cap: int = 16,
    tasks: int | None = None,
) -> int:
    """Số >0 là cố định; 0/None = auto: dùng gần hết máy, chỉ hạ nhẹ khi đang full."""
    limit = max(1, int(cap))
    if requested is not None and int(requested) > 0:
        value = min(limit, int(requested))
    else:
        cores = max(1, os.cpu_count() or 4)
        cpu_idle, memory_free = _cpu_idle_and_memory()
        # Auto: 88–100% logical cores — máy rảnh thì full; CPU đang full chỉ hạ nhẹ.
        cpu_target = max(1, round(cores * (0.88 + 0.12 * cpu_idle)))
        if memory_free < 0.10:
            cpu_target = max(1, round(cpu_target * 0.55))
        elif memory_free < 0.18:
            cpu_target = max(1, round(cpu_target * 0.75))

        if kind == "network":
            value = max(2, min(limit, cpu_target * 2))
        elif kind == "gpu":
            gpu = _gpu_headroom()
            if gpu is None:
                value = min(limit, cpu_target)
            else:
                gpu_idle, vram_free = gpu
                # Dùng hết GPU khi còn headroom; full thì chỉ giảm ~10–15%.
                # (Bỏ trần cứng 4 luồng cũ — đó là lý do util GPU thấp.)
                if vram_free < 0.08:
                    scale = 0.55  # VRAM sát đáy — tránh OOM
                elif gpu_idle < 0.06:  # util ≳ 94%
                    scale = 0.85
                elif gpu_idle < 0.15:  # util ≳ 85%
                    scale = 0.92
                else:
                    scale = 1.0
                # Khi GPU còn rảnh: đẩy gần full cores (không bị cpu_target kéo xuống thấp)
                base = cpu_target if scale < 1.0 else max(cpu_target, round(cores * 0.95))
                value = max(1, min(limit, round(base * scale)))
        else:
            value = min(limit, cpu_target)
    if tasks is not None:
        value = min(value, max(1, int(tasks)))
    return max(1, value)
