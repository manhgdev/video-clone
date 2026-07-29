"""ElevenLabs TTS helpers."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

from ..core.config import EL_ADAM, _EL_PRESET
from .voice_store import normalize_voice_language

_el_key_i = 0
_el_voices_cache: list[dict[str, Any]] | None = None
_el_voices_cache_key_sig: str = ""

# khớp StudioVoiceAdamAI: Adam + eleven_v3; bump khi đổi cách gọi TTS
EL_MODEL = "eleven_v3"
EL_TTS_VER = "adam-v5-slot"
EL_VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": False,
}
_VI_CHARS = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.I,
)

def _el_http(**kwargs: Any) -> httpx.Client:
    # ponytail: trust_env=False — Cursor/sandbox HTTP_PROXY hay trả 403 giả; EL không cần proxy local
    kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)


def _el_keys() -> list[str]:
    try:
        from ..core.app_config import elevenlabs_api_keys

        keys = elevenlabs_api_keys()
        if keys:
            return keys
    except Exception:
        pass
    raw = os.environ.get("ELEVENLABS_API_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]



def _el_keys_sig(keys: list[str] | None = None) -> str:
    ks = keys if keys is not None else _el_keys()
    # chỉ fingerprint độ dài + suffix — không log full key
    return "|".join(f"{len(k)}:{k[-4:]}" for k in ks)


def clear_el_voices_cache() -> None:
    """Gọi sau khi lưu/đổi API key — cache rỗng cũ sẽ chặn list giọng."""
    global _el_voices_cache, _el_voices_cache_key_sig
    _el_voices_cache = None
    _el_voices_cache_key_sig = ""


def _el_voice_id(voice: str) -> str | None:
    """el:xxx hoặc id thuần ElevenLabs → voice_id; else None (dùng say)."""
    if not voice or voice == "system" or voice.startswith("cc:"):
        return None
    if voice.startswith("el:"):
        return voice[3:].strip() or None
    # ElevenLabs ids ~20 ký tự alnum; tránh nhầm tên say ngắn
    if re.fullmatch(r"[A-Za-z0-9]{16,}", voice):
        return voice
    return None


def _el_lang_code(lang: str | None, text: str = "") -> str:
    """Ép vi khi đích là VI hoặc chữ có dấu Việt — không để model đoán en."""
    code = (lang or "").strip().lower().split("-")[0]
    if code == "vi" or _VI_CHARS.search(text or ""):
        return "vi"
    if code and code != "auto" and len(code) == 2:
        return code
    return ""


def _el_tts(text: str, voice_id: str, out_wav: Path, lang: str | None = None) -> None:
    """POST ElevenLabs → mp3 → wav; luân phiên key khi 401/429. Khớp Studio Adam."""
    global _el_key_i
    keys = _el_keys()
    if not keys:
        raise RuntimeError(
            "Thiếu ElevenLabs API key. Mở Cấu hình → ElevenLabs, "
            "hoặc set ELEVENLABS_API_KEYS trong backend/.env"
        )
    mp3 = out_wav.with_suffix(".mp3")
    last = ""
    body: dict[str, Any] = {
        "text": text or ".",
        "model_id": EL_MODEL,
        "voice_settings": dict(EL_VOICE_SETTINGS),
        # seed cố định theo text → cùng câu không lúc Anh lúc Việt
        "seed": int(hashlib.sha1((text or ".").encode()).hexdigest()[:8], 16),
    }
    code = _el_lang_code(lang, text or "")
    if code:
        body["language_code"] = code
    with _el_http(timeout=120.0) as client:
        for _ in range(len(keys)):
            key = keys[_el_key_i % len(keys)]
            r = client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json=body,
            )
            # 403: key thiếu quyền / voice không dùng được — xoay key như 401
            if r.status_code in (401, 403, 429):
                last = f"{r.status_code} {r.text[:200]}"
                _el_key_i += 1
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"ElevenLabs TTS lỗi {r.status_code}: {r.text[:300]}")
            mp3.write_bytes(r.content)
            subprocess.check_call(
                ["ffmpeg", "-y", "-i", str(mp3), "-acodec", "pcm_s16le", str(out_wav)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            mp3.unlink(missing_ok=True)
            return
    raise RuntimeError(f"ElevenLabs hết key/quota/quyền: {last}")


def _el_voice_options() -> list[dict[str, Any]]:
    """Preset + API voices (cache process theo fingerprint key)."""
    global _el_voices_cache, _el_voices_cache_key_sig
    keys = _el_keys()
    sig = _el_keys_sig(keys)
    if _el_voices_cache is not None and _el_voices_cache_key_sig == sig:
        return _el_voices_cache
    # key đổi / chưa cache — luôn fetch lại
    _el_voices_cache = None
    _el_voices_cache_key_sig = sig

    preset = [
        {
            "id": f"el:{vid}",
            "name": f"ElevenLabs · {name}",
            "engine": "elevenlabs",
            "type": "cloud",
            "description": "Giọng đám mây ElevenLabs.",
        }
        for vid, name in _EL_PRESET
    ]
    if not keys:
        # không cache rỗng vĩnh viễn — lần sau có key sẽ fetch
        return []
    try:
        with _el_http(timeout=12.0) as client:
            r = client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": keys[0]},
            )
        if r.status_code < 400:
            fetched: list[dict[str, Any]] = []
            for v in r.json().get("voices", []):
                vid = v.get("voice_id") or ""
                name = v.get("name") or vid
                if vid:
                    labels = v.get("labels") if isinstance(v.get("labels"), dict) else {}
                    fetched.append(
                        {
                            "id": f"el:{vid}",
                            "name": f"ElevenLabs · {name}",
                            "engine": "elevenlabs",
                            "type": "cloud",
                            "description": v.get("description")
                            or labels.get("description")
                            or "Giọng đám mây ElevenLabs.",
                            "gender": labels.get("gender") or "",
                            "accent": labels.get("accent") or "",
                            "age": labels.get("age") or "",
                            "category": labels.get("use_case") or v.get("category") or "",
                            "language": normalize_voice_language(labels.get("language") or ""),
                        }
                    )
            if fetched:
                _el_voices_cache = fetched
                return fetched
        # 401/403: key chết / thiếu voices_read → preset vẫn dùng được cho TTS
        _el_voices_cache = preset
        return preset
    except (httpx.HTTPError, OSError):
        # mạng lỗi: vẫn trả preset (có key) để UI có Adam…
        _el_voices_cache = preset
        return preset


