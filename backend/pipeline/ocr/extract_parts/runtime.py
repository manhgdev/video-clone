"""Paddle/RapidOCR hardsub extract — runtime."""
from __future__ import annotations

"""RapidOCR extract — hardsub đáy + mid/vertical/labels.

Tách khỏi asr.py (Whisper) và đường dịch/phụ đề burn layout.
Không sửa logic — chỉ di chuyển.
"""

from functools import lru_cache
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
# add_dll_directory() trả handle — phải giữ sống hoặc GC sẽ xóa dir khỏi search path!
_dll_handles: list = []


def _cpu_budget(ratio: float = 0.9) -> int:
    """Số luồng CPU dùng cho OCR (auto gần full cores)."""
    n = os.cpu_count() or 4
    return max(1, min(n, int(n * ratio)))


def _ocr_pool_workers(
    requested: int | None, *, cap: int | None = None, gpu: bool = False
) -> int:
    # GPU: pack VRAM (đừng kẹp bằng CPU budget — để card đầy khi rảnh)
    from pipeline.core.resources import pack_gpu_workers

    if gpu:
        hard = (
            cap
            if cap is not None
            else pack_gpu_workers(per_job_mb=450, reserve_mb=350, hard_max=20)
        )
        return adaptive_workers(requested, kind="gpu", cap=max(1, int(hard)))
    budget = _cpu_budget(0.92)
    hard = cap if cap is not None else budget
    return adaptive_workers(requested, kind="cpu", cap=min(hard, budget))


def _ocr_semaphore() -> threading.Semaphore:
    """Semaphore toàn cục OCR — GPU pack VRAM; CPU ≤ budget cores."""
    global _ocr_sem, _ocr_sem_n
    try:
        from pipeline.core.resources import pack_gpu_workers

        # _rapidocr_gpu_kwargs định nghĩa bên dưới cùng file
        if _rapidocr_gpu_kwargs().get("det_use_cuda"):
            n = pack_gpu_workers(per_job_mb=450, reserve_mb=350, hard_max=20)
        else:
            n = _cpu_budget(0.92)
    except Exception:
        n = _cpu_budget(0.92)
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
    """Mọi thư mục nvidia/*/bin (pip CUDA wheels) — purelib + .venv-runtime + sys.path."""
    roots: list[Path] = []
    try:
        import sysconfig

        roots.append(Path(sysconfig.get_paths()["purelib"]))
    except Exception:
        pass
    try:
        exe_parent = Path(sys.executable).parent
        site1 = exe_parent.parent / "Lib" / "site-packages"
        site2 = exe_parent / "Lib" / "site-packages"
        for s in (site1, site2):
            if s.is_dir():
                roots.append(s)
    except Exception:
        pass
    home = os.environ.get("VIDEO_CLONE_HOME", "").strip()
    if home:
        for venv_name in (".venv-runtime", ".venv-ocr"):
            ocr_site = (
                Path(home) / venv_name / "Lib" / "site-packages"
                if os.name == "nt"
                else Path(home)
                / venv_name
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            )
            roots.append(ocr_site)
    # Thêm tất cả site-packages từ sys.path — frozen app có .venv-runtime trong sys.path[0]
    for p in sys.path:
        if p:
            roots.append(Path(p))
    # Cố gắng lấy path từ onnxruntime đã import (nếu sẵn có).
    # KHOONG import onnxruntime ở đây — sẽ poison sys.modules nếu DLL chưa sẵn.
    existing_ort = sys.modules.get("onnxruntime")
    if existing_ort is not None and getattr(existing_ort, "__file__", None):
        try:
            roots.append(Path(existing_ort.__file__).resolve().parent.parent)
        except Exception:
            pass

    seen: set[str] = set()
    bins: list[Path] = []
    for root in roots:
        nvidia = root / "nvidia"
        if nvidia.is_dir():
            for b in nvidia.glob("*/bin"):
                key = str(b.resolve()) if b.exists() else str(b)
                if key not in seen and b.is_dir():
                    seen.add(key)
                    bins.append(b)
        tlib = root / "torch" / "lib"
        if tlib.is_dir():
            key = str(tlib.resolve()) if tlib.exists() else str(tlib)
            if key not in seen:
                seen.add(key)
                bins.append(tlib)
    return bins


_cuda_lock = threading.Lock()


