"""Ollama generate helper. Reuses the existing local/cloud Ollama port."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from pipeline.mt.ollama import _ollama_model
from pipeline.core.app_config import load_app_config, provider_api_keys
from pipeline.core.jobs import check_cancel, current_job_id
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


def generate_json(
    prompt: str,
    *,
    model: str | None = None,
    timeout: float = 180,
    job_id: str | None = None,
) -> Any:
    """Generate JSON and promptly abort a local Ollama request when cancelled.

    Ollama's non-streaming endpoint cannot observe cancellation until the model
    completes its whole response. Streaming lets the queue close the request at
    the next generated chunk instead.
    """
    active_job_id = job_id or current_job_id()
    check_cancel(active_job_id)
    chosen = model or pick_llm()
    if not chosen:
        return None
    text = ""
    if chosen.startswith("cloud:"):
        _, provider, configured_model = chosen.split(":", 2)
        try:
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
        except Exception as exc:
            try:
                from pipeline.core.app_log import append_log
                append_log(f"[llm] Cloud {provider} lỗi ({exc}), tự động chuyển sang local Ollama…")
            except Exception:
                pass
            chosen = pick_llm()

    check_cancel(active_job_id)
    if not text and chosen and not chosen.startswith("cloud:"):
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                with client.stream(
                    "POST",
                    "http://127.0.0.1:11434/api/generate",
                    json={
                        "model": chosen,
                        "prompt": prompt,
                        "stream": True,
                        "format": "json",
                        "keep_alive": "60m",  # Giữ model trên VRAM không bị unload/reload
                        "think": False,
                        "options": {
                            "num_predict": 1024,
                            "num_ctx": 2048,  # siêu gọn nhẹ = tốc độ sinh tức thì
                            "temperature": 0.5,
                            "top_p": 0.9,
                        },
                    },
                ) as res:
                    res.raise_for_status()
                    chunks: list[str] = []
                    for line in res.iter_lines():
                        check_cancel(active_job_id)
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunks.append(str(event.get("response") or ""))
                        if event.get("done"):
                            break
                    text = "".join(chunks)
        except Exception as e:
            # Cancellation is control flow; never silently turn it into a
            # fallback script after the user explicitly removed the project.
            check_cancel(active_job_id)
            try:
                from pipeline.core.app_log import append_log
                append_log(f"[llm] Ollama ({chosen}) lỗi: {e}")
            except Exception:
                pass
    check_cancel(active_job_id)
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
