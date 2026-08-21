"""Ollama generate helper. Reuses the existing local/cloud Ollama port."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from pipeline.mt.ollama import _ollama_model
from pipeline.core.app_config import load_app_config, provider_api_keys
from pipeline.mt.cloud import _gemini_generate, _openai_compatible_chat


def list_ollama_models() -> list[str]:
    try:
        with httpx.Client(timeout=8.0, trust_env=False) as client:
            tags = client.get("http://127.0.0.1:11434/api/tags")
            tags.raise_for_status()
            return [str(m.get("name") or "") for m in tags.json().get("models") or [] if m.get("name")]
    except Exception:
        return []


def pick_llm(models: list[str] | None = None, *, prefer_vision: bool = False) -> str | None:
    names = models if models is not None else list_ollama_models()
    if not names:
        return None
    if prefer_vision:
        vl = [n for n in names if any(k in n.lower() for k in ("vl", "vision", "llava", "minicpm-v"))]
        if vl:
            return vl[0]
    try:
        return _ollama_model(names, tier="balanced")
    except Exception:
        return names[0]


def generate_json(prompt: str, *, model: str | None = None, timeout: float = 180) -> Any:
    chosen = model or pick_llm()
    if not chosen:
        return None
    cloud_provider: str | None = None
    try:
        if chosen.startswith("cloud:"):
            _, provider, configured_model = chosen.split(":", 2)
            cloud_provider = provider
            cloud = load_app_config()["cloud"].get(provider) or {}
            keys = provider_api_keys(provider)
            if not keys:
                raise RuntimeError(f"REVIEW_CLOUD_KEY_REQUIRED:{provider}")
            actual_model = configured_model or str(cloud.get("reviewModel") or "")
            chat_kw = dict(
                base_url=str(cloud.get("reviewBaseUrl") or ""),
                api_keys=keys, model=actual_model, prompt=prompt, timeout=timeout,
            )
            if provider == "gemini":
                text = _gemini_generate(**chat_kw)
            else:
                text = _openai_compatible_chat(
                    **chat_kw,
                    max_output_tokens=2048,
                    system_msg="Return valid JSON only. Do not use markdown fences.",
                )
        else:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                res = client.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={
                        "model": chosen,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                        "think": False,
                        "options": {
                            "num_predict": 8192,
                            "num_ctx": 6144,  # smaller KV cache = faster tokens/sec
                            "temperature": 0.7,
                        },
                    },
                )
                res.raise_for_status()
                text = str((res.json() or {}).get("response") or "")
    except RuntimeError:
        raise
    except httpx.HTTPStatusError as exc:
        if cloud_provider:
            raise RuntimeError(
                f"REVIEW_CLOUD_REQUEST_FAILED:{cloud_provider}:{exc.response.status_code}"
            ) from None
        return None
    except Exception:
        if cloud_provider:
            raise RuntimeError(f"REVIEW_CLOUD_REQUEST_FAILED:{cloud_provider}") from None
        return None
    return parse_json(text)


def parse_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    start, end = raw.find("["), raw.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
