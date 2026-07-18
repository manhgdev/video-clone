"""Paddle/RapidOCR hardsub extract — runtime."""
from __future__ import annotations

"""RapidOCR extract — hardsub đáy + mid/vertical/labels.

Tách khỏi asr.py (Whisper) và đường dịch/phụ đề burn layout.
Không sửa logic — chỉ di chuyển.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

from pipeline.core.jobs import check_cancel, run_cmd
from pipeline.core.project import cache_frames, set_status
from pipeline.core.resources import adaptive_workers

# giới hạn tổng luồng OCR phụ — tránh 100% CPU (để UI/OS ~5–10%)
_ocr_sem: threading.Semaphore | None = None
_ocr_sem_n: int = 0
_cuda_dlls_ready = False


def _cpu_budget(ratio: float = 0.9) -> int:
    """Số luồng CPU dùng cho OCR (mặc định 90% core — chừa UI)."""
    n = os.cpu_count() or 4
    return max(1, min(n, int(n * ratio)))


def _ocr_pool_workers(
    requested: int | None, *, cap: int | None = None, gpu: bool = False
) -> int:
    budget = _cpu_budget(0.9)
    hard = cap if cap is not None else min(4, budget)
    return adaptive_workers(
        requested, kind="gpu" if gpu else "cpu", cap=min(hard, budget)
    )


def _ocr_semaphore() -> threading.Semaphore:
    """Semaphore toàn cục: tổng job OCR phụ ≤ budget (không nhân 3 pass)."""
    global _ocr_sem, _ocr_sem_n
    n = _cpu_budget(0.9)
    if _ocr_sem is None or _ocr_sem_n != n:
        _ocr_sem = threading.Semaphore(n)
        _ocr_sem_n = n
    return _ocr_sem


def _limit_onnx_threads() -> None:
    """ONNX/OpenMP 1 thread / process — fan-out bằng pool, không nhân core."""
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    os.environ.setdefault("ORT_NUM_THREADS", "1")


def _nvidia_bin_dirs() -> list[Path]:
    """Mọi thư mục nvidia/*/bin (pip CUDA wheels) — purelib + .venv-ocr + sys.path."""
    roots: list[Path] = []
    try:
        import sysconfig

        roots.append(Path(sysconfig.get_paths()["purelib"]))
    except Exception:
        pass
    home = os.environ.get("VIDEO_CLONE_HOME", "").strip()
    if home:
        ocr_site = (
            Path(home) / ".venv-ocr" / "Lib" / "site-packages"
            if os.name == "nt"
            else Path(home)
            / ".venv-ocr"
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        roots.append(ocr_site)
    try:
        import onnxruntime as ort

        # .../site-packages/onnxruntime → sibling nvidia/
        roots.append(Path(ort.__file__).resolve().parent.parent)
    except Exception:
        pass
    for p in sys.path:
        if p:
            roots.append(Path(p))

    seen: set[str] = set()
    bins: list[Path] = []
    for root in roots:
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        for b in nvidia.glob("*/bin"):
            key = str(b.resolve()) if b.exists() else str(b)
            if key in seen:
                continue
            if b.is_dir():
                seen.add(key)
                bins.append(b)
    return bins


def prepare_cuda_dlls() -> None:
    """PATH + add_dll_directory cho CUDA pip wheels (Whisper + RapidOCR)."""
    global _cuda_dlls_ready
    if os.name != "nt" or _cuda_dlls_ready:
        return
    bins = _nvidia_bin_dirs()
    if not bins:
        return
    path = os.environ.get("PATH", "")
    path_parts = path.split(os.pathsep) if path else []
    prepend = [str(b) for b in bins if str(b) not in path_parts]
    if prepend:
        os.environ["PATH"] = os.pathsep.join(prepend + path_parts)
    # Windows: LoadLibrary tìm DLL qua add_dll_directory (PATH đôi khi không đủ).
    add_dir = getattr(os, "add_dll_directory", None)
    if add_dir:
        for b in bins:
            try:
                add_dir(str(b))
            except (OSError, FileNotFoundError):
                pass
    _cuda_dlls_ready = True


def _rapidocr_labels(*, use_cuda: bool | None = None) -> Any:
    """OCR lỏng hơn cho nhãn 1 chữ / graphic nhỏ (default min_height=30 bỏ sót 行)."""
    from rapidocr_onnxruntime import RapidOCR  # type: ignore

    _limit_onnx_threads()
    if use_cuda:
        prepare_cuda_dlls()
    gpu_kwargs = (
        _rapidocr_gpu_kwargs()
        if use_cuda is None
        else {
            "det_use_cuda": use_cuda,
            "cls_use_cuda": use_cuda,
            "rec_use_cuda": use_cuda,
        }
    )
    return RapidOCR(
        **gpu_kwargs,
        box_thresh=0.3,
        thresh=0.2,
        text_score=0.3,
        unclip_ratio=2.0,
        min_height=8,
    )


def _rapidocr_gpu_kwargs() -> dict[str, bool]:
    """Use CUDA for all OCR models when ONNX Runtime exposes its GPU provider."""
    try:
        prepare_cuda_dlls()
        import onnxruntime as ort

        use_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
    except (ImportError, OSError):
        use_cuda = False
    return {
        "det_use_cuda": use_cuda,
        "cls_use_cuda": use_cuda,
        "rec_use_cuda": use_cuda,
    }


__all__ = [
    '_ocr_sem',
    '_ocr_sem_n',
    '_cuda_dlls_ready',
    '_cpu_budget',
    '_ocr_pool_workers',
    '_ocr_semaphore',
    '_limit_onnx_threads',
    '_nvidia_bin_dirs',
    'prepare_cuda_dlls',
    '_rapidocr_labels',
    '_rapidocr_gpu_kwargs',
]
