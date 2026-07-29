from __future__ import annotations

import pytest

from pipeline.mt import api
from pipeline.mt.ollama import _ollama_model


def test_ollama_error_never_falls_back_to_google(monkeypatch: pytest.MonkeyPatch) -> None:
    google_called = False

    def fail_ollama(*_args, **_kwargs):
        raise RuntimeError("cloud quota exhausted")

    def fake_google(*_args, **_kwargs):
        nonlocal google_called
        google_called = True
        return ["sai đường"]

    monkeypatch.setattr(api, "translate_ollama", fail_ollama)
    monkeypatch.setattr(api, "translate_google_free", fake_google)

    with pytest.raises(RuntimeError, match="quota"):
        api.translate_segments(["hello"], "vi", translator="ollama")
    assert not google_called


def test_local_tiers_choose_installed_model_size() -> None:
    models = ["qwen:3b", "qwen:8b", "qwen:14b"]
    assert _ollama_model(models, tier="fast") == "qwen:3b"
    assert _ollama_model(models, tier="balanced") == "qwen:8b"
    assert _ollama_model(models, tier="quality") == "qwen:14b"
