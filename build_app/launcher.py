"""Packaged VideoClone desktop window: local API + built web UI."""
from __future__ import annotations

import multiprocessing
import os
import shutil
import socket
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
# Ưu tiên GPU (CUDA/MPS); giới hạn thread CPU phụ — tránh đơ máy
os.environ.setdefault("VIENEU_BACKEND", "auto")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "2")
try:
    from pipeline.core.accel import apply_gpu_process_env

    apply_gpu_process_env()
except Exception:
    pass

# Các gói AI nặng được cài ở lần chạy đầu, ngoài thư mục app để nâng cấp không cần build lại EXE.
runtime_venv = home / ".venv-runtime"
runtime_site = (
    runtime_venv / "Lib" / "site-packages"
    if sys.platform == "win32"
    else runtime_venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
)

if getattr(sys, "frozen", False) and sys.stdout is None:
    runtime_log = (home / "app.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = runtime_log
    sys.stderr = runtime_log

if runtime_site.is_dir():
    sys.path.insert(0, str(runtime_site))
    if getattr(sys, "frozen", False):
        try:
            from pipeline.core.runtime_site import (
                install_runtime_meta_path,
                prepare_cv2_import_path,
                preload_cv2,
            )

            install_runtime_meta_path(runtime_site)
            prepare_cv2_import_path(runtime_site)
            # Phải import cv2 khi path[0] = runtime site-packages.
            # Gắn .venv-ocr / .../cv2 trước → OpenCV recursion → app tắt khi Dịch.
            preload_cv2()
        except Exception:
            traceback.print_exc()

ocr_venv = home / ".venv-ocr"
ocr_site = (
    ocr_venv / "Lib" / "site-packages"
    if sys.platform == "win32"
    else ocr_venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
)
if ocr_site.is_dir():
    # KHÔNG sys.path.insert(.venv-ocr) — path[0] ≠ runtime → OpenCV recursion crash khi Dịch.
    # Chỉ nạp CUDA DLL + onnxruntime GPU module (không đưa ocr site vào sys.path).
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
    # Gỡ mọi entry .venv-ocr + .../cv2 đã lọt vào path (bản cũ / plugin)
    def _path_ok(p: str) -> bool:
        n = p.replace("\\", "/").rstrip("/").lower()
        if n.endswith("/cv2"):
            return False
        if "/.venv-ocr/" in f"/{n}/" or n.endswith("/.venv-ocr/lib/site-packages"):
            return False
        return True

    sys.path[:] = [p for p in sys.path if _path_ok(p)]
    if "onnxruntime" not in sys.modules:
        # Load ORT by file path only — never leave ocr site on sys.path
        ocr_spec = PathFinder.find_spec("onnxruntime", [str(ocr_site)])
        if ocr_spec and ocr_spec.loader:
            ocr_module = module_from_spec(ocr_spec)
            sys.modules["onnxruntime"] = ocr_module
            try:
                ocr_spec.loader.exec_module(ocr_module)
            except Exception:
                traceback.print_exc()
                sys.modules.pop("onnxruntime", None)
    # Runtime luôn path[0] sau bước ocr
    if runtime_site.is_dir():
        try:
            from pipeline.core.runtime_site import prepare_cv2_import_path

            prepare_cv2_import_path(runtime_site)
        except Exception:
            _rt = str(runtime_site)
            while _rt in sys.path:
                sys.path.remove(_rt)
            sys.path.insert(0, _rt)
            sys.path[:] = [p for p in sys.path if _path_ok(p)]

bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
os.environ["PATH"] = os.pathsep.join((str(bundle), os.environ.get("PATH", "")))

# Seed giọng zmAI đi kèm; không ghi đè giọng hoặc metadata người dùng đã sửa.
bundled_voice_refs = bundle / "resources" / "voice-ref"
user_voice_refs = home / "resources" / "voice-ref"
if bundled_voice_refs.is_dir():
    user_voice_refs.mkdir(parents=True, exist_ok=True)
    for source in bundled_voice_refs.iterdir():
        target = user_voice_refs / source.name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)

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


API_HOST = "127.0.0.1"
API_PORT_PREFERRED = 8787
API_PORT_SCAN = 100  # ponytail: 8787–8886, rồi OS chọn port ngẫu nhiên


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def pick_api_port(
    host: str = API_HOST,
    preferred: int = API_PORT_PREFERRED,
    span: int = API_PORT_SCAN,
) -> int:
    """Chọn port trống — ưu tiên preferred, tránh đụng app khác trên 8787."""
    for port in range(preferred, preferred + span):
        if _port_in_use(host, port):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def api_base(port: int) -> str:
    return f"http://{API_HOST}:{port}"


def server_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"{api_base(port)}/api/health", timeout=1) as response:
            if response.status != 200:
                return False
            body = response.read(512).decode("utf-8", errors="replace")
            return '"app":"videoclone"' in body.replace(" ", "")
    except Exception:
        return False


def wait_for_server(port: int, timeout: float = 120.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_running(port):
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
    try:
        from pipeline.core.app_log import append_log, install_process_hooks

        install_process_hooks()
        append_log(f"[desktop] start v{APP_VERSION}")
    except Exception:
        traceback.print_exc()
    port = pick_api_port()
    os.environ["VIDEO_CLONE_PORT"] = str(port)
    base = api_base(port)
    print(f"VideoClone API → {base}", flush=True)

    config = uvicorn.Config(app, host=API_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="videoclone-api", daemon=True)
    thread.start()
    try:
        if not wait_for_server(port):
            print(f"[desktop] API did not start on {base}", flush=True)
            # Giữ cửa sổ thông báo thay vì im lặng exit
            try:
                webview.create_window(
                    f"VideoClone v{APP_VERSION}",
                    html=(
                        "<html><body style='font-family:sans-serif;padding:2rem'>"
                        f"<h2>Không mở được API</h2><p>{base}</p>"
                        "<p>Xem log: %LOCALAPPDATA%\\VideoClone\\app.log</p>"
                        "</body></html>"
                    ),
                    width=520,
                    height=280,
                )
                webview.start()
            except Exception:
                traceback.print_exc()
            return 1
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
                base,
                **win_kw,
            )
        except TypeError:
            # pywebview cũ không hỗ trợ icon=
            win_kw.pop("icon", None)
            webview.create_window(
                f"VideoClone v{APP_VERSION}",
                base,
                **win_kw,
            )
        # webview.start() chặn đến khi user đóng cửa sổ — không thoát vì lỗi job nền
        try:
            webview.start()
        except Exception:
            traceback.print_exc()
            print("[desktop] webview.start failed — xem app.log", flush=True)
            return 1
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--restart-after":
            wait_for_parent_exit(int(sys.argv[2]))
        elif len(sys.argv) > 1:
            raise SystemExit(2)
        raise SystemExit(run_desktop())
    except SystemExit:
        raise
    except BaseException:
        # Mọi lỗi khởi động: ghi log, không silent die
        traceback.print_exc()
        try:
            (home / "app.log").open("a", encoding="utf-8").write(
                f"\n[fatal] {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            traceback.print_exc(file=(home / "app.log").open("a", encoding="utf-8"))
        except Exception:
            pass
        raise SystemExit(1)
