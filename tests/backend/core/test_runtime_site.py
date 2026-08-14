"""cv2 must not go through runtime meta-path — OpenCV re-imports itself."""
import sys
from pathlib import Path

from pipeline.core import runtime_site as rs
from pipeline.core.system_check import probe


def test_cv2_not_in_runtime_meta_roots() -> None:
    assert "cv2" not in rs._RUNTIME_ROOTS
    assert "cv2" in rs._PURGE_ROOTS


def test_sanitize_drops_cv2_package_dir(monkeypatch) -> None:
    fake = [
        "C:/app",
        "C:/venv/Lib/site-packages/cv2",
        "C:/venv/Lib/site-packages",
        "C:/venv/Lib/site-packages\\cv2",
    ]
    monkeypatch.setattr(sys, "path", fake)
    rs._sanitize_cv2_sys_path()
    assert all(not p.replace("\\", "/").rstrip("/").endswith("/cv2") for p in sys.path)
    assert "C:/venv/Lib/site-packages" in sys.path


def test_preload_cv2_puts_runtime_first(monkeypatch, tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    site.mkdir()
    ocr = tmp_path / "ocr-site"
    ocr.mkdir()
    monkeypatch.setattr(rs.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rs, "runtime_site_packages", lambda: site)
    monkeypatch.setattr(sys, "path", [str(ocr), str(site), "C:/other"])

    class _Ok:
        CascadeClassifier = object

    monkeypatch.setitem(sys.modules, "cv2", _Ok())
    rs.preload_cv2()  # already loaded with CascadeClassifier → early return
    # Force path fix path by clearing marker
    sys.modules.pop("cv2", None)

    calls: list[str] = []

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "cv2" or (fromlist and "cv2" in str(name)):
            calls.append(sys.path[0])
            mod = _Ok()
            sys.modules["cv2"] = mod
            return mod
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)
    rs.preload_cv2()
    assert sys.path[0] == str(site)
    assert calls and calls[0] == str(site)


def test_probe_rejects_hollow_cv2(monkeypatch) -> None:
    class _Hollow:
        pass

    monkeypatch.setattr(probe.sys, "frozen", False, raising=False)
    monkeypatch.setattr(probe.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr("builtins.__import__", lambda name, *args, **kwargs: _Hollow() if name == "cv2" else __import__(name, *args, **kwargs))
    assert probe._mod_ok("cv2") == (False, "cv2 thiếu VideoCapture")
