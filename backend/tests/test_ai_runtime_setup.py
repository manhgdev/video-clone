from pipeline.core import system_check
from types import SimpleNamespace


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


def test_ai_runtime_installs_torch_when_missing(monkeypatch):
    monkeypatch.setattr(system_check, "_nvidia_present", lambda: True)
    monkeypatch.setattr(system_check, "_torch_cuda_ready", lambda: False)
    monkeypatch.setattr(
        system_check,
        "_mod_ok",
        lambda name: (name not in ("torch", "torchaudio"), "ok" if name not in ("torch", "torchaudio") else "chưa cài"),
    )
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(system_check.subprocess, "run", fake_run)
    monkeypatch.setattr(system_check, "_install_runtime_torch", lambda **_kw: calls.append(["install_runtime_torch"]))
    result = system_check.install_ai_runtime()

    assert result["ok"] is True
    assert any("install_runtime_torch" in str(call) for call in calls)


def test_ai_runtime_upgrades_cpu_torch_on_nvidia(monkeypatch):
    monkeypatch.setattr(system_check, "_nvidia_present", lambda: True)
    monkeypatch.setattr(system_check, "_torch_cuda_ready", lambda: False)
    monkeypatch.setattr(system_check, "_mod_ok", lambda _name: (True, "ok"))
    calls = []

    monkeypatch.setattr(system_check, "_install_runtime_torch", lambda **_kw: calls.append("upgrade"))
    monkeypatch.setattr(system_check, "_clear_torch_modules", lambda: None)
    result = system_check.install_ai_runtime()

    assert result["ok"] is True
    assert calls == ["upgrade"]
    assert "ONNX/CPU" not in result["detail"] or "cần" in result["detail"]


def test_ensure_runtime_torch_installs_cuda_on_nvidia(monkeypatch):
    monkeypatch.setattr(system_check, "_nvidia_present", lambda: True)
    monkeypatch.setattr(system_check, "_torch_cuda_ready", lambda: False)
    monkeypatch.setattr(system_check, "_mod_ok", lambda name: (name != "torchaudio", "ok"))
    calls = []

    monkeypatch.setattr(system_check, "_install_runtime_torch", lambda **_kw: calls.append("cuda"))
    monkeypatch.setattr(system_check, "_clear_torch_modules", lambda: None)
    system_check.ensure_runtime_torch()
    assert calls == ["cuda"]


def test_ensure_torchaudio_skips_when_ready(monkeypatch):
    monkeypatch.setattr(system_check, "_runtime_torch_needs_install", lambda: False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("must not pip install")

    monkeypatch.setattr(system_check, "_install_runtime_torch", unexpected)
    system_check.ensure_torchaudio()


def test_ensure_runtime_transformers_installs_when_missing(monkeypatch):
    monkeypatch.setattr(system_check, "_mod_ok", lambda name: (name != "transformers", "ok"))
    calls = []

    monkeypatch.setattr(system_check, "_runtime_pip_install", lambda *pkgs, **_kw: calls.append(pkgs))
    system_check.ensure_runtime_transformers()
    assert calls == [("transformers",)]


def test_ai_runtime_installs_when_transformers_missing(monkeypatch):
    monkeypatch.setattr(system_check, "_runtime_torch_needs_install", lambda: False)
    monkeypatch.setattr(
        system_check,
        "_mod_ok",
        lambda name: (name != "transformers", "ok" if name != "transformers" else "chưa cài"),
    )
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(system_check.subprocess, "run", fake_run)
    result = system_check.install_ai_runtime()

    assert result["ok"] is True
    assert calls
