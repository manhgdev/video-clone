"""App-level cloud API config (translate + TTS keys)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import DATA

_CONFIG_PATH = DATA / "app_config.json"

# provider → (env_key, default_base, default_model)
PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "env": "OPENAI_API_KEY",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "label": "OpenAI",
    },
    "gemini": {
        "env": "GEMINI_API_KEY",
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
        "label": "Gemini",
    },
    "deepseek": {
        "env": "DEEPSEEK_API_KEY",
        "base": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "label": "DeepSeek",
    },
    "openrouter": {
        "env": "OPENROUTER_API_KEY",
        "base": "https://openrouter.ai/api/v1",
        "model": "google/gemini-2.0-flash-001",
        "label": "OpenRouter",
    },
    "grok": {
        "env": "XAI_API_KEY",
        "base": "https://api.x.ai/v1",
        "model": "grok-3-mini",
        "label": "Grok",
    },
}


def _default_cloud() -> dict[str, dict[str, str]]:
    return {
        pid: {"apiKey": "", "baseUrl": meta["base"], "model": meta["model"]}
        for pid, meta in PROVIDERS.items()
    }


def _default_tts() -> dict[str, Any]:
    return {
        "elevenlabs": {
            "apiKeys": "",  # comma-separated, same as ELEVENLABS_API_KEYS
            "label": "ElevenLabs",
            "env": "ELEVENLABS_API_KEYS",
        }
    }


def _mask_key(key: str) -> str:
    if not key:
        return ""
    return ("•" * max(0, min(12, len(key) - 4))) + key[-4:]


def load_app_config() -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if _CONFIG_PATH.exists():
        try:
            raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
    cloud = _default_cloud()
    saved = raw.get("cloud") if isinstance(raw.get("cloud"), dict) else {}
    for pid, meta in PROVIDERS.items():
        block = saved.get(pid) if isinstance(saved.get(pid), dict) else {}
        env_key = (os.environ.get(meta["env"]) or "").strip()
        file_key = str(block.get("apiKey") or "").strip()
        cloud[pid] = {
            "apiKey": file_key or env_key,
            "baseUrl": str(block.get("baseUrl") or meta["base"]).strip() or meta["base"],
            "model": str(block.get("model") or meta["model"]).strip() or meta["model"],
        }

    tts = _default_tts()
    saved_tts = raw.get("tts") if isinstance(raw.get("tts"), dict) else {}
    el_block = saved_tts.get("elevenlabs") if isinstance(saved_tts.get("elevenlabs"), dict) else {}
    file_keys = str(el_block.get("apiKeys") or "").strip()
    env_keys = (os.environ.get("ELEVENLABS_API_KEYS") or "").strip()
    tts["elevenlabs"]["apiKeys"] = file_keys or env_keys

    return {"cloud": cloud, "tts": tts}


def save_app_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge patch into app_config.json. Empty apiKey keeps previous."""
    cur = load_app_config()
    cloud_in = patch.get("cloud") if isinstance(patch.get("cloud"), dict) else {}
    for pid in PROVIDERS:
        if pid not in cloud_in or not isinstance(cloud_in[pid], dict):
            continue
        b = cloud_in[pid]
        prev = cur["cloud"][pid]
        key = str(b.get("apiKey") if "apiKey" in b else prev["apiKey"]).strip()
        # UI may send masked "••••xx" — ignore
        if key.startswith("•") or key == "(đã lưu)":
            key = prev["apiKey"]
        base = str(b.get("baseUrl") or prev["baseUrl"]).strip() or prev["baseUrl"]
        model = str(b.get("model") or prev["model"]).strip() or prev["model"]
        cur["cloud"][pid] = {"apiKey": key, "baseUrl": base, "model": model}

    tts_in = patch.get("tts") if isinstance(patch.get("tts"), dict) else {}
    el_in = tts_in.get("elevenlabs") if isinstance(tts_in.get("elevenlabs"), dict) else None
    if el_in is not None:
        prev_keys = str(cur["tts"]["elevenlabs"].get("apiKeys") or "")
        keys = str(el_in.get("apiKeys") if "apiKeys" in el_in else prev_keys).strip()
        if keys.startswith("•") or keys == "(đã lưu)":
            keys = prev_keys
        cur["tts"]["elevenlabs"]["apiKeys"] = keys

    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    to_write = {
        "cloud": {
            pid: {
                "apiKey": cur["cloud"][pid]["apiKey"],
                "baseUrl": cur["cloud"][pid]["baseUrl"],
                "model": cur["cloud"][pid]["model"],
            }
            for pid in PROVIDERS
        },
        "tts": {
            "elevenlabs": {
                "apiKeys": cur["tts"]["elevenlabs"]["apiKeys"],
            }
        },
    }
    _CONFIG_PATH.write_text(
        json.dumps(to_write, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return cur


def public_app_config() -> dict[str, Any]:
    """Keys masked for UI."""
    cfg = load_app_config()
    out_cloud: dict[str, Any] = {}
    for pid, meta in PROVIDERS.items():
        b = cfg["cloud"][pid]
        key = b["apiKey"]
        out_cloud[pid] = {
            "apiKey": _mask_key(key),
            "apiKeySet": bool(key),
            "baseUrl": b["baseUrl"],
            "model": b["model"],
            "label": meta["label"],
            "env": meta["env"],
        }

    el_keys_raw = str(cfg["tts"]["elevenlabs"].get("apiKeys") or "")
    parts = [k.strip() for k in el_keys_raw.split(",") if k.strip()]
    masked_parts = [_mask_key(k) for k in parts]
    return {
        "cloud": out_cloud,
        "tts": {
            "elevenlabs": {
                "apiKeys": ", ".join(masked_parts),
                "apiKeySet": bool(parts),
                "keyCount": len(parts),
                "label": "ElevenLabs",
                "env": "ELEVENLABS_API_KEYS",
            }
        },
    }


def provider_credentials(provider: str) -> dict[str, str]:
    pid = (provider or "").lower().strip()
    if pid not in PROVIDERS:
        raise RuntimeError(f"Provider không hỗ trợ: {provider}")
    b = load_app_config()["cloud"][pid]
    if not b["apiKey"]:
        env = PROVIDERS[pid]["env"]
        raise RuntimeError(
            f"Chưa có API key {PROVIDERS[pid]['label']}. "
            f"Mở Cấu hình → API dịch cloud, hoặc set {env} trong server/.env"
        )
    return dict(b)


def elevenlabs_api_keys() -> list[str]:
    """Keys from app_config.json first, else ELEVENLABS_API_KEYS env."""
    cfg = load_app_config()
    raw = str(cfg.get("tts", {}).get("elevenlabs", {}).get("apiKeys") or "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if keys:
        return keys
    env = (os.environ.get("ELEVENLABS_API_KEYS") or "").strip()
    return [k.strip() for k in env.split(",") if k.strip()]
