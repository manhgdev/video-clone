"""Kiểm tra dependency runtime cho UI Thiết lập."""
from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run_ver(cmd: list[str], *, timeout: float = 4.0) -> str:
    try:
        out = subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        ).strip()
        return out.splitlines()[0][:120] if out else "ok"
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return f"error: {e}"


def _mod_ok(name: str) -> tuple[bool, str]:
    try:
        spec = importlib.util.find_spec(name)
        if spec is None:
            return False, "chưa cài"
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", None) or getattr(mod, "VERSION", None)
        return True, str(ver) if ver else "ok"
    except Exception as e:
        return False, str(e)[:80]


def _item(
    *,
    id: str,
    name: str,
    ok: bool,
    required: bool,
    detail: str,
    hint: str,
    install: str = "",
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "ok": ok,
        "required": required,
        "detail": detail,
        "hint": hint,
        "install": install,
    }


def system_checks() -> dict[str, Any]:
    """Danh sách dependency + ready/missing cho first-run UI."""
    items: list[dict[str, Any]] = []
    system = platform.system()
    machine = platform.machine()

    # Python (process đang chạy API)
    py_ok = sys.version_info >= (3, 10)
    items.append(
        _item(
            id="python",
            name="Python",
            ok=py_ok,
            required=True,
            detail=f"{sys.executable} · {platform.python_version()}",
            hint="Cần Python ≥ 3.10 cho backend FastAPI.",
            install="https://www.python.org/downloads/",
        )
    )

    # ffmpeg / ffprobe
    ff = _which("ffmpeg")
    items.append(
        _item(
            id="ffmpeg",
            name="ffmpeg",
            ok=bool(ff),
            required=True,
            detail=_run_ver(["ffmpeg", "-version"]) if ff else "không có trên PATH",
            hint="Bắt buộc để cắt audio, cover/burn, mux xuất video.",
            install=(
                "https://ffmpeg.org/download.html"
                if system != "Darwin"
                else "brew install ffmpeg"
            ),
        )
    )
    fp = _which("ffprobe")
    items.append(
        _item(
            id="ffprobe",
            name="ffprobe",
            ok=bool(fp),
            required=True,
            detail=_run_ver(["ffprobe", "-version"]) if fp else "không có trên PATH",
            hint="Thường đi kèm ffmpeg (cùng package).",
            install=(
                "https://ffmpeg.org/download.html"
                if system != "Darwin"
                else "brew install ffmpeg"
            ),
        )
    )

    # Python packages (server)
    for mid, title, req, hint, install in (
        (
            "faster_whisper",
            "faster-whisper",
            True,
            "ASR giọng nói (engine Whisper).",
            "pip install faster-whisper",
        ),
        (
            "rapidocr_onnxruntime",
            "RapidOCR",
            True,
            "OCR hardsub / nhãn trên khung.",
            "pip install rapidocr-onnxruntime",
        ),
        (
            "httpx",
            "httpx",
            True,
            "Gọi API dịch / TTS cloud.",
            "pip install httpx",
        ),
        (
            "PIL",
            "Pillow",
            True,
            "Vẽ caption khi burn (thường đi kèm RapidOCR).",
            "pip install pillow",
        ),
        (
            "cv2",
            "OpenCV",
            False,
            "Xử lý khung OCR (thường đi kèm RapidOCR).",
            "pip install opencv-python-headless",
        ),
    ):
        ok, detail = _mod_ok(mid)
        items.append(
            _item(
                id=mid,
                name=title,
                ok=ok,
                required=req,
                detail=detail,
                hint=hint,
                install=install,
            )
        )

    # TTS hệ thống
    if system == "Darwin":
        say = _which("say")
        items.append(
            _item(
                id="say",
                name="macOS say",
                ok=bool(say),
                required=False,
                detail=say or "không có",
                hint="TTS hệ thống khi không dùng CapCut/ElevenLabs.",
                install="",
            )
        )
    else:
        esp = _which("espeak-ng") or _which("espeak")
        items.append(
            _item(
                id="espeak",
                name="espeak-ng",
                ok=bool(esp),
                required=False,
                detail=esp or "không có",
                hint="TTS hệ thống Linux (tuỳ chọn).",
                install="sudo apt install espeak-ng",
            )
        )

    # Ollama (tuỳ chọn)
    ol = _which("ollama")
    ol_ok = False
    ol_detail = "chưa cài"
    if ol:
        ol_detail = _run_ver(["ollama", "--version"])
        try:
            import httpx

            r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.5)
            ol_ok = r.status_code < 500
            if ol_ok:
                n = len((r.json() or {}).get("models") or [])
                ol_detail = f"{ol_detail} · server OK · {n} model"
            else:
                ol_detail = f"{ol_detail} · server HTTP {r.status_code}"
        except Exception:
            ol_ok = True  # binary có; server có thể chưa bật
            ol_detail = f"{ol_detail} · binary OK (chưa ping được server)"
    items.append(
        _item(
            id="ollama",
            name="Ollama",
            ok=ol_ok,
            required=False,
            detail=ol_detail,
            hint="Dịch local (translator = ollama). Không bắt buộc nếu dùng Google/TikTok.",
            install="https://ollama.com/download",
        )
    )

    # Node (chỉ dev frontend — API không cần khi đã build static)
    node = _which("node")
    items.append(
        _item(
            id="node",
            name="Node.js",
            ok=bool(node),
            required=False,
            detail=_run_ver(["node", "-v"]) if node else "không có (chỉ cần khi dev UI)",
            hint="Chỉ cần khi chạy `npm run dev`. Bản packaged dùng UI build sẵn.",
            install="https://nodejs.org/",
        )
    )

    # Data dir
    try:
        from .config import DATA

        data_path = Path(DATA)
        data_path.mkdir(parents=True, exist_ok=True)
        data_ok = data_path.is_dir() and data_path.exists()
        data_detail = str(data_path.resolve())
    except Exception as e:
        data_ok = False
        data_detail = str(e)
    items.append(
        _item(
            id="data",
            name="Thư mục data",
            ok=data_ok,
            required=True,
            detail=data_detail,
            hint="Lưu project, cache OCR, TTS, file xuất.",
            install="",
        )
    )

    required_missing = [i for i in items if i["required"] and not i["ok"]]
    optional_missing = [i for i in items if not i["required"] and not i["ok"]]
    return {
        "ok": len(required_missing) == 0,
        "platform": f"{system} {machine}",
        "python": platform.python_version(),
        "items": items,
        "requiredMissing": [i["id"] for i in required_missing],
        "optionalMissing": [i["id"] for i in optional_missing],
        "summary": (
            "Sẵn sàng"
            if not required_missing
            else f"Thiếu {len(required_missing)} thành phần bắt buộc"
        ),
    }
