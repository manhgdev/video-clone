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


def adaptive_workers(
    requested: int | None,
    *,
    kind: str = "cpu",
    cap: int = 16,
    tasks: int | None = None,
) -> int:
    """Số >0 là cố định; 0/None tự tăng giảm theo CPU, RAM và GPU rảnh."""
    limit = max(1, int(cap))
    if requested is not None and int(requested) > 0:
        value = min(limit, int(requested))
    else:
        cores = max(1, os.cpu_count() or 4)
        cpu_idle, memory_free = _cpu_idle_and_memory()
        # Dùng 55–95% logical cores: máy rảnh thì tăng ngay, máy đang bận vẫn
        # để đủ luồng cho pipeline tiến nhanh mà không làm treo UI/OS.
        cpu_target = max(1, round(cores * (0.55 + 0.40 * cpu_idle)))
        if memory_free < 0.12:
            cpu_target = max(1, cpu_target // 3)
        elif memory_free < 0.22:
            cpu_target = max(1, cpu_target // 2)

        if kind == "network":
            value = max(2, min(limit, cpu_target * 2))
        elif kind == "gpu":
            gpu = _gpu_headroom()
            if gpu is None:
                value = min(limit, cpu_target)
            else:
                gpu_idle, vram_free = gpu
                gpu_limit = 4 if gpu_idle >= 0.55 and vram_free >= 0.35 else 2
                if vram_free < 0.18 or gpu_idle < 0.20:
                    gpu_limit = 1
                value = min(limit, cpu_target, gpu_limit)
        else:
            value = min(limit, cpu_target)
    if tasks is not None:
        value = min(value, max(1, int(tasks)))
    return max(1, value)
