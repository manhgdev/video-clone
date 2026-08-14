"""Cài gói AI: kiểm theo THIẾT KẾ HIỆN TẠI.

- Dev (không frozen): install_ai_runtime chỉ cài nhóm pip (Whisper/OCR/VieNeu),
  KHÔNG đụng torch — torch giao cho ensure_runtime_torch với guard DLL-locked
  (torch đã load bởi backend đang chạy thì tuyệt đối không pip).
- ensure_runtime_torch chỉ cài khi torch thật sự vắng mặt và .pyd không bị lock.
"""
import pytest
from types import SimpleNamespace
from pathlib import Path

from pipeline.core import system_check
from pipeline.core.media import _gpu_kind_from_name


def test_ai_runtime_includes_soundfile_native_dependency() -> None:
    assert "cffi" in system_check._AI_RUNTIME_PACKAGES
    assert {"cffi", "pycparser"}.issubset(system_check._PKG_WHISPER)
    assert {"soundfile", "cffi"}.issubset(system_check._AI_RUNTIME_MODULES)


def test_frozen_runtime_provisions_managed_python(monkeypatch, tmp_path):
    calls = []
    py = tmp_path / ".venv-runtime" / "Scripts" / "python.exe"

    def fake_stream(cmd, **_kwargs):
        calls.append(cmd)
        if "venv" in cmd:
            py.parent.mkdir(parents=True)
            py.touch()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(system_check, "_pip_stream", fake_stream)
    result = system_check._ensure_frozen_runtime_venv("uv.exe", tmp_path / ".venv-runtime")

    assert result == py
    assert calls[0][:3] == ["uv.exe", "python", "install"]
    assert calls[1][:2] == ["uv.exe", "venv"]


def test_runtime_ort_accel_uses_detected_hardware(monkeypatch):
    monkeypatch.setattr(
        "pipeline.core.media.detect_device",
        lambda: {"gpuKind": "amd", "accel": "directml"},
    )
    assert system_check._runtime_ort_accel() == "directml"


def test_demucs_refresh_drops_cached_missing_venv(monkeypatch):
    from pipeline.core.system_check import probe

    probe._DEMUCS_PY_CACHE = (float("inf"), None)
    monkeypatch.setattr(
        probe,
        "_demucs_check_uncached",
        lambda: (probe._DEMUCS_PY_CACHE is None, "fresh"),
    )

    assert probe._demucs_check(refresh=True) == (True, "fresh")


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("NVIDIA GeForce RTX 4090", "nvidia"),
        ("NVIDIA RTX A6000", "nvidia"),
        ("Tesla T4", "nvidia"),
        ("AMD Radeon RX 7900 XTX", "amd"),
        ("Radeon PRO W7900", "amd"),
        ("AMD FirePro W9100", "amd"),
        ("Intel(R) UHD Graphics 770", "intel"),
        ("Intel Iris Xe Graphics", "intel"),
        ("Intel Arc A770", "intel"),
    ],
)
def test_gpu_family_classification(name, kind):
    assert _gpu_kind_from_name(name) == kind


def test_ai_runtime_skips_install_when_ready(monkeypatch):
    monkeypatch.setattr(system_check, "_mod_ok", lambda _name: (True, "ok"))
    monkeypatch.setattr(system_check, "_runtime_torch_needs_install", lambda: False)

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("installer must not run when every runtime module is ready")

    monkeypatch.setattr(system_check.subprocess, "run", unexpected_run)
    result = system_check.install_ai_runtime()

    assert result["ok"] is True
    assert "Whisper" in result["detail"]
    assert "VieNeu Local" in result["detail"]


