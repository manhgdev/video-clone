"""Frozen app probes OCR CUDA via .venv-ocr subprocess, not in-process ORT."""
from pathlib import Path
from unittest.mock import MagicMock

from pipeline.core import system_check as sc


def test_ocr_cuda_check_uses_ocr_venv_when_frozen(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sc.sys, "frozen", True, raising=False)
    ocr_py = tmp_path / "python.exe"
    ocr_py.write_text("")
    monkeypatch.setattr(sc, "_ocr_python", lambda: ocr_py)
    seen: list[Path] = []

    def fresh(py: Path) -> tuple[bool, str]:
        seen.append(py)
        return True, "CUDAExecutionProvider,CPUExecutionProvider"

    monkeypatch.setattr(sc, "_ocr_cuda_check_fresh", fresh)

    ok, detail = sc._ocr_cuda_check()

    assert ok is True
    assert seen == [ocr_py]
    assert "CUDA" in detail


def test_ocr_cuda_check_in_process_when_not_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sc.sys, "frozen", False, raising=False)
    fake_ort = MagicMock()
    fake_ort.get_available_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)
    monkeypatch.setattr(
        "pipeline.ocr.extract.prepare_cuda_dlls",
        lambda: None,
        raising=False,
    )

    ok, detail = sc._ocr_cuda_check()

    assert ok is True
    assert "CUDAExecutionProvider" in detail
