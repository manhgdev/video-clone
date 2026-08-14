"""Fast system checks avoid heavy subprocess imports."""
import time
from pathlib import Path
from unittest.mock import MagicMock

from pipeline.core import system_check as sc


def test_system_checks_fast_skips_heavy_probes(monkeypatch) -> None:
    heavy = MagicMock(return_value=(True, "heavy"))
    monkeypatch.setattr(sc, "_demucs_check", heavy)
    monkeypatch.setattr(sc, "_ocr_cuda_check_cached", heavy)
    monkeypatch.setattr(sc, "_torch_cuda_ready_cached", lambda: True)
    monkeypatch.setattr(sc, "_runtime_modules_batch_ok", lambda names: {n: (True, "ok") for n in names})
    monkeypatch.setattr(sc, "_demucs_venv_fast", lambda: (True, "fast demucs"))
    monkeypatch.setattr(sc, "_ocr_venv_fast", lambda: (True, "fast ocr"))
    monkeypatch.setattr(sc, "_runtime_venv_fast", lambda: (True, "fast ai"))
    monkeypatch.setattr(sc.sys, "frozen", True, raising=False)
    monkeypatch.setenv("VIDEO_CLONE_HOME", str(Path("C:/VideoCloneTest")))
    sc._invalidate_checks_cache()

    result = sc.system_checks(fast=True)

    assert result["fast"] is True
    heavy.assert_not_called()


def test_system_checks_deep_runs_heavy_probes(monkeypatch) -> None:
    called = {"demucs": False}

    def mark_demucs(*, refresh=False):
        called["demucs"] = True
        return True, "deep demucs"

    monkeypatch.setattr(sc, "_demucs_check", mark_demucs)
    monkeypatch.setattr(sc, "_ocr_cuda_check_cached", lambda **kw: (True, "ocr"))
    monkeypatch.setattr(sc, "_torch_cuda_ready_cached", lambda: True)
    monkeypatch.setattr(
        sc,
        "_runtime_modules_batch_ok",
        lambda names: {n: (True, "ok") for n in names},
    )
    monkeypatch.setattr(sc.sys, "frozen", True, raising=False)
    monkeypatch.setenv("VIDEO_CLONE_HOME", str(Path("C:/VideoCloneTest")))
    sc._invalidate_checks_cache()

    result = sc.system_checks(fast=False)

    assert result["fast"] is False
    assert called["demucs"] is True


def test_clean_frozen_machine_marks_ai_groups_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "pipeline.core.media.detect_device",
        lambda: {
            "osLabel": "Windows",
            "arch": "x64",
            "gpuKind": "cpu",
            "accel": "cpu",
            "install": {},
        },
    )
    monkeypatch.setattr(sc, "_which", lambda _name: None)
    monkeypatch.setattr(sc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sc, "_runtime_venv_fast", lambda: (False, "chưa cài"))
    monkeypatch.setattr(sc, "_demucs_venv_fast", lambda: (False, "chưa cài"))
    monkeypatch.setattr(sc, "_ocr_venv_fast", lambda: (True, "not relevant"))
    sc._invalidate_checks_cache()

    result = sc.system_checks(fast=True)
    by_id = {item["id"]: item for item in result["items"]}

    assert by_id["ai_runtime"]["ok"] is False
    assert by_id["ai_runtime_ocr"]["ok"] is False
    assert by_id["ai_runtime_vieneu"]["ok"] is False
    assert "python" not in by_id

    # Desktop setup must never expose Python even if a launcher is misdetected as non-frozen.
    monkeypatch.setattr(sc.sys, "frozen", False, raising=False)
    sc._invalidate_checks_cache()
    result = sc.system_checks(fast=True)
    assert "python" not in {item["id"] for item in result["items"]}
