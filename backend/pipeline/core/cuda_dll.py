"""Windows: load torch cuDNN before ctranslate2/faster-whisper.

ctranslate2 ships its own cudnn64_9.dll without cudnnGetLibConfig.
If Whisper warms first, Windows keeps that DLL and Torch CUDA hard-crashes
the uvicorn worker (Error 127 → ECONNRESET on /api/tts/studio/synthesize).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False


def prefer_torch_cudnn() -> None:
    global _done
    if _done or sys.platform != "win32":
        return
    _done = True
    try:
        import torch

        lib = Path(torch.__file__).resolve().parent / "lib"
        if not lib.is_dir():
            return
        # First hit for LoadLibrary("cudnn64_9.dll") must be torch's copy.
        os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(lib))
        import ctypes

        for name in (
            "cudnn_ops64_9.dll",
            "cudnn_graph64_9.dll",
            "cudnn_cnn64_9.dll",
            "cudnn_adv64_9.dll",
            "cudnn_heuristic64_9.dll",
            "cudnn_engines_precompiled64_9.dll",
            "cudnn_engines_runtime_compiled64_9.dll",
            "cudnn64_9.dll",
        ):
            path = lib / name
            if path.is_file():
                ctypes.WinDLL(str(path))
    except Exception:
        # ponytail: best-effort — missing CUDA still falls back later
        pass