def test_ai_runtime_dev_delegates_torch_to_ensure(monkeypatch):
    """Dev mode: torch thiếu → install_ai_runtime vẫn ok nhưng KHÔNG tự cài torch."""
    monkeypatch.setattr(system_check, "_nvidia_present", lambda: True)
    monkeypatch.setattr(system_check, "_torch_cuda_ready", lambda: False)
    monkeypatch.setattr(system_check, "_torch_cuda_ready_cached", lambda: False)
    monkeypatch.setattr(system_check, "_torch_broken", lambda: False)
    monkeypatch.setattr(
        system_check,
        "_mod_ok",
        lambda name: (name not in ("torch", "torchaudio"), "ok" if name not in ("torch", "torchaudio") else "chưa cài"),
    )
    torch_calls = []
    monkeypatch.setattr(system_check, "_install_runtime_torch", lambda **_kw: torch_calls.append("torch"))
    monkeypatch.setattr(
        system_check, "_pip_stream",
        lambda cmd: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = system_check.install_ai_runtime()

    assert result["ok"] is True
    assert torch_calls == [], "dev mode không được pip torch từ install_ai_runtime"


def test_ensure_runtime_torch_installs_cuda_when_absent(monkeypatch):
    """torch vắng hẳn + NVIDIA + không lock → cài bản CUDA đúng một lần."""
    monkeypatch.setattr(system_check, "_torch_warm_done", False)
    monkeypatch.setattr(system_check, "_nvidia_present", lambda: True)
    monkeypatch.setattr(system_check, "_torch_cuda_ready", lambda: False)
    monkeypatch.setattr(system_check, "_torch_cuda_ready_cached", lambda: False)
    monkeypatch.setattr(system_check, "_torch_dll_locked", lambda: False)
    monkeypatch.setattr(
        system_check,
        "_mod_ok",
        lambda name: (name not in ("torch", "torchaudio"), "ok"),
    )
    calls = []
    monkeypatch.setattr(system_check, "_install_runtime_torch", lambda **_kw: calls.append("cuda"))
    monkeypatch.setattr(system_check, "_clear_torch_modules", lambda: None)
    system_check.ensure_runtime_torch()
    assert calls == ["cuda"]


def test_ensure_runtime_torch_dev_guard_skips_pip(monkeypatch):
    """torch đã import được (backend đang chạy) → guard chặn pip, không gọi installer."""
    monkeypatch.setattr(system_check, "_torch_warm_done", False)
    monkeypatch.setattr(system_check, "_nvidia_present", lambda: True)
    monkeypatch.setattr(system_check, "_torch_cuda_ready", lambda: False)
    monkeypatch.setattr(system_check, "_torch_cuda_ready_cached", lambda: False)
    monkeypatch.setattr(system_check, "_torch_dll_locked", lambda: False)
    monkeypatch.setattr(system_check, "_mod_ok", lambda name: (name != "torchaudio", "ok"))

    def unexpected(**_kw):
        raise AssertionError("không được pip khi torch đang load trong process dev")

    monkeypatch.setattr(system_check, "_install_runtime_torch", unexpected)
    system_check.ensure_runtime_torch()


def test_ensure_torchaudio_skips_when_ready(monkeypatch):
    monkeypatch.setattr(system_check, "_runtime_torch_needs_install", lambda: False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("must not pip install")

    monkeypatch.setattr(system_check, "_install_runtime_torch", unexpected)
    system_check.ensure_torchaudio()


def test_ensure_runtime_transformers_installs_when_missing(monkeypatch):
    monkeypatch.setattr(
        "pipeline.core.runtime_site.verify_transformers_ok",
        lambda: (False, "missing"),
    )
    monkeypatch.setattr(
        "pipeline.core.runtime_site.bootstrap_ai_runtime",
        lambda **_kw: None,
    )
    calls = []

    monkeypatch.setattr(
        "pipeline.core.system_check._runtime_pip_install",
        lambda *pkgs, **_kw: calls.append(pkgs),
    )

    with pytest.raises(RuntimeError, match="transformers"):
        system_check.ensure_runtime_transformers()
    assert calls == [
        ("transformers>=4.46.0", "huggingface-hub>=0.34", "safetensors"),
    ]


def test_ai_runtime_installs_when_transformers_missing(monkeypatch):
    monkeypatch.setattr(system_check, "_runtime_torch_needs_install", lambda: False)
    monkeypatch.setattr(system_check, "_torch_broken", lambda: False)
    monkeypatch.setattr(system_check, "_nvidia_present", lambda: False)
    monkeypatch.setattr(
        system_check,
        "_mod_ok",
        lambda name: (name != "transformers", "ok" if name != "transformers" else "chưa cài"),
    )
    calls = []

    def fake_pip_stream(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # _pip_stream mới là đường pip thật (stream log) — subprocess.run không dùng
    monkeypatch.setattr(system_check, "_pip_stream", fake_pip_stream)
    result = system_check.install_ai_runtime()

    assert result["ok"] is True
    assert any("transformers" in " ".join(map(str, c)) for c in calls)
