"""GPU preference for all local AI (VieNeu / Whisper / Demucs / OCR).

Priority: CUDA (NVIDIA) → MPS (Apple Silicon) → CPU.
Frozen desktop app probes torch inside .venv-runtime (PyInstaller cannot import torch).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
from typing import Literal

TorchDevice = Literal["cuda", "mps", "cpu"]
VieNeuBackend = Literal["pytorch", "onnx"]

_lock = threading.Lock()
_cache: dict[str, object] = {}


def _nvidia_smi() -> bool:
    try:
        cmd = "nvidia-smi"
        if not shutil.which("nvidia-smi") and sys.platform == "win32":
            for cand in (
                r"C:\Windows\System32\nvidia-smi.exe",
                r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
            ):
                if os.path.isfile(cand):
                    cmd = cand
                    break
        r = subprocess.run(
            [cmd, "-L"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0,
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        return False


def _runtime_python() -> str | None:
    """Python that owns torch in frozen app."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    home = (os.environ.get("VIDEO_CLONE_HOME") or "").strip()
    if not home:
        return None
    py = (
        os.path.join(home, ".venv-runtime", "Scripts", "python.exe")
        if sys.platform == "win32"
        else os.path.join(home, ".venv-runtime", "bin", "python")
    )
    return py if os.path.isfile(py) else None


def _probe_torch_device_in(python: str) -> TorchDevice:
    code = (
        "import torch\n"
        "d='cpu'\n"
        "try:\n"
        "  if torch.cuda.is_available(): d='cuda'\n"
        "  elif getattr(torch.backends,'mps',None) and torch.backends.mps.is_available(): d='mps'\n"
        "except Exception: pass\n"
        "print(d)\n"
    )
    try:
        r = subprocess.run(
            [python, "-c", code],
            capture_output=True,
            text=True,
            timeout=90,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0,
        )
        out = (r.stdout or "").strip().splitlines()
        d = (out[-1] if out else "cpu").strip().lower()
        if d in ("cuda", "mps", "cpu"):
            return d  # type: ignore[return-value]
    except Exception:
        pass
    return "cpu"


def preferred_torch_device(*, refresh: bool = False) -> TorchDevice:
    """Best *working* torch device for this machine right now."""
    with _lock:
        if not refresh and "torch_device" in _cache and _cache["torch_device"] in ("cuda", "mps"):
            return _cache["torch_device"]  # type: ignore[return-value]

    # Env override (debug / force)
    env = (os.environ.get("VIDEOCLONE_TORCH_DEVICE") or os.environ.get("TORCH_DEVICE") or "").strip().lower()
    if env in ("cuda", "mps", "cpu"):
        with _lock:
            _cache["torch_device"] = env
        return env  # type: ignore[return-value]

    device: TorchDevice = "cpu"
    if getattr(sys, "frozen", False):
        py = _runtime_python()
        if py:
            device = _probe_torch_device_in(py)
    else:
        try:
            try:
                from pipeline.ocr.extract_parts.runtime import prepare_cuda_dlls
                prepare_cuda_dlls()
            except Exception:
                pass

            import torch

            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        except Exception:
            pass

    with _lock:
        _cache["torch_device"] = device
    return device


def preferred_vieneu_backend() -> tuple[VieNeuBackend, str]:
    """(backend, device) for VieNeu — GPU when possible, else ONNX/CPU."""
    env = (os.environ.get("VIENEU_BACKEND") or "auto").strip().lower()
    if env in ("onnx", "cpu"):
        return "onnx", "cpu"
    if env == "pytorch":
        d = preferred_torch_device()
        return "pytorch", (d if d in ("cuda", "mps", "cpu") else "cpu")

    # auto: never pick ONNX if we have NVIDIA/Apple GPU torch
    d = preferred_torch_device()
    if d in ("cuda", "mps"):
        return "pytorch", d
    # No GPU torch — ONNX is lighter than pytorch/cpu for presets
    if env == "pytorch-cpu":
        return "pytorch", "cpu"
    return "onnx", "cpu"


def _tts_vram_hard_cap() -> int:
    """Trần TTS = pack theo VRAM thật (free/total), không bảng cứng ≤4.

    ~1.4GB / luồng VieNeu Turbo + reserve; card 16GB rảnh → tới ~8–10.
    """
    d = preferred_torch_device()
    if d == "cpu":
        return 2
    if d == "mps":
        try:
            import psutil  # type: ignore

            gb = float(psutil.virtual_memory().total) / (1024**3)
            if gb >= 36:
                return 8
            if gb >= 24:
                return 6
            if gb >= 16:
                return 4
            return 3
        except Exception:
            return 3
    # CUDA: hard_max theo total, actual pack theo free trong adaptive_workers
    try:
        import subprocess as sp

        raw = sp.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=1.5,
            stderr=sp.DEVNULL,
            creationflags=int(getattr(sp, "CREATE_NO_WINDOW", 0))
            if sys.platform == "win32"
            else 0,
        ).splitlines()[0]
        total_mb = float(raw.strip())
    except Exception:
        return 6
    # Trần lý thuyết khi card trống (pack_gpu_workers còn clamp theo free)
    # 24GB→12, 16GB→10, 12GB→8, 8GB→6, 6GB→5, 4GB→3
    if total_mb >= 22000:
        return 12
    if total_mb >= 14000:
        return 10
    if total_mb >= 10000:
        return 8
    if total_mb >= 7000:
        return 6
    if total_mb >= 5000:
        return 5
    if total_mb >= 3500:
        return 3
    return 2


def tts_local_workers(requested: int | None, *, tasks: int | None = None) -> int:
    """VieNeu/local TTS — auto pack VRAM (kind=tts). Rảnh → full pack; bận → hạ."""
    from pipeline.core.resources import adaptive_workers

    hard = _tts_vram_hard_cap()
    return adaptive_workers(requested, kind="tts", cap=hard, tasks=tasks)


def apply_gpu_process_env() -> None:
    """Call early in frozen/dev backend so child procs see GPU preference."""
    d = preferred_torch_device()
    if d == "cuda":
        os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
        # Don't force CUDA_VISIBLE_DEVICES — user may multi-GPU
        os.environ.setdefault("VIENEU_BACKEND", "auto")
    elif d == "mps":
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        os.environ.setdefault("VIENEU_BACKEND", "auto")
    # Reduce CPU oversubscription when GPU is primary
    if d in ("cuda", "mps"):
        os.environ.setdefault("OMP_NUM_THREADS", "2")
        os.environ.setdefault("MKL_NUM_THREADS", "2")


@lru_cache(maxsize=1)
def accel_label() -> str:
    d = preferred_torch_device()
    if d == "cuda":
        try:
            if getattr(sys, "frozen", False):
                return "CUDA"
            import torch

            return f"CUDA · {torch.cuda.get_device_name(0)}"
        except Exception:
            return "CUDA"
    if d == "mps":
        return "Apple GPU (MPS)"
    return "CPU"
