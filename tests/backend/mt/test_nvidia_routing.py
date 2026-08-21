from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

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


def test_gemini_retries_transient_503(monkeypatch) -> None:
    statuses = iter([503, 503, 200])
    sleeps: list[float] = []

    class Response:
        def __init__(self, status: int):
            self.status_code = status
            self.headers = {}

        def raise_for_status(self):
            assert self.status_code < 400

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response(next(statuses))

    monkeypatch.setattr(cloud.httpx, "Client", Client)
    monkeypatch.setattr(cloud.time, "sleep", sleeps.append)

    result = cloud._gemini_generate(
        base_url="https://example.invalid/v1beta",
        api_key="key",
        model="gemini-test",
        prompt="test",
    )

    assert result == '{"ok":true}'
    assert sleeps == [2, 4]


def test_gemini_429_enters_shared_cooldown_and_continues(monkeypatch) -> None:
    statuses = iter([429, 200])
    sleeps: list[float] = []

    class Response:
        headers = {}

        def __init__(self, status: int):
            self.status_code = status

        def raise_for_status(self):
            assert self.status_code < 400

        def json(self):
            if self.status_code == 429:
                return {"error": {"details": [{"retryDelay": "7s"}]}}
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response(next(statuses))

    monkeypatch.setattr(cloud, "_gemini_next_request_at", 0)
    monkeypatch.setattr(cloud, "_gemini_rate_limited_until", 0)
    monkeypatch.setattr(cloud.httpx, "Client", Client)
    monkeypatch.setattr(cloud.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(cloud.time, "sleep", sleeps.append)

    result = cloud._gemini_generate(
        base_url="https://example.invalid/v1beta",
        api_key="key",
        model="gemini-test",
        prompt="test",
    )

    assert result == "ok"
    assert sleeps == [7.0]
    assert cloud._gemini_rate_limited_until == 400.0


class _FakeResponse:
    headers = {}

    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fake_client(post_fn):
    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *args, **kwargs):
            return post_fn(*args, **kwargs)

    return Client


class CloudKeyRotationTests(unittest.TestCase):
    def setUp(self):
        self.sleeps: list[float] = []
        self._patches = [
            patch.object(cloud.random.SystemRandom, "shuffle", lambda self, seq: None),
            patch.object(cloud, "_gemini_next_request_at", 0),
            patch.object(cloud, "_gemini_rate_limited_until", 0),
            patch.object(cloud.time, "sleep", self.sleeps.append),
            patch.object(cloud.time, "monotonic", lambda: 100.0),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)

    def _use_client(self, post_fn):
        p = patch.object(cloud.httpx, "Client", _fake_client(post_fn))
        p.start()
        self.addCleanup(p.stop)

    def test_gemini_rotates_to_next_key_on_429(self):
        used: list[str] = []
        statuses = iter([429, 200])

        def post(_url, params=None, **_kwargs):
            used.append((params or {})["key"])
            return _FakeResponse(next(statuses))

        self._use_client(post)
        result = cloud._gemini_generate(
            base_url="https://example.invalid/v1beta",
            api_keys=["key-a", "key-b"],
            model="gemini-test",
            prompt="test",
        )
        self.assertEqual(result, "ok")
        self.assertEqual(used, ["key-a", "key-b"])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(cloud._gemini_rate_limited_until, 0)

    def test_gemini_multi_key_429_waits_then_retries_oldest(self):
        used: list[str] = []
        # First pass: both keys 429. After pause, oldest (key-a) succeeds.
        statuses = iter([429, 429, 200])

        def post(_url, params=None, **_kwargs):
            used.append((params or {})["key"])
            return _FakeResponse(next(statuses))

        self._use_client(post)
        result = cloud._gemini_generate(
            base_url="https://example.invalid/v1beta",
            api_keys=["key-a", "key-b"],
            model="gemini-test",
            prompt="test",
        )
        self.assertEqual(result, "ok")
        self.assertEqual(used, ["key-a", "key-b", "key-a"])
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreaterEqual(self.sleeps[0], 3.0)
        self.assertLessEqual(self.sleeps[0], 8.0)
        self.assertEqual(cloud._gemini_rate_limited_until, 0)

    def test_gemini_multi_key_429_exhausts_rounds_then_raises(self):
        used: list[str] = []

        def post(_url, params=None, **_kwargs):
            used.append((params or {})["key"])
            return _FakeResponse(429)

        self._use_client(post)
        with self.assertRaises(RuntimeError):
            cloud._gemini_generate(
                base_url="https://example.invalid/v1beta",
                api_keys=["key-a", "key-b"],
                model="gemini-test",
                prompt="test",
            )
        # 3 pause-retries after the first full pass → 4 passes × 2 keys
        self.assertEqual(used, ["key-a", "key-b"] * 4)
        self.assertEqual(len(self.sleeps), 3)
        self.assertEqual(cloud._gemini_rate_limited_until, 0)

    def test_gemini_exhausts_keys_then_raises(self):
        used: list[str] = []

        def post(_url, params=None, **_kwargs):
            used.append((params or {})["key"])
            return _FakeResponse(401)

        self._use_client(post)
        with self.assertRaises(RuntimeError):
            cloud._gemini_generate(
                base_url="https://example.invalid/v1beta",
                api_keys=["key-a", "key-b"],
                model="gemini-test",
                prompt="test",
            )
        self.assertEqual(used, ["key-a", "key-b"])

    def test_openai_compatible_rotates_to_next_key_on_401(self):
        used: list[str] = []
        statuses = iter([401, 200])

        def post(_url, headers=None, **_kwargs):
            used.append((headers or {}).get("Authorization", ""))
            return _FakeResponse(
                next(statuses),
                {"choices": [{"message": {"content": "json-ok"}}]},
            )

        self._use_client(post)
        result = cloud._openai_compatible_chat(
            base_url="https://api.x.ai/v1",
            api_keys=["key-a", "key-b"],
            model="grok-test",
            prompt="test",
        )
        self.assertEqual(result, "json-ok")
        self.assertEqual(used, ["Bearer key-a", "Bearer key-b"])
        self.assertEqual(self.sleeps, [])