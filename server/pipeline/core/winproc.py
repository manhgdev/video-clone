"""Windows: ẩn cửa sổ console khi spawn subprocess (ffmpeg, python, nvidia-smi…)."""
from __future__ import annotations

import subprocess
import sys

_patched = False


def hide_console_kwargs() -> dict:
    """Kwargs cho Popen/run/check_output — không hiện CMD đen."""
    if sys.platform != "win32":
        return {}
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {"creationflags": flags}


def apply_subprocess_no_window() -> None:
    """Patch subprocess.Popen một lần — mọi call sau không flash CMD."""
    global _patched
    if _patched or sys.platform != "win32":
        return
    _patched = True
    _orig = subprocess.Popen
    no_win = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))

    class Popen(_orig):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            # Không ghi đè nếu caller đã set flags / shell interactive
            if kwargs.get("creationflags") is None and not kwargs.get("shell"):
                kwargs["creationflags"] = no_win
            super().__init__(*args, **kwargs)

    subprocess.Popen = Popen  # type: ignore[misc, assignment]
