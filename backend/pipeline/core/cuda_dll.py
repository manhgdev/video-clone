"""Windows: load torch cuDNN before ctranslate2/faster-whisper.

ctranslate2 ships cudnn64_9.dll without cudnnGetLibConfig. If that copy
is bound first, Torch CUDA hard-crashes (Error 127 / 0xC0000409).
Fix: prepend torch's lib, overlay torch's DLLs onto ctranslate2, then
reload cudnn64_9.dll from torch's path. Do not fall back to CPU.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_path_bound = False

_CUDNN_NAMES = (
    "cudnn_ops64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_adv64_9.dll",
    "cudnn_heuristic64_9.dll",
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn64_9.dll",
)

# sherpa_onnx ORT CUDA looks next to onnxruntime_providers_cuda.dll (Error 126).
_CUBLAS_NAMES = (
    "cudart64_12.dll",
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "nvJitLink_120_0.dll",
)


def _torch_lib() -> Path | None:
    try:
        import torch

        lib = Path(torch.__file__).resolve().parent / "lib"
        if lib.is_dir() and (
            (lib / "cudnn64_9.dll").is_file() or (lib / "cublasLt64_12.dll").is_file()
        ):
            return lib
    except Exception:
        pass
    return None


def _package_dir(name: str) -> Path | None:
    try:
        import importlib.util

        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin:
            return None
        d = Path(spec.origin).resolve().parent
        return d if d.is_dir() else None
    except Exception:
        return None


def _ctranslate2_dir() -> Path | None:
    return _package_dir("ctranslate2")


def _sherpa_lib() -> Path | None:
    root = _package_dir("sherpa_onnx")
    if root is None:
        return None
    lib = root / "lib"
    return lib if lib.is_dir() else None


def _bind_torch_path(lib: Path) -> None:
    global _path_bound
    os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(lib))
        except OSError:
            pass
    _path_bound = True


def _copy_dlls(lib: Path, dest: Path | None, names: tuple[str, ...]) -> None:
    if dest is None:
        return
    for name in names:
        src = lib / name
        dst = dest / name
        if not src.is_file():
            continue
        try:
            if dst.is_file() and dst.stat().st_size == src.stat().st_size:
                continue
            shutil.copy2(src, dst)
        except OSError:
            pass


def _load_named(lib: Path, names: tuple[str, ...]) -> None:
    import ctypes

    for name in names:
        path = lib / name
        if path.is_file():
            ctypes.WinDLL(str(path))


def _unload_cudnn64() -> None:
    """Drop the in-process cudnn64_9.dll so torch's copy can bind."""
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    k32.FreeLibrary.argtypes = [ctypes.c_void_p]
    for _ in range(8):
        h = k32.GetModuleHandleW("cudnn64_9.dll")
        if not h:
            return
        if not k32.FreeLibrary(h):
            return


def _overlay_onto_ctranslate2(lib: Path) -> None:
    _copy_dlls(lib, _ctranslate2_dir(), _CUDNN_NAMES)


def _overlay_onto_sherpa(lib: Path) -> None:
    dest = _sherpa_lib()
    _copy_dlls(lib, dest, _CUBLAS_NAMES)
    _copy_dlls(lib, dest, _CUDNN_NAMES)


def prefer_torch_cudnn() -> None:
    if sys.platform != "win32":
        return
    lib = _torch_lib()
    if lib is None:
        return
    if not _path_bound:
        _bind_torch_path(lib)
    _overlay_onto_ctranslate2(lib)
    _overlay_onto_sherpa(lib)
    _load_named(lib, _CUBLAS_NAMES)
    if not cudnn_healthy():
        _unload_cudnn64()
    _load_named(lib, _CUDNN_NAMES)


def cudnn_healthy() -> bool:
    """True if the in-process cudnn64_9.dll exposes cudnnGetLibConfig."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        dll = ctypes.WinDLL("cudnn64_9.dll")
        _ = dll.cudnnGetLibConfig
        return True
    except (OSError, AttributeError):
        return False
