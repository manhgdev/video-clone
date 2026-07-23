"""Bootstrap AI packages from .venv-runtime under PyInstaller frozen builds.

PyInstaller --exclude-module leaves hollow entries in sys.modules; find_spec still
passes so Thiết lập báo OK while VieNeu fails on ``from transformers import …``.
Install a meta-path finder so runtime-root imports resolve from site-packages.
"""
from __future__ import annotations

import os
import sys
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path

# add_dll_directory() trả handle — phải giữ sống hoặc GC sẽ xóa dir khỏi DLL search path!
_dll_handles: list = []

_RUNTIME_ROOTS = frozenset(
    {
        "accelerate",
        "ctranslate2",
        # ponytail: không hook cv2 — OpenCV bootstrap tự import lại "cv2" lần 2;
        # meta-path sẽ chạy lại __init__ → "recursion is detected".
        "datasets",
        "faster_whisper",
        "huggingface_hub",
        "numpy",
        "onnxruntime",
        "PIL",
        "perth",
        "rapidocr_onnxruntime",
        "safetensors",
        "sea_g2p",
        "soundfile",
        "soxr",
        "tokenizers",
        "torch",
        "torchaudio",
        "transformers",
        "vieneu",
        "yaml",
    }
)

# Stub/hollow modules to drop from sys.modules (may include packages not in meta-path).
_PURGE_ROOTS = _RUNTIME_ROOTS | frozenset({"cv2"})

_AI_PRELOAD = (
    "safetensors",
    "huggingface_hub",
    "tokenizers",
    "torch",
    "transformers",
)


class _RuntimeSiteFinder:
    """Redirect excluded AI imports to runtime venv before PyInstaller frozen importer."""

    def __init__(self, site: str) -> None:
        self._site = site

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        root = fullname.split(".", 1)[0]
        if root not in _RUNTIME_ROOTS:
            return None
        # ponytail: only top-level — PathFinder on dotted names can resolve
        # huggingface_hub.utils.tqdm → site-packages/tqdm (wrong module).
        if "." in fullname:
            return None
        return PathFinder.find_spec(fullname, [self._site])


def runtime_site_packages() -> Path | None:
    """Site-packages dir for the desktop runtime venv (frozen) or None in dev."""
    if not getattr(sys, "frozen", False):
        return None
    home = (os.environ.get("VIDEO_CLONE_HOME") or "").strip()
    if not home:
        return None
    venv = Path(home) / ".venv-runtime"
    if sys.platform == "win32":
        site = venv / "Lib" / "site-packages"
    else:
        site = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    return site if site.is_dir() else None


def prepare_runtime_torch_dlls(site: Path) -> None:
    """Add torch/lib to DLL search path (frozen + external torch wheels)."""
    torch_lib = site / "torch" / "lib"
    if not torch_lib.is_dir():
        return
    lib_s = str(torch_lib)
    os.environ["PATH"] = lib_s + os.pathsep + os.environ.get("PATH", "")
    add_dir = getattr(os, "add_dll_directory", None)
    if add_dir and sys.platform == "win32":
        try:
            handle = add_dir(lib_s)
            if handle is not None:
                _dll_handles.append(handle)
        except OSError:
            pass


def _purge_external_modules(site: Path) -> None:
    site_s = str(site).replace("\\", "/")
    for key in list(sys.modules):
        root = key.split(".", 1)[0]
        if root not in _PURGE_ROOTS:
            continue
        mod = sys.modules.get(key)
        if mod is None:
            sys.modules.pop(key, None)
            continue
        mod_file = (getattr(mod, "__file__", "") or "").replace("\\", "/")
        if mod_file and site_s in mod_file:
            continue
        sys.modules.pop(key, None)
    # OpenCV leftover after failed/partial import
    if hasattr(sys, "OpenCV_LOADER"):
        try:
            delattr(sys, "OpenCV_LOADER")
        except Exception:
            pass


def _sanitize_cv2_sys_path() -> None:
    """Drop path entries that break OpenCV bootstrap (recursion).

    Known killers from app.log:
    - .../site-packages/cv2  (package dir, not parent site-packages)
    - .../.venv-ocr/...      (OCR CUDA venv before runtime)
    """
    cleaned: list[str] = []
    for p in sys.path:
        if not p:
            continue
        norm = p.replace("\\", "/").rstrip("/").lower()
        if norm.endswith("/cv2"):
            continue
        if "/.venv-ocr/" in f"/{norm}/" or norm.endswith("/.venv-ocr/lib/site-packages"):
            continue
        # Windows short / mixed case site-packages\\cv2
        if norm.replace("/", "\\").endswith("\\cv2"):
            continue
        cleaned.append(p)
    sys.path[:] = cleaned


def prepare_cv2_import_path(site: Path | None = None) -> str | None:
    """Force runtime site-packages to sys.path[0]; strip ocr/cv2 traps. Returns site str."""
    root = site or runtime_site_packages()
    _sanitize_cv2_sys_path()
    if not root or not root.is_dir():
        return None
    site_s = str(root)
    while site_s in sys.path:
        sys.path.remove(site_s)
    sys.path.insert(0, site_s)
    _sanitize_cv2_sys_path()
    return site_s


