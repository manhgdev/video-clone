"""Packaged VideoClone desktop window: local API + built web UI."""
from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time
import traceback
import urllib.request
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path


def app_home() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "VideoClone"


home = app_home()
home.mkdir(parents=True, exist_ok=True)
os.environ["VIDEO_CLONE_DESKTOP"] = "1"
os.environ.setdefault("VIDEO_CLONE_HOME", str(home))
os.environ.setdefault("VIDEO_CLONE_DATA", str(home / "data"))
os.environ.setdefault("VIDEO_CLONE_PUBLIC_DATA", str(home / "public_data"))
os.environ.setdefault("CAPCUT_DEVICE_JSON", str(home / "capcut_device.json"))

if getattr(sys, "frozen", False) and sys.stdout is None:
    runtime_log = (home / "app.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = runtime_log
    sys.stderr = runtime_log

ocr_venv = home / ".venv-ocr"
ocr_site = (
    ocr_venv / "Lib" / "site-packages"
    if sys.platform == "win32"
    else ocr_venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
)
if ocr_site.is_dir():
    sys.path.insert(0, str(ocr_site))
    # CUDA pip wheels (cublas64_12.dll, …) — phải có trên PATH trước khi load ORT GPU
    nvidia_root = ocr_site / "nvidia"
    if nvidia_root.is_dir():
        cuda_bins = [str(p) for p in nvidia_root.glob("*/bin") if p.is_dir()]
        if cuda_bins:
            os.environ["PATH"] = os.pathsep.join(cuda_bins + [os.environ.get("PATH", "")])
            add_dir = getattr(os, "add_dll_directory", None)
            if add_dir and sys.platform == "win32":
                for b in cuda_bins:
                    try:
                        add_dir(b)
                    except OSError:
                        pass
    ocr_spec = PathFinder.find_spec("onnxruntime", [str(ocr_site)])
    if ocr_spec and ocr_spec.loader:
        ocr_module = module_from_spec(ocr_spec)
        sys.modules["onnxruntime"] = ocr_module
        try:
            ocr_spec.loader.exec_module(ocr_module)
        except Exception:
            traceback.print_exc()
            sys.modules.pop("onnxruntime", None)

bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
os.environ["PATH"] = os.pathsep.join((str(bundle), os.environ.get("PATH", "")))

# Ẩn cửa sổ console đen khi app GUI spawn ffmpeg / demucs / nvidia-smi
if sys.platform == "win32":
    try:
        import subprocess as _sp

        _no_win = int(getattr(_sp, "CREATE_NO_WINDOW", 0x08000000))
        _OrigPopen = _sp.Popen

        class _PopenNoWindow(_OrigPopen):  # type: ignore[misc, valid-type]
            def __init__(self, *a, **kw):
                if kw.get("creationflags") is None and not kw.get("shell"):
                    kw["creationflags"] = _no_win
                super().__init__(*a, **kw)

        _sp.Popen = _PopenNoWindow  # type: ignore[misc, assignment]
    except Exception:
        traceback.print_exc()


def app_version() -> str:
    for candidate in (
        bundle / "VERSION",
        Path(__file__).resolve().parent / "VERSION",
    ):
        try:
            v = candidate.read_text(encoding="utf-8").strip()
            if v:
                return v
        except OSError:
            pass
    return "1.0.0"


APP_VERSION = app_version()
os.environ.setdefault("VIDEO_CLONE_VERSION", APP_VERSION)

from fastapi.staticfiles import StaticFiles  # noqa: E402
from main import app  # noqa: E402
import uvicorn  # noqa: E402
import webview  # noqa: E402

web_dir = bundle / "dist"
app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")


def server_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/api/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_server(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_running():
            return True
        time.sleep(0.1)
    return False


def wait_for_parent_exit(pid: int) -> None:
    """Let a replacement desktop process wait until the old API releases its port."""
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 30_000)
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def centered_xy(width: int, height: int) -> tuple[int, int]:
    """Góc trên-trái để cửa sổ nằm giữa màn hình chính."""
    sw, sh = 1920, 1080
    try:
        if sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            sw = int(user32.GetSystemMetrics(0))
            sh = int(user32.GetSystemMetrics(1))
        elif sys.platform == "darwin":
            try:
                from AppKit import NSScreen  # type: ignore

                frame = NSScreen.mainScreen().frame()
                sw, sh = int(frame.size.width), int(frame.size.height)
            except Exception:
                pass
        else:
            try:
                import subprocess

                out = subprocess.check_output(
                    ["xrandr"], text=True, stderr=subprocess.DEVNULL, timeout=2
                )
                for line in out.splitlines():
                    if " connected" in line and " primary " in line:
                        # e.g. "eDP-1 connected primary 1920x1080+0+0"
                        for part in line.split():
                            if "x" in part and "+" in part:
                                res = part.split("+")[0]
                                w_s, h_s = res.split("x", 1)
                                sw, sh = int(w_s), int(h_s)
                                break
                        break
            except Exception:
                pass
    except Exception:
        pass
    x = max(0, (sw - width) // 2)
    y = max(0, (sh - height) // 2)
    return x, y


def run_desktop() -> int:
    if server_running():
        return 0

    config = uvicorn.Config(app, host="127.0.0.1", port=8787, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="videoclone-api", daemon=True)
    thread.start()
    try:
        if not wait_for_server():
            raise RuntimeError("VideoClone API did not start")
        win_w, win_h = 1440, 900
        x, y = centered_xy(win_w, win_h)
        icon = None
        for cand in (bundle / "app.ico", Path(__file__).resolve().parent / "app.ico"):
            if cand.is_file():
                icon = str(cand)
                break
        win_kw: dict = dict(
            width=win_w,
            height=win_h,
            x=x,
            y=y,
            min_size=(960, 640),
        )
        if icon:
            win_kw["icon"] = icon
        try:
            webview.create_window(
                f"VideoClone v{APP_VERSION}",
                "http://127.0.0.1:8787",
                **win_kw,
            )
        except TypeError:
            # pywebview cũ không hỗ trợ icon=
            win_kw.pop("icon", None)
            webview.create_window(
                f"VideoClone v{APP_VERSION}",
                "http://127.0.0.1:8787",
                **win_kw,
            )
        webview.start()
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--restart-after":
        wait_for_parent_exit(int(sys.argv[2]))
    elif len(sys.argv) > 1:
        raise SystemExit(2)
    raise SystemExit(run_desktop())
