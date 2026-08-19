"""Bundled Chocolatey ShimGen must not shadow a working ffmpeg on PATH."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from pipeline.core import media


def test_is_real_ff_bin_rejects_shim_size(tmp_path: Path) -> None:
    shim = tmp_path / "ffmpeg.exe"
    shim.write_bytes(b"MZ" + b"\0" * 400)
    assert media._is_real_ff_bin(shim) is False
    real = tmp_path / "ffmpeg-real.exe"
    real.write_bytes(b"MZ" + b"\0" * media._FF_MIN_BYTES)
    assert media._is_real_ff_bin(real) is True


def test_ff_bin_skips_tiny_bundled_shim(tmp_path: Path, monkeypatch) -> None:
    ext = ".exe" if sys.platform == "win32" else ""
    bundle = tmp_path / "bundle"
    real_dir = tmp_path / "real"
    bundle.mkdir()
    real_dir.mkdir()
    (bundle / f"ffmpeg{ext}").write_bytes(b"MZ" + b"\0" * 400)
    real = real_dir / f"ffmpeg{ext}"
    real.write_bytes(b"MZ" + b"\0" * 400)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bundle), str(real_dir))))
    assert Path(media._ff_bin("ffmpeg")).resolve() == real.resolve()


def test_ff_bin_prefers_large_bundled(tmp_path: Path, monkeypatch) -> None:
    ext = ".exe" if sys.platform == "win32" else ""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    bundled = bundle / f"ffmpeg{ext}"
    bundled.write_bytes(b"MZ" + b"\0" * media._FF_MIN_BYTES)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", "")
    assert Path(media._ff_bin("ffmpeg")).resolve() == bundled.resolve()