def ensure_cv2():
    """Import OpenCV an toàn (frozen + dev). Trả module hoặc raise ImportError.

    OpenCV bootstrap re-import ``cv2`` khi path[0] là site khác (.venv-ocr)
    hoặc khi ``.../site-packages/cv2`` nằm trên sys.path → recursion → APP tắt.
    """
    existing = sys.modules.get("cv2")
    if existing is not None and getattr(existing, "VideoCapture", None):
        return existing

    root = runtime_site_packages()
    # Dev: path sạch rồi import
    if not getattr(sys, "frozen", False) or not root or not root.is_dir():
        _sanitize_cv2_sys_path()
        import cv2 as _cv2

        return _cv2

    prepare_cv2_import_path(root)

    if hasattr(sys, "OpenCV_LOADER"):
        try:
            delattr(sys, "OpenCV_LOADER")
        except Exception:
            pass
    for key in list(sys.modules):
        if key == "cv2" or key.startswith("cv2."):
            sys.modules.pop(key, None)

    # Re-assert path immediately before import (other code may mutate sys.path)
    prepare_cv2_import_path(root)
    import cv2 as _cv2  # noqa: F401

    # OpenCV bootstrap có thể thêm site-packages/cv2 vào path; dọn lại.
    _sanitize_cv2_sys_path()

    if not getattr(_cv2, "VideoCapture", None):
        raise ImportError("cv2 loaded without VideoCapture")
    return _cv2


def preload_cv2() -> None:
    """Import OpenCV once while runtime site-packages is sys.path[0]."""
    if not getattr(sys, "frozen", False):
        return
    try:
        ensure_cv2()
        try:
            from pipeline.core.app_log import append_log

            append_log("[cv2] preload ok", also_print=False)
        except Exception:
            pass
    except Exception as e:
        try:
            from pipeline.core.app_log import append_exception

            append_exception("[cv2] preload failed", e)
        except Exception:
            pass


def install_runtime_meta_path(site: Path | None = None) -> None:
    """Hook meta-path so runtime venv wins over PyInstaller exclude stubs (frozen only)."""
    if not getattr(sys, "frozen", False):
        return
    root = site or runtime_site_packages()
    if not root or not root.is_dir():
        return
    prepare_cv2_import_path(root)
    prepare_runtime_torch_dlls(root)
    # CUDA DLL paths phải được đăng ký TRƯỚC khi onnxruntime/rapidocr được import qua meta-path.
    # Reset để re-scan sau khi prepare_cv2_import_path đã thêm .venv-runtime vào sys.path[0].
    try:
        from pipeline.ocr.extract_parts.runtime import _reset_cuda_dlls, prepare_cuda_dlls
        _reset_cuda_dlls()  # force re-scan — sys.path vừa có .venv-runtime
        prepare_cuda_dlls()
    except Exception:
        pass
    _purge_external_modules(root)
    site_s = str(root)
    if not any(isinstance(f, _RuntimeSiteFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _RuntimeSiteFinder(site_s))
    # Sau khi purge, preload cv2 ngay từ .venv-runtime — tránh recursion khi rapidocr import cv2
    preload_cv2()


def _load_from_site(name: str, site: Path) -> None:
    spec = PathFinder.find_spec(name, [str(site)])
    if not spec or not spec.loader:
        raise ModuleNotFoundError(name)
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)


def ensure_runtime_import(name: str, *, site: Path | None = None) -> None:
    """Import module from runtime venv; frozen builds install meta-path + purge stubs."""
    install_runtime_meta_path(site)
    if not getattr(sys, "frozen", False):
        __import__(name)
        return
    root = site or runtime_site_packages()
    if not root or not root.is_dir():
        raise ModuleNotFoundError(name)
    existing = sys.modules.get(name)
    if existing is not None:
        mod_file = (getattr(existing, "__file__", "") or "").replace("\\", "/")
        if mod_file and str(root).replace("\\", "/") in mod_file:
            return
    _purge_external_modules(root)
    if name == "torch":
        __import__("torch")
        return
    _load_from_site(name, root)


def bootstrap_ai_runtime(site: Path | None = None) -> None:
    """Preload HF/transformers stack before VieNeu model code runs (frozen only)."""
    if not getattr(sys, "frozen", False):
        return
    root = site or runtime_site_packages()
    if not root or not root.is_dir():
        return
    install_runtime_meta_path(root)
    for mod in _AI_PRELOAD:
        try:
            ensure_runtime_import(mod, site=root)
        except Exception:
            pass


def verify_transformers_ok() -> tuple[bool, str]:
    """True when transformers loads PretrainedConfig (VieNeu v3 — pin 4.57.x)."""
    try:
        install_runtime_meta_path()
        ensure_runtime_import("torch")
        ensure_runtime_import("transformers")
        import importlib

        import transformers

        cfg = importlib.import_module("transformers.configuration_utils")
        if not (getattr(cfg, "PretrainedConfig", None) or getattr(cfg, "PreTrainedConfig", None)):
            raise ImportError("missing PretrainedConfig")
        return True, str(getattr(transformers, "__version__", "") or "ok")
    except Exception as e:
        return False, str(e)[:160]
