"""Kiểm tra dependency runtime cho UI Thiết lập."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


def _mod_ok(name: str, *, dist_map: dict[str, list[str]] | None = None) -> tuple[bool, str]:
    try:
        if getattr(sys, "frozen", False) and name in _AI_RUNTIME_MODULES:
            return _runtime_mod_ok(name)
        if importlib.util.find_spec(name) is None:
            return False, "chưa cài"
        __import__(name)
        # Import OK = package usable. Metadata có thể hỏng (~orch dist-info) — đừng coi là thiếu.
        try:
            dists = (dist_map or _pkg_distributions()).get(name) or []
            if dists:
                return True, importlib.metadata.version(dists[0])
        except Exception:
            pass
        return True, "ok"
    except Exception as e:
        return False, str(e)[:80]


def _runtime_mod_ok(name: str) -> tuple[bool, str]:
    """Probe import in .venv-runtime — PyInstaller parent cannot import AI wheels."""
    return _runtime_modules_batch_ok([name]).get(name, (False, "unknown"))


def _runtime_modules_batch_ok(names: list[str]) -> dict[str, tuple[bool, str]]:
    """One subprocess for all runtime imports — avoids 8× cold-start on system checks."""
    py = _runtime_python()
    if not py.is_file():
        return {n: (False, "thiếu .venv-runtime") for n in names}
    payload = json.dumps(names)
    script = (
        "import json, sys\n"
        "names = json.loads(sys.argv[1])\n"
        "out = {}\n"
        "for n in names:\n"
        "  try:\n"
        "    __import__(n)\n"
        "    out[n] = [True, 'ok']\n"
        "  except Exception as e:\n"
        "    out[n] = [False, str(e)[:80]]\n"
        "print(json.dumps(out))\n"
    )
    try:
        proc = subprocess.run(
            [str(py), "-c", script, payload],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        return {n: (False, "timeout") for n in names}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "import fail").strip()[-80:]
        return {n: (False, err or "import fail") for n in names}
    try:
        raw = json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return {n: (False, "probe parse fail") for n in names}
    out: dict[str, tuple[bool, str]] = {}
    for n in names:
        pair = raw.get(n) if isinstance(raw, dict) else None
        if isinstance(pair, list) and len(pair) >= 2:
            out[n] = bool(pair[0]), str(pair[1])
        else:
            out[n] = False, "missing"
    return out


_PKG_DIST: dict[str, list[str]] | None = None


def _pkg_distributions() -> dict[str, list[str]]:
    global _PKG_DIST
    if _PKG_DIST is None:
        _PKG_DIST = importlib.metadata.packages_distributions()
    return _PKG_DIST


_CHECKS_CACHE: tuple[float, bool, dict[str, Any]] | None = None
_CHECKS_TTL = 45.0
_RUNTIME_FAST_DIST = (
    "faster-whisper",
    "rapidocr-onnxruntime",
    "transformers",
    "vieneu",
    "torch",
)
_TORCH_CUDA_CACHE: tuple[float, bool] | None = None
_TORCH_CUDA_TTL = 120.0
_OCR_CUDA_CACHE: tuple[float, tuple[bool, str]] | None = None
_OCR_CUDA_TTL = 60.0
_DEMUCS_PY_CACHE: tuple[float, Path | None] | None = None
_DEMUCS_PY_TTL = 300.0


def _invalidate_checks_cache() -> None:
    global _CHECKS_CACHE, _TORCH_CUDA_CACHE, _OCR_CUDA_CACHE, _demucs_cache, _DEMUCS_PY_CACHE
    _CHECKS_CACHE = None
    _TORCH_CUDA_CACHE = None
    _OCR_CUDA_CACHE = None
    _demucs_cache = None
    _DEMUCS_PY_CACHE = None


def _torch_cuda_ready_cached() -> bool:
    global _TORCH_CUDA_CACHE
    now = time.monotonic()
    if _TORCH_CUDA_CACHE and now - _TORCH_CUDA_CACHE[0] < _TORCH_CUDA_TTL:
        return _TORCH_CUDA_CACHE[1]
    ok = _torch_cuda_ready()
    _TORCH_CUDA_CACHE = (now, ok)
    return ok


def _ocr_cuda_check_cached(*, refresh: bool = False) -> tuple[bool, str]:
    global _OCR_CUDA_CACHE
    now = time.monotonic()
    if not refresh and _OCR_CUDA_CACHE and now - _OCR_CUDA_CACHE[0] < _OCR_CUDA_TTL:
        return _OCR_CUDA_CACHE[1]
    result = _ocr_cuda_check()
    _OCR_CUDA_CACHE = (now, result)
    return result


_AI_RUNTIME_PACKAGES = (
    "faster-whisper>=1.1.0",
    "rapidocr-onnxruntime>=1.2.0",
    "pillow",
    "opencv-python-headless",
    "huggingface-hub>=0.34,<1.0",
    "perth",
    "pyyaml",
    "sea-g2p",
    "soundfile",
    "soxr",
    "tokenizers",
    "transformers==4.57.6",
)
_VIENEU_PACKAGE = "vieneu>=3.2.0"
_TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu124"
_TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
_AI_RUNTIME_MODULES = (
    "faster_whisper",
    "rapidocr_onnxruntime",
    "PIL",
    "cv2",
    "torch",
    "torchaudio",
    "transformers",
    "vieneu",
)


def _nvidia_present() -> bool:
    return bool(_which("nvidia-smi"))


def _apple_silicon_runtime() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64")


def _runtime_torch_accel() -> str:
    if _nvidia_present():
        return "cuda"
    if _apple_silicon_runtime():
        return "mac"
    return "cpu"


def _torch_cuda_ready() -> bool:
    if getattr(sys, "frozen", False):
        try:
            from pipeline.tts.engines.vieneu_frozen import runtime_torch_cuda_ready

            return runtime_torch_cuda_ready()
        except Exception:
            return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _runtime_python() -> Path:
    if getattr(sys, "frozen", False):
        home = Path(os.environ["VIDEO_CLONE_HOME"])
        venv = home / ".venv-runtime"
        return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return Path(sys.executable)


def _ocr_python() -> Path:
    if getattr(sys, "frozen", False):
        home = Path(os.environ["VIDEO_CLONE_HOME"])
        venv = home / ".venv-ocr"
        return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return Path(sys.executable)


def _video_clone_home() -> Path:
    home = os.environ.get("VIDEO_CLONE_HOME", "").strip()
    if home:
        return Path(home)
    if getattr(sys, "frozen", False):
        raise RuntimeError("VIDEO_CLONE_HOME missing")
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "VideoClone"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VideoClone"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "VideoClone"


def _venv_site_packages(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Lib" / "site-packages"
    ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return venv / "lib" / ver / "site-packages"


def _site_has_dist(sp: Path, prefix: str) -> bool:
    if not sp.is_dir():
        return False
    pre = prefix.lower().replace("_", "-")
    for entry in sp.iterdir():
        name = entry.name.lower().replace("_", "-")
        if name.startswith(pre):
            return True
    return False


def _runtime_venv_fast() -> tuple[bool, str]:
    """Filesystem-only — no torch/whisper import subprocess."""
    venv = _video_clone_home() / ".venv-runtime"
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not py.is_file():
        return False, "chưa cài"
    sp = _venv_site_packages(venv)
    missing = [n for n in _RUNTIME_FAST_DIST if not _site_has_dist(sp, n)]
    if missing:
        return False, f"thiếu: {', '.join(missing)}"
    return True, "đã cài · .venv-runtime"


def _ocr_venv_fast() -> tuple[bool, str]:
    py = _ocr_python()
    if not py.is_file():
        return False, "chưa cài"
    sp = _venv_site_packages(py.parent.parent)
    if not _site_has_dist(sp, "onnxruntime"):
        return False, "chưa cài onnxruntime-gpu"
    return True, "đã cài · bấm Kiểm tra lại để xác minh CUDA"


def _demucs_venv_fast() -> tuple[bool, str]:
    from pipeline.export.stem import _demucs_install_root, _demucs_py_in

    py = _demucs_py_in(_demucs_install_root())
    if not py.is_file():
        return False, "chưa cài"
    sp = _venv_site_packages(py.parent.parent)
    if _apple_silicon():
        if _site_has_dist(sp, "demucs-mlx") or _site_has_dist(sp, "demucs_mlx"):
            return True, "đã cài · Apple Metal"
        return False, "thiếu demucs-mlx"
    if _site_has_dist(sp, "demucs"):
        return True, "đã cài"
    return False, "thiếu demucs"


def _mod_ok_fast(name: str) -> tuple[bool, str]:
    if importlib.util.find_spec(name) is None:
        return False, "chưa cài"
    return True, "ok"


def _clear_torch_modules() -> None:
    for name in list(sys.modules):
        if name == "torch" or name.startswith("torch."):
            del sys.modules[name]


def _runtime_pip_cmd(*extra: str) -> list[str]:
    if getattr(sys, "frozen", False):
        home = Path(os.environ["VIDEO_CLONE_HOME"])
        venv = home / ".venv-runtime"
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        uv = shutil.which("uv")
        if not uv or not py.is_file():
            raise RuntimeError("Bản ứng dụng thiếu uv hoặc venv runtime — vào Thiết lập → Cài gói AI")
        return [uv, "pip", "install", "--python", str(py), *extra]
    return [sys.executable, "-m", "pip", "install", *extra]


def _runtime_pip_uninstall_cmd(*packages: str) -> list[str]:
    if getattr(sys, "frozen", False):
        home = Path(os.environ["VIDEO_CLONE_HOME"])
        venv = home / ".venv-runtime"
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        uv = shutil.which("uv")
        if not uv or not py.is_file():
            raise RuntimeError("Bản ứng dụng thiếu uv hoặc venv runtime — vào Thiết lập → Cài gói AI")
        return [uv, "pip", "uninstall", "--python", str(py), "-y", *packages]
    return [sys.executable, "-m", "pip", "uninstall", "-y", *packages]


def _runtime_pip_install(
    *packages: str,
    index_url: str | None = None,
    timeout: float = 600,
) -> None:
    if not packages:
        return
    cmd = _runtime_pip_cmd("--upgrade", *packages)
    if index_url:
        cmd.extend(["--index-url", index_url])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout)[-2000:])


def _install_runtime_torch(*, accel: str | None = None) -> None:
    """PyTorch khớp GPU — VieNeu auto chỉ dùng CUDA khi torch.cuda sẵn sàng."""
    wanted = accel or _runtime_torch_accel()
    if wanted == "cuda":
        subprocess.run(
            _runtime_pip_uninstall_cmd("torch", "torchaudio", "torchvision"),
            capture_output=True,
            text=True,
            timeout=300,
        )
        _runtime_pip_install(
            "torch",
            "torchaudio",
            index_url=_TORCH_CUDA_INDEX,
            timeout=2400,
        )
        return
    if wanted == "mac":
        _runtime_pip_install("torch", "torchaudio", timeout=1200)
        return
    idx = None if sys.platform == "darwin" else _TORCH_CPU_INDEX
    _runtime_pip_install("torch", "torchaudio", index_url=idx, timeout=1200)


_torch_warm_done = False  # once per process — không spam pip / log


def _runtime_torch_needs_install() -> bool:
    # Chỉ import được mới tin — metadata ~orch hỏng không bắt reinstall.
    if not _mod_ok("torch")[0]:
        return True
    if not _mod_ok("torchaudio")[0]:
        return True
    return _nvidia_present() and not _torch_cuda_ready_cached()


def _torch_dll_locked() -> bool:
    """True nếu torch đã load trong process này — pip đụng _C.pyd → WinError 5."""
    return "torch" in sys.modules or any(k.startswith("torch.") for k in sys.modules)


def ensure_runtime_torch() -> None:
    """VieNeu zmAI/clone cần torch(+audio); NVIDIA cần bản CUDA (không phải PyPI CPU)."""
    global _torch_warm_done
    if _torch_warm_done:
        return
    if getattr(sys, "frozen", False):
        from .runtime_site import ensure_runtime_import, install_runtime_meta_path

        install_runtime_meta_path()
        try:
            ensure_runtime_import("torch")
            ensure_runtime_import("torchaudio")
        except Exception:
            pass
    if not _runtime_torch_needs_install():
        _torch_warm_done = True
        return

    # Dev: torch đã load (uvicorn/worker) → tuyệt đối không pip (Access denied _C.pyd).
    # Thiếu torchaudio: cài tay khi tắt backend, không auto-pip trong warm.
    if not getattr(sys, "frozen", False) and (
        _torch_dll_locked() or _mod_ok("torch")[0]
    ):
        if not _mod_ok("torchaudio")[0]:
            print(
                "[ensure_runtime_torch] torchaudio missing — skip pip while server running. "
                "Stop backend then: pip install torchaudio --index-url "
                f"{_TORCH_CUDA_INDEX if _nvidia_present() else _TORCH_CPU_INDEX}",
                flush=True,
            )
        _torch_warm_done = True
        return

    before_cuda = _torch_cuda_ready()
    try:
        _install_runtime_torch()
    except Exception as exc:
        # WinError 5 / pip fail — log 1 lần, không kill warm-models
        print(f"[ensure_runtime_torch] install skipped: {exc}", flush=True)
        _torch_warm_done = True
        return
    if not before_cuda:
        _clear_torch_modules()
    if getattr(sys, "frozen", False):
        from .runtime_site import bootstrap_ai_runtime, install_runtime_meta_path

        install_runtime_meta_path()
        bootstrap_ai_runtime()
    _torch_warm_done = True


def ensure_runtime_transformers() -> None:
    """VieNeu PyTorch backend cần transformers (đăng ký model_type vieneu_v3)."""
    from .runtime_site import (
        bootstrap_ai_runtime,
        install_runtime_meta_path,
        runtime_site_packages,
        verify_transformers_ok,
        _purge_external_modules,
    )

    bootstrap_ai_runtime()
    ok, _detail = verify_transformers_ok()
    if ok:
        return
    _runtime_pip_install(
        "transformers==4.57.6",
        "huggingface-hub>=0.34,<1.0",
        "safetensors",
        timeout=1200,
    )
    root = runtime_site_packages()
    if root:
        _purge_external_modules(root)
    install_runtime_meta_path()
    bootstrap_ai_runtime()
    ok, detail = verify_transformers_ok()
    if not ok:
        raise RuntimeError(
            f"transformers chưa import được sau cài đặt: {detail}. "
            "Thử Thiết lập → Cài gói AI rồi khởi động lại app."
        )


def ensure_torchaudio() -> None:
    ensure_runtime_torch()


def _ai_runtime_detail(*, torch_cuda: bool | None = None) -> str:
    base = "Whisper · OCR · zmAI · VieNeu Local"
    if _nvidia_present():
        cuda = torch_cuda if torch_cuda is not None else _torch_cuda_ready_cached()
        if cuda:
            try:
                import torch

                return f"{base} · VieNeu CUDA · {torch.cuda.get_device_name(0)}"
            except Exception:
                return f"{base} · VieNeu CUDA"
        return f"{base} · VieNeu ONNX/CPU (cần PyTorch CUDA)"
    return base


def install_ai_runtime() -> dict[str, Any]:
    """Cài nhóm ASR/OCR nặng vào venv riêng của bản desktop."""
    if getattr(sys, "frozen", False):
        ok, detail = _runtime_venv_fast()
        if ok:
            return {
                "ok": True,
                "message": "Gói AI đã sẵn sàng",
                "detail": detail,
            }
        missing = list(_AI_RUNTIME_MODULES)
        needs_torch = _nvidia_present()
    else:
        missing = [name for name in _AI_RUNTIME_MODULES if not _mod_ok(name)[0]]
        needs_torch = _runtime_torch_needs_install()
    if not missing and not needs_torch:
        return {
            "ok": True,
            "message": "Gói AI đã sẵn sàng",
            "detail": _ai_runtime_detail(),
        }

    if getattr(sys, "frozen", False):
        home = Path(os.environ["VIDEO_CLONE_HOME"])
        venv = home / ".venv-runtime"
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("Bản ứng dụng thiếu uv để cài gói AI")
        if not py.is_file():
            subprocess.run(
                [uv, "venv", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", "--seed", str(venv)],
                check=True,
                capture_output=True,
                text=True,
                timeout=900,
            )
        # opencv-python + headless cùng lúc → đụng cv2; chỉ giữ headless.
        subprocess.run(
            [uv, "pip", "uninstall", "--python", str(py), "-y", "opencv-python"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        base_cmd = [uv, "pip", "install", "--python", str(py), "--upgrade", *_AI_RUNTIME_PACKAGES]
        vieneu_cmd = [
            uv, "pip", "install", "--python", str(py), "--upgrade", "--no-deps", _VIENEU_PACKAGE
        ]
    else:
        base_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *_AI_RUNTIME_PACKAGES]
        vieneu_cmd = [
            sys.executable, "-m", "pip", "install", "--upgrade", "--no-deps", _VIENEU_PACKAGE
        ]

    if missing:
        proc = subprocess.run(base_cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout)[-3000:])
    if "vieneu" in missing or not _mod_ok("vieneu")[0]:
        proc = subprocess.run(vieneu_cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout)[-3000:])
    if needs_torch:
        _install_runtime_torch()
        _clear_torch_modules()
    _invalidate_checks_cache()
    return {
        "ok": True,
        "message": "Đã cài gói AI",
        "detail": _ai_runtime_detail(),
    }


def _ocr_cuda_check() -> tuple[bool, str]:
    """Probe ORT providers — frozen app probes .venv-ocr subprocess (in-process ORT is CPU stub)."""
    if getattr(sys, "frozen", False):
        py = _ocr_python()
        if py.is_file():
            return _ocr_cuda_check_fresh(py)
        return False, "thiếu .venv-ocr"
    try:
        from pipeline.ocr.extract import prepare_cuda_dlls

        prepare_cuda_dlls()
        import onnxruntime as ort

        providers = list(ort.get_available_providers())
        detail = ",".join(providers) if providers else "no providers"
        return "CUDAExecutionProvider" in providers, detail
    except Exception as e:
        return False, str(e)[:160]


def _ocr_cuda_check_fresh(python: str | Path = sys.executable) -> tuple[bool, str]:
    """Probe ORT in a new process; this API may still hold the old CPU DLL."""
    try:
        proc = subprocess.run(
            [
                str(python),
                "-c",
                "import onnxruntime as ort; print(','.join(ort.get_available_providers()))",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    detail = (proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr.strip())[:500]
    return proc.returncode == 0 and "CUDAExecutionProvider" in detail, detail or "no providers"


def install_ocr_cuda() -> dict[str, Any]:
    """Install the OCR GPU runtime into the Python running this API."""
    if getattr(sys, "frozen", False):
        ok, detail = _ocr_venv_fast()
        if ok:
            return {"ok": True, "message": "GPU tăng tốc đã được cài", "detail": detail}
    ok, detail = _ocr_cuda_check()
    if ok:
        return {"ok": True, "message": "GPU tăng tốc đã được cài", "detail": detail}
    if getattr(sys, "frozen", False):
        home = Path(os.environ["VIDEO_CLONE_HOME"])
        venv = home / ".venv-ocr"
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("Bản ứng dụng thiếu uv để cài OCR GPU")
        if not py.is_file():
            subprocess.run(
                [uv, "venv", "--python", "3.12", "--seed", str(venv)],
                check=True,
                capture_output=True,
                text=True,
                timeout=900,
            )
        proc = subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(py),
                "--upgrade",
                "onnxruntime-gpu[cuda,cudnn]>=1.23,<1.24",
            ],
            capture_output=True,
            text=True,
            timeout=1200,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout)[-2000:])
        ok, detail = _ocr_cuda_check_fresh(py)
        if not ok:
            raise RuntimeError(f"CUDA provider unavailable after install: {detail}")
        _invalidate_checks_cache()
        return {
            "ok": True,
            "message": "Đã cài OCR GPU",
            "detail": detail,
        }
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
        proc = subprocess.run(
            pip
            + [
                "install",
                "--force-reinstall",
                "--no-deps",
                "--progress-bar",
                "off",
                "onnxruntime-gpu>=1.23,<1.24",
            ],
            capture_output=True,
            text=True,
            timeout=300,
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
    # ponytail: Windows keeps the old ORT DLL mapped until this API exits; verify after restart.
    _invalidate_checks_cache()
    return {"ok": True, "message": "Đã cài GPU tăng tốc", "detail": "CUDAExecutionProvider"}


def _demucs_venv_python() -> Path | None:
    """Tìm python .venv-demucs — ưu tiên venv đã import được demucs."""
    global _DEMUCS_PY_CACHE
    now = time.monotonic()
    if _DEMUCS_PY_CACHE and now - _DEMUCS_PY_CACHE[0] < _DEMUCS_PY_TTL:
        return _DEMUCS_PY_CACHE[1]

    from pipeline.export.stem import _demucs_py_in, _demucs_root_candidates

    candidates: list[Path] = []
    for root in _demucs_root_candidates():
        py = _demucs_py_in(root)
        if py.is_file():
            candidates.append(py)
    if not candidates:
        _DEMUCS_PY_CACHE = (now, None)
        return None

    def _import_ok(exe: Path) -> bool:
        try:
            r = subprocess.run(
                [str(exe), "-c", "import demucs, soundfile"],
                capture_output=True,
                timeout=25,
            )
            if r.returncode == 0:
                return True
            r2 = subprocess.run(
                [str(exe), "-c", "import demucs_mlx, soundfile"],
                capture_output=True,
                timeout=25,
            )
            return r2.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    found: Path | None = None
    for py in candidates:
        if _import_ok(py):
            found = py
            break
    if found is None:
        found = candidates[0]
    _DEMUCS_PY_CACHE = (now, found)
    return found


def _apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64")


def _demucs_check_uncached() -> tuple[bool, str]:
    """Demucs sẵn sàng.

    - Apple Silicon → demucs-mlx (Metal)
    - NVIDIA → torch CUDA
    - Không GPU → torch CPU cũng ok
    """
    want_cuda = bool(_which("nvidia-smi"))
    want_mlx = _apple_silicon()
    py = _demucs_venv_python()
    if not py:
        return False, "chưa có backend/.venv-demucs (bấm Cài đặt)"

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
                timeout=45,
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
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:160]
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if "No module named 'demucs'" in err or "No module named \"demucs\"" in err:
            return False, "chưa cài demucs trong .venv-demucs (bấm Cài đặt)"
        if "No module named 'torch'" in err:
            return False, "chưa cài torch trong .venv-demucs (bấm Cài đặt)"
        short = err.splitlines()[-1][:160] if err else "import demucs/torch thất bại"
        return False, short
    parts = (r.stdout or "").strip().split("|")
    device = parts[0] if parts else "?"
    name = parts[1] if len(parts) > 1 else "?"
    ver = parts[2] if len(parts) > 2 else "?"
    detail = f"{device} · {name} · torch {ver}"
    if want_cuda and device != "cuda":
        return False, f"đang CPU (chậm) — {detail}"
    return True, detail


_DEMUCS_CACHE_TTL = 300.0
_demucs_cache: tuple[float, tuple[bool, str]] | None = None
_demucs_cache_lock = threading.Lock()


def _demucs_check(*, refresh: bool = False) -> tuple[bool, str]:
    global _demucs_cache
    with _demucs_cache_lock:
        now = time.monotonic()
        if not refresh and _demucs_cache and now - _demucs_cache[0] < _DEMUCS_CACHE_TTL:
            return _demucs_cache[1]
        result = _demucs_check_uncached()
        _demucs_cache = (now, result)
        return result


def install_demucs_cuda() -> dict[str, Any]:
    """Cài Demucs tối ưu: NVIDIA CUDA / Apple demucs-mlx / CPU."""
    ok, detail = _demucs_check()
    if ok:
        return {"ok": True, "message": "Demucs đã sẵn sàng", "detail": detail}
    from pipeline.export.stem import _demucs_python

    py = Path(_demucs_python(None, report=False))
    ok, detail = _demucs_check(refresh=True)
    if not ok:
        raise RuntimeError(f"Demucs chưa sẵn sàng sau khi cài: {detail} · python={py}")
    label = "Apple GPU" if _apple_silicon() else ("NVIDIA GPU" if _which("nvidia-smi") else "CPU")
    _invalidate_checks_cache()
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


def system_checks(*, refresh: bool = False, fast: bool = True) -> dict[str, Any]:
    """Danh sách dependency + ready/missing cho first-run UI."""
    global _CHECKS_CACHE
    if refresh:
        _invalidate_checks_cache()
    elif (
        _CHECKS_CACHE
        and _CHECKS_CACHE[1] == fast
        and time.monotonic() - _CHECKS_CACHE[0] < _CHECKS_TTL
    ):
        return _CHECKS_CACHE[2]
    result = _system_checks_uncached(fast=fast)
    _CHECKS_CACHE = (time.monotonic(), fast, result)
    return result


def _system_checks_uncached(*, fast: bool = True) -> dict[str, Any]:
    """fast=True: chỉ PATH/venv/dist-info (~vài giây). fast=False: import probe nặng."""
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

    dist = None if fast else _pkg_distributions()
    nvidia = device.get("gpuKind") == "nvidia"
    if fast:
        demucs_ok, demucs_detail = _demucs_venv_fast()
        cuda_ok, cuda_detail = _ocr_venv_fast() if nvidia else (True, "")
        torch_cuda_ok = True
        if getattr(sys, "frozen", False):
            runtime_ok, runtime_detail = _runtime_venv_fast()
            runtime_missing = [] if runtime_ok else ["ai_runtime"]
        else:
            runtime_missing = [
                mid for mid in _AI_RUNTIME_MODULES if not _mod_ok_fast(mid)[0]
            ]
            runtime_detail = (
                "đã cài"
                if not runtime_missing
                else f"thiếu: {', '.join(runtime_missing)}"
            )
        runtime_torch_cuda = False
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_demucs = pool.submit(_demucs_check)
            fut_ocr = pool.submit(_ocr_cuda_check_cached) if nvidia else None
            fut_cuda = pool.submit(_torch_cuda_ready_cached) if _nvidia_present() else None
            demucs_ok, demucs_detail = fut_demucs.result()
            cuda_ok, cuda_detail = fut_ocr.result() if fut_ocr else (True, "")
            torch_cuda_ok = fut_cuda.result() if fut_cuda else True

        runtime_missing = [
            mid
            for mid in _AI_RUNTIME_MODULES
            if not _runtime_modules_batch_ok(list(_AI_RUNTIME_MODULES)).get(mid, (False, ""))[0]
        ] if getattr(sys, "frozen", False) else [
            mid for mid in _AI_RUNTIME_MODULES if not _mod_ok(mid, dist_map=dist)[0]
        ]
        runtime_torch_cuda = _nvidia_present() and not torch_cuda_ok
        runtime_detail = (
            _ai_runtime_detail(torch_cuda=torch_cuda_ok)
            if not runtime_missing
            else f"thiếu: {', '.join(runtime_missing)}"
        )
    items.append(
        _item(
            id="ai_runtime",
            name="Gói AI · Whisper + OCR + VieNeu",
            ok=not runtime_missing and not runtime_torch_cuda,
            required=True,
            detail=runtime_detail,
            hint=(
                "Cài một lần để dùng Whisper, OCR, zmAI và VieNeu Local. "
                "Có NVIDIA: VieNeu dùng GPU khi PyTorch CUDA đã cài (vài phút)."
            ),
            install="ai_runtime",
            installLabel="Cài gói AI",
        )
    )

    for mid, title, req in (("httpx", "httpx", True),):
        ok, detail = _mod_ok_fast(mid) if fast else _mod_ok(mid, dist_map=dist)
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
    ocr_inst, ocr_lab, ocr_hint = _install_from_plan(plan, "ocr_cuda")
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

    # Demucs — đã probe ở trên
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
        if fast:
            ol_ok = True
        else:
            try:
                import httpx

                r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=0.6)
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
        "fast": fast,
    }
