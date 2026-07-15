"""Kiểm tra dependency runtime cho UI Thiết lập."""
from __future__ import annotations

import importlib.metadata
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
        dists = importlib.metadata.packages_distributions().get(name) or []
        return True, importlib.metadata.version(dists[0]) if dists else "ok"
    except Exception as e:
        return False, str(e)[:80]


def _ocr_cuda_check() -> tuple[bool, str]:
    try:
        # Import helper trong process con để các DLL CUDA cài bằng pip
        # được thêm vào PATH trước khi ONNX tạo session.
        out = subprocess.check_output(
            [
                sys.executable,
                "-c",
                (
                    "from pipeline.asr import _rapidocr_labels; "
                    "e=_rapidocr_labels(use_cuda=True); "
                    "print(','.join(e.text_det.infer.session.get_providers()))"
                ),
            ],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=15,
        ).strip()
        return "CUDAExecutionProvider" in out, out
    except (subprocess.SubprocessError, OSError) as e:
        return False, str(e)[:160]


def install_ocr_cuda() -> dict[str, Any]:
    """Install the OCR GPU runtime into the Python running this API."""
    ok, detail = _ocr_cuda_check()
    if ok:
        return {"ok": True, "message": "GPU tăng tốc đã được cài", "detail": detail}
    pip = [sys.executable, "-m", "pip"]
    subprocess.run(
        pip + ["uninstall", "-y", "onnxruntime", "onnxruntime-gpu"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    try:
        proc = subprocess.run(
            pip
            + [
                "install",
                "--progress-bar",
                "off",
                "onnxruntime-gpu[cuda,cudnn]>=1.23,<1.24",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
    except Exception:
        # ponytail: keep OCR usable if the optional 2 GB GPU install fails.
        subprocess.run(
            pip + ["install", "onnxruntime"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        raise
    ok, detail = _ocr_cuda_check()
    if not ok:
        subprocess.run(
            pip + ["uninstall", "-y", "onnxruntime-gpu"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            pip + ["install", "onnxruntime"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        raise RuntimeError(f"CUDA provider unavailable after install: {detail}")
    return {"ok": True, "message": "Đã cài GPU tăng tốc", "detail": detail}


def _demucs_venv_python() -> Path | None:
    server_root = Path(__file__).resolve().parents[2]
    py = server_root / ".venv-demucs" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    return py if py.is_file() else None


def _apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64")


def _demucs_check() -> tuple[bool, str]:
    """Demucs sẵn sàng.

    - Apple Silicon → demucs-mlx (Metal)
    - NVIDIA → torch CUDA
    - Không GPU → torch CPU cũng ok
    """
    want_cuda = bool(_which("nvidia-smi"))
    want_mlx = _apple_silicon()
    py = _demucs_venv_python()
    if not py:
        return False, "chưa có server/.venv-demucs (bấm Cài đặt)"

    if want_mlx:
        try:
            r = subprocess.run(
                [
                    str(py),
                    "-c",
                    (
                        "import demucs_mlx, soundfile; "
                        "import importlib.metadata as m; "
                        "print(m.version('demucs-mlx'))"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)[:160]
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "import fail").strip()[-160:]
            return False, err or "chưa có demucs-mlx (Apple GPU)"
        ver = (r.stdout or "").strip() or "?"
        return True, f"mlx · Apple Silicon · demucs-mlx {ver}"

    try:
        r = subprocess.run(
            [
                str(py),
                "-c",
                (
                    "import demucs, soundfile, torch; "
                    "d=('cuda' if torch.cuda.is_available() else "
                    "('mps' if getattr(torch.backends,'mps',None) and torch.backends.mps.is_available() else 'cpu')); "
                    "n=(torch.cuda.get_device_name(0) if d=='cuda' else ('Apple GPU' if d=='mps' else 'CPU')); "
                    "print(f'{d}|{n}|{torch.__version__}')"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:160]
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "import fail").strip()[-160:]
        return False, err or "import demucs/torch thất bại"
    parts = (r.stdout or "").strip().split("|")
    device = parts[0] if parts else "?"
    name = parts[1] if len(parts) > 1 else "?"
    ver = parts[2] if len(parts) > 2 else "?"
    detail = f"{device} · {name} · torch {ver}"
    if want_cuda and device != "cuda":
        return False, f"đang CPU (chậm) — {detail}"
    return True, detail


def install_demucs_cuda() -> dict[str, Any]:
    """Cài Demucs tối ưu: NVIDIA CUDA / Apple demucs-mlx / CPU."""
    ok, detail = _demucs_check()
    if ok:
        return {"ok": True, "message": "Demucs đã sẵn sàng", "detail": detail}
    from pipeline.export.mux import _demucs_python

    py = Path(_demucs_python(None, report=False))
    ok, detail = _demucs_check()
    if not ok:
        raise RuntimeError(f"Demucs chưa sẵn sàng sau khi cài: {detail} · python={py}")
    label = "Apple GPU" if _apple_silicon() else ("NVIDIA GPU" if _which("nvidia-smi") else "CPU")
    return {"ok": True, "message": f"Đã cài Demucs ({label})", "detail": detail}


def _item(
    *,
    id: str,
    name: str,
    ok: bool,
    required: bool,
    detail: str,
    hint: str,
    install: str = "",
    installLabel: str = "",
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "ok": ok,
        "required": required,
        "detail": detail,
        "hint": hint,
        "install": install,
        "installLabel": installLabel,
    }


def _plan_item(plan: dict[str, Any], item_id: str) -> dict[str, Any]:
    items = plan.get("items") if isinstance(plan.get("items"), dict) else {}
    raw = items.get(item_id) if isinstance(items, dict) else None
    return raw if isinstance(raw, dict) else {}


def _install_from_plan(plan: dict[str, Any], item_id: str) -> tuple[str, str, str]:
    """Trả (install_value, install_label, hint) theo thiết bị."""
    p = _plan_item(plan, item_id)
    kind = str(p.get("kind") or "")
    value = str(p.get("value") or "")
    label = str(p.get("label") or "")
    hint = str(p.get("hint") or "")
    if kind == "none" or not value:
        return "", label, hint
    if kind == "action" and not p.get("relevant", True):
        return "", label, hint
    return value, label, hint


def system_checks() -> dict[str, Any]:
    """Danh sách dependency + ready/missing cho first-run UI."""
    from .media import detect_device

    items: list[dict[str, Any]] = []
    system = platform.system()
    machine = platform.machine()
    device = detect_device()
    plan = device.get("install") or {}

    # ── Thiết bị (luôn hiện đầu) — quyết định path cài ──
    gpu_line = device.get("gpuName") or "không có GPU tăng tốc"
    if device.get("vramMb"):
        gpu_line = f"{gpu_line} · {device['vramMb']} MB"
    if device.get("driver"):
        gpu_line = f"{gpu_line} · driver {device['driver']}"
    items.append(
        _item(
            id="device",
            name=f"Thiết bị · {device.get('osLabel')}",
            ok=True,
            required=True,
            detail=(
                f"{device.get('osLabel')} · {device.get('arch')} · "
                f"GPU: {gpu_line} · accel={device.get('accel')}"
            ),
            hint=str(plan.get("hint") or ""),
        )
    )

    # Python
    py_ok = sys.version_info >= (3, 10)
    py_inst, py_lab, py_hint = _install_from_plan(plan, "python")
    items.append(
        _item(
            id="python",
            name="Python",
            ok=py_ok,
            required=True,
            detail=f"{sys.executable} · {platform.python_version()}",
            hint=py_hint or "Cần Python ≥ 3.10 cho backend FastAPI.",
            install=py_inst,
            installLabel=py_lab,
        )
    )

    # ffmpeg / ffprobe
    ff = _which("ffmpeg")
    ff_inst, ff_lab, ff_hint = _install_from_plan(plan, "ffmpeg")
    items.append(
        _item(
            id="ffmpeg",
            name="ffmpeg",
            ok=bool(ff),
            required=True,
            detail=_run_ver(["ffmpeg", "-version"]) if ff else "không có trên PATH",
            hint=ff_hint or "Bắt buộc để cắt audio, cover/burn, mux xuất video.",
            install=ff_inst,
            installLabel=ff_lab,
        )
    )
    fp = _which("ffprobe")
    fp_inst, fp_lab, fp_hint = _install_from_plan(plan, "ffprobe")
    items.append(
        _item(
            id="ffprobe",
            name="ffprobe",
            ok=bool(fp),
            required=True,
            detail=_run_ver(["ffprobe", "-version"]) if fp else "không có trên PATH",
            hint=fp_hint or "Thường đi kèm ffmpeg (cùng package).",
            install=fp_inst,
            installLabel=fp_lab,
        )
    )

    # Python packages
    for mid, title, req in (
        ("faster_whisper", "faster-whisper", True),
        ("rapidocr_onnxruntime", "RapidOCR", True),
        ("httpx", "httpx", True),
        ("PIL", "Pillow", True),
        ("cv2", "OpenCV", False),
    ):
        ok, detail = _mod_ok(mid)
        inst, lab, hint = _install_from_plan(plan, mid)
        items.append(
            _item(
                id=mid,
                name=title,
                ok=ok,
                required=req,
                detail=detail,
                hint=hint,
                install=inst,
                installLabel=lab,
            )
        )

    # OCR CUDA — chỉ relevant trên NVIDIA
    cuda_ok, cuda_detail = _ocr_cuda_check()
    ocr_inst, ocr_lab, ocr_hint = _install_from_plan(plan, "ocr_cuda")
    nvidia = device.get("gpuKind") == "nvidia"
    items.append(
        _item(
            id="ocr_cuda",
            name="GPU tăng tốc OCR / Whisper",
            ok=cuda_ok if nvidia else True,
            required=False,
            detail=(
                cuda_detail
                if nvidia
                else (
                    "Apple Silicon — không dùng CUDA"
                    if device.get("gpuKind") == "apple"
                    else "Không có NVIDIA — OCR chạy CPU"
                )
            ),
            hint=ocr_hint,
            install=ocr_inst,
            installLabel=ocr_lab,
        )
    )

    # Demucs
    demucs_ok, demucs_detail = _demucs_check()
    dem_inst, dem_lab, dem_hint = _install_from_plan(plan, "demucs")
    items.append(
        _item(
            id="demucs",
            name="Demucs (xóa lời)",
            ok=demucs_ok,
            required=False,
            detail=demucs_detail,
            hint=dem_hint,
            install=dem_inst,
            installLabel=dem_lab,
        )
    )

    # TTS hệ thống — theo OS trong plan
    if system == "Darwin":
        say = _which("say")
        t_inst, t_lab, t_hint = _install_from_plan(plan, "say")
        items.append(
            _item(
                id="say",
                name=str(_plan_item(plan, "say").get("name") or "macOS say"),
                ok=bool(say),
                required=False,
                detail=say or "không có",
                hint=t_hint,
                install=t_inst,
                installLabel=t_lab,
            )
        )
    else:
        esp = _which("espeak-ng") or _which("espeak")
        t_inst, t_lab, t_hint = _install_from_plan(plan, "espeak")
        items.append(
            _item(
                id="espeak",
                name=str(_plan_item(plan, "espeak").get("name") or "espeak-ng"),
                ok=bool(esp),
                required=False,
                detail=esp or "không có",
                hint=t_hint,
                install=t_inst,
                installLabel=t_lab,
            )
        )

    # Ollama
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
            ol_ok = True
            ol_detail = f"{ol_detail} · binary OK (chưa ping được server)"
    ol_inst, ol_lab, ol_hint = _install_from_plan(plan, "ollama")
    items.append(
        _item(
            id="ollama",
            name="Ollama",
            ok=ol_ok,
            required=False,
            detail=ol_detail,
            hint=ol_hint or "Dịch local (translator = ollama).",
            install=ol_inst,
            installLabel=ol_lab,
        )
    )

    # Node
    node = _which("node")
    nd_inst, nd_lab, nd_hint = _install_from_plan(plan, "node")
    items.append(
        _item(
            id="node",
            name="Node.js",
            ok=bool(node),
            required=False,
            detail=_run_ver(["node", "-v"]) if node else "không có (chỉ cần khi dev UI)",
            hint=nd_hint,
            install=nd_inst,
            installLabel=nd_lab,
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
    _, _, data_hint = _install_from_plan(plan, "data")
    items.append(
        _item(
            id="data",
            name="Thư mục data",
            ok=data_ok,
            required=True,
            detail=data_detail,
            hint=data_hint or "Lưu project, cache OCR, TTS, file xuất.",
        )
    )

    required_missing = [i for i in items if i["required"] and not i["ok"]]
    optional_missing = [i for i in items if not i["required"] and not i["ok"]]
    return {
        "ok": len(required_missing) == 0,
        "platform": f"{system} {machine}",
        "python": platform.python_version(),
        "device": device,
        "items": items,
        "requiredMissing": [i["id"] for i in required_missing],
        "optionalMissing": [i["id"] for i in optional_missing],
        "summary": (
            "Sẵn sàng"
            if not required_missing
            else f"Thiếu {len(required_missing)} thành phần bắt buộc"
        ),
    }
