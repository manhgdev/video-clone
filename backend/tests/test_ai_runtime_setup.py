from pipeline.core import system_check


def test_ai_runtime_skips_install_when_ready(monkeypatch):
    monkeypatch.setattr(system_check, "_mod_ok", lambda _name: (True, "ok"))

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("installer must not run when every runtime module is ready")

    monkeypatch.setattr(system_check.subprocess, "run", unexpected_run)
    result = system_check.install_ai_runtime()

    assert result["ok"] is True
    assert "Whisper" in result["detail"]
    assert "VieNeu Local" in result["detail"]