def prepare_cuda_dlls() -> None:
    """PATH + add_dll_directory cho CUDA pip wheels (Whisper + RapidOCR + onnxruntime-gpu)."""
    global _cuda_dlls_ready
    if os.name != "nt" or _cuda_dlls_ready:
        return
    with _cuda_lock:
        if _cuda_dlls_ready:
            return
        bins = _nvidia_bin_dirs()
        # Thêm onnxruntime/capi/ từ mọi site-packages — onnxruntime-gpu đặt DLL ở đây,
        # không phải nvidia/*/bin. Thiếu thư mục này → DLL load failed importing _core.
        seen_bins = {str(b) for b in bins}
        for p in sys.path:
            if not p:
                continue
            sp = Path(p)
            capi = sp / "onnxruntime" / "capi"
            if capi.is_dir() and str(capi) not in seen_bins:
                seen_bins.add(str(capi))
                bins.append(capi)
            # cv2.pyd phụ thuộc vào opencv_world4xx.dll nằm trong cv2/ package dir.
            # Windows 10+ không tự tìm DLL trong thư mục .pyd mà không có add_dll_directory.
            cv2_dir = sp / "cv2"
            if cv2_dir.is_dir() and str(cv2_dir) not in seen_bins:
                seen_bins.add(str(cv2_dir))
                bins.append(cv2_dir)
            # delvewheel pattern: DLLs đặt trong <package>.libs/ (e.g. av.libs/, numpy.libs/)
            # av._core.pyd cần avcodec/avformat DLLs trong av.libs/
            if sp.is_dir():
                for libs_dir in sp.glob("*.libs"):
                    if libs_dir.is_dir() and str(libs_dir) not in seen_bins:
                        seen_bins.add(str(libs_dir))
                        bins.append(libs_dir)
        # Frozen APP (PyInstaller): thêm _MEIPASS/onnxruntime/capi/ và _MEIPASS/torch/lib/
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for sub in ("onnxruntime/capi", "torch/lib"):
                d = Path(meipass) / sub
                if d.is_dir() and str(d) not in seen_bins:
                    seen_bins.add(str(d))
                    bins.insert(0, d)  # ưu tiên _MEIPASS trước
        if not bins:
            return
        path = os.environ.get("PATH", "")
        path_parts = path.split(os.pathsep) if path else []
        prepend = [str(b) for b in bins if str(b) not in path_parts]
        if prepend:
            os.environ["PATH"] = os.pathsep.join(prepend + path_parts)
        # Windows: LoadLibrary tìm DLL qua add_dll_directory (PATH đôi khi không đủ).
        # QUAN TRỌNG: lưu handle vào _dll_handles — nếu handle bị GC thì dir bị xóa khỏi search path!
        add_dir = getattr(os, "add_dll_directory", None)
        if add_dir:
            for b in bins:
                try:
                    handle = add_dir(str(b))
                    if handle is not None:
                        _dll_handles.append(handle)
                except (OSError, FileNotFoundError):
                    pass
        _cuda_dlls_ready = True


def _reset_cuda_dlls() -> None:
    """Force re-scan DLL dirs (gọi sau khi sys.path thay đổi, e.g. .venv-runtime added)."""
    global _cuda_dlls_ready
    with _cuda_lock:
        _cuda_dlls_ready = False


def _patch_rapidocr_onnxruntime_ep() -> None:
    """Fix ONNXRuntime CUDA failure 900 (stream capture) during multithreading with EXHAUSTIVE cudnn search."""
    try:
        from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession
        if hasattr(OrtInferSession, "_patched_ep_list"):
            return
        orig_get_ep_list = OrtInferSession._get_ep_list
        def patched_get_ep_list(self) -> list:
            ep_list = orig_get_ep_list(self)
            for idx, (ep_name, ep_opts) in enumerate(ep_list):
                if ep_name == "CUDAExecutionProvider" and isinstance(ep_opts, dict):
                    ep_opts["cudnn_conv_algo_search"] = "HEURISTIC"
            return ep_list
        OrtInferSession._get_ep_list = patched_get_ep_list
        OrtInferSession._patched_ep_list = True
    except Exception:
        pass


def _rapidocr_labels(*, use_cuda: bool | None = None) -> Any:
    """OCR lỏng hơn cho nhãn 1 chữ / graphic nhỏ (default min_height=30 bỏ sót 行)."""
    # QUAN TRỌNG: thứ tự call bắt buộc:
    # 1. prepare_cuda_dlls() — đăng ký CUDA DLL paths trước mọi onnxruntime import
    # 2. ensure_cv2()       — preload cv2 trước rapidocr (rapidocr import cv2 ngay khi load)
    # 3. import rapidocr   — khi đó cv2 đã trong sys.modules, không trigger bootstrap lại
    prepare_cuda_dlls()
    try:
        from pipeline.core.runtime_site import ensure_cv2
        ensure_cv2()
    except Exception:
        pass
    from rapidocr_onnxruntime import RapidOCR  # type: ignore
    _patch_rapidocr_onnxruntime_ep()

    _limit_onnx_threads()
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


@lru_cache(maxsize=1)
def _rapidocr_gpu_kwargs() -> dict[str, bool]:
    """Ưu tiên CUDA khi onnxruntime-gpu có CUDAExecutionProvider."""
    try:
        # prepare_cuda_dlls đã đăng ký cả onnxruntime/capi/ — gọi trước khi import ort
        prepare_cuda_dlls()
        import onnxruntime as ort
        _patch_rapidocr_onnxruntime_ep()

        providers = list(ort.get_available_providers())
        use_cuda = "CUDAExecutionProvider" in providers
        try:
            from pipeline.core.app_log import append_log

            append_log(f"[ocr-gpu] providers={providers} -> use_cuda={use_cuda}")
        except Exception:
            pass
    except (ImportError, OSError) as e:
        use_cuda = False
        try:
            from pipeline.core.app_log import append_log

            append_log(f"[ocr-gpu] check err={e} -> use_cuda=False")
        except Exception:
            pass
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
