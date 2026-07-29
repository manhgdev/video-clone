from __future__ import annotations

from pipeline.mt import api
from pipeline.mt import cloud
from pipeline.mt.cloud import _nvidia_riva_language_codes, _nvidia_riva_language_pair


def test_nvidia_routes_to_openai_compatible_cloud(monkeypatch) -> None:
    called: dict[str, str] = {}

    def fake_cloud(texts, target_lang, provider, **_kwargs):
        called["provider"] = provider
        return ["xin chao"] * len(texts)

    monkeypatch.setattr(api, "translate_cloud", fake_cloud)
    assert api.translate_segments(["hello"], "vi", translator="nvidia") == ["xin chao"]
    assert called["provider"] == "nvidia"


def test_nvidia_riva_uses_language_pair_prompt() -> None:
    assert _nvidia_riva_language_pair("zh", "vi", "xin chao") == "zh-cn-vi"
    assert _nvidia_riva_language_pair("auto", "vi", "中文") == "zh-cn-vi"
    assert _nvidia_riva_language_codes("zh", "vi", "xin chao") == ("zh-cn", "vi")


def test_nvidia_riva_uses_google_only_for_the_english_pivot(monkeypatch) -> None:
    monkeypatch.setattr(
        "pipeline.core.app_config.provider_credentials",
        lambda _provider: {"apiKey": "key", "baseUrl": "https://integrate.api.nvidia.com/v1", "model": "nvidia/riva-translate-4b-instruct-v2"},
    )
    google_calls = []
    riva_calls = []
    monkeypatch.setattr(
        cloud,
        "translate_google_free",
        lambda texts, target, source, **_kwargs: google_calls.append((texts, target, source)) or ["clean phone"],
    )
    monkeypatch.setattr(
        cloud,
        "_openai_compatible_chat",
        lambda **kwargs: riva_calls.append((kwargs["prompt"], kwargs["system_msg"])) or "dien thoai sach",
    )
    assert cloud.translate_cloud(["中文"], "vi", "nvidia", source_lang="zh", workers=1) == ["dien thoai sach"]
    assert google_calls == [(["中文"], "en", "zh-cn")]
    assert riva_calls == [("clean phone", "en-vi")]
