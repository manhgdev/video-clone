"""TTS: CapCut + ElevenLabs + macOS say / espeak."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

from ..core.config import EL_ADAM
from ..core.media import ffprobe_duration
from . import capcut as capcut_client
from .eleven import (
    EL_MODEL,
    EL_TTS_VER,
    _el_keys,
    _el_lang_code,
    _el_tts,
    _el_voice_id,
    _el_voice_options,
)

_cc_voices_cache: list[dict[str, Any]] | None = None
CC_TTS_VER = "cc3-slot"
_VOICES_JSON = Path(__file__).resolve().parent / "voices_capcut.json"

def _cc_parse(voice: str) -> tuple[str, str] | None:
    """cc:voice_type:resource_id → (voice_type, resource_id)."""
    if not voice or not voice.startswith("cc:"):
        return None
    rest = voice[3:]
    voice_type, sep, resource_id = rest.rpartition(":")
    if not sep or not voice_type or not resource_id:
        return None
    return voice_type, resource_id


def _capcut_tts(text: str, voice_type: str, resource_id: str, out_wav: Path) -> None:
    mp3 = out_wav.with_suffix(".mp3")
    capcut_client.synthesize_mp3(text or ".", voice_type, resource_id, mp3)
    subprocess.check_call(
        ["ffmpeg", "-y", "-i", str(mp3), "-acodec", "pcm_s16le", str(out_wav)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    mp3.unlink(missing_ok=True)


def _load_capcut_voices() -> list[dict[str, Any]]:
    global _cc_voices_cache
    if _cc_voices_cache is not None:
        return _cc_voices_cache
    if not _VOICES_JSON.is_file():
        _cc_voices_cache = []
        return []
    _cc_voices_cache = json.loads(_VOICES_JSON.read_text(encoding="utf-8"))
    return _cc_voices_cache


def _cc_voice_options(lang: str | None = None) -> list[dict[str, str]]:
    prefer = (lang or "vi").split("-")[0].lower()
    # jp in Voice.json aliases ja
    aliases = {prefer}
    if prefer == "ja":
        aliases.add("jp")
    if prefer == "jp":
        aliases.add("ja")
    out: list[dict[str, str]] = []
    for v in _load_capcut_voices():
        lan = (v.get("lan") or "").lower()
        if aliases and lan not in aliases:
            continue
        vt = v.get("voice_type") or ""
        rid = str(v.get("resource_id") or "")
        name = v.get("display_name") or vt
        if not vt or not rid:
            continue
        out.append({"id": f"cc:{vt}:{rid}", "name": f"CapCut · {name}"})
    return out


def _parse_say_voices() -> list[tuple[str, str, str]]:
    """Return [(say_id, locale, label), ...] from `say -v ?`."""
    if platform.system() != "Darwin":
        return []
    try:
        raw = subprocess.check_output(["say", "-v", "?"], text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        left = line.split("#", 1)[0].rstrip()
        m = re.search(r"\s([a-z]{2}_[A-Z]{2})\s*$", left)
        if not m:
            continue
        locale = m.group(1)
        raw_name = left[: m.start()].strip()
        # `say -v` wants "Linh", not "Linh (Vietnamese (Vietnam))"
        say_id = raw_name.split(" (", 1)[0].strip()
        if not say_id or say_id in seen:
            continue
        seen.add(say_id)
        out.append((say_id, locale, f"{say_id} ({locale})"))
    return out


def list_voices(lang: str | None = None) -> list[dict[str, str]]:
    voices: list[dict[str, str]] = []
    voices.extend(_cc_voice_options(lang))
    voices.extend(_el_voice_options())

    voices.append({"id": "system", "name": "Giọng hệ thống (theo ngôn ngữ đích)"})
    parsed = _parse_say_voices()
    prefer = (lang or "vi").split("-")[0].lower()
    ordered = sorted(
        parsed,
        key=lambda x: (0 if x[1].lower().startswith(prefer) else 1, x[0]),
    )
    for say_id, locale, label in ordered:
        if locale.startswith(("vi", "en", "zh", "ja", "ko")):
            voices.append({"id": say_id, "name": f"macOS · {label}"})
    return voices[:160]


def resolve_voice(voice: str, lang: str = "vi") -> str:
    """Map 'system' → giọng say đúng ngôn ngữ (vd. Linh cho vi)."""
    if _cc_parse(voice):
        return voice
    el = _el_voice_id(voice)
    if el:
        return f"el:{el}"
    if voice and voice != "system":
        return voice.split(" (", 1)[0].strip()
    prefer = (lang or "vi").split("-")[0].lower()
    # có ElevenLabs → mặc định Adam
    if _el_keys():
        return f"el:{EL_ADAM}"
    parsed = _parse_say_voices()
    for say_id, locale, _ in parsed:
        if locale.lower().startswith(prefer):
            return say_id
    for fallback in ("Linh", "Samantha", "Alex"):
        if any(s[0] == fallback for s in parsed):
            return fallback
    return voice if voice and voice != "system" else "Samantha"

def tts_cache_key(text: str, voice: str, lang: str, match: str) -> str:
    code = _el_lang_code(lang, text)
    ver = CC_TTS_VER if voice.startswith("cc:") else EL_TTS_VER
    model = "capcut" if voice.startswith("cc:") else EL_MODEL
    raw = f"{text.strip()}|{voice}|{lang}|{match}|{model}|{code}|{ver}".encode()
    return hashlib.sha1(raw).hexdigest()[:20]

def tts_segment(
    text: str,
    voice: str,
    out_wav: Path,
    target_sec: float | None,
    match: str,
    lang: str = "vi",
    *,
    force_refit: bool = False,
) -> float:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    resolved = resolve_voice(voice, lang)
    has_file = out_wav.exists() and out_wav.stat().st_size > 128
    # force_refit: chỉ atempo file sẵn có (không gọi API lại)
    if not has_file:
        if force_refit:
            force_refit = False  # không có file → synth mới
        cc = _cc_parse(resolved)
        el = _el_voice_id(resolved)
        if cc:
            _capcut_tts(text, cc[0], cc[1], out_wav)
        elif el:
            _el_tts(text, el, out_wav, lang=lang)
        elif platform.system() == "Darwin":
            tmp = out_wav.with_suffix(".aiff")
            cmd = ["say", "-v", resolved, "-o", str(tmp), text or "."]
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # ponytail: afconvert more reliable than ffmpeg on macOS aiff
            subprocess.check_call(
                ["afconvert", "-f", "WAVE", "-d", "LEI16", str(tmp), str(out_wav)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            tmp.unlink(missing_ok=True)
        else:
            # ponytail: espeak-ng offline fallback; Piper when quality matters
            v = "vi" if lang.startswith("vi") else resolved
            subprocess.check_call(
                ["espeak-ng", "-v", v, "-w", str(out_wav), text or "."],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    dur = ffprobe_duration(out_wav)
    # Fit slot bằng atempo (đọc hết, nhanh hơn) — không cắt chữ.
    # natural/stretch: luôn cố fit vào target; max 2× (chuỗi atempo).
    if match != "none" and target_sec and target_sec > 0.08 and dur > 0.05:
        if match == "stretch":
            fit_sec = target_sec
        else:
            if dur <= target_sec * 1.04 and not force_refit:
                return dur
            fit_sec = target_sec
        # Cho phép tăng tốc tới 2.0× để nhét hết câu vào slot
        max_speed = 2.0
        fit_sec = max(fit_sec, dur / max_speed)
        ratio = dur / fit_sec
        if ratio > 1.02 or (match == "stretch" and abs(ratio - 1.0) > 0.03):
            stretched = out_wav.with_name(out_wav.stem + "_stretch.wav")
            filters = []
            r = ratio
            while r > 2.0:
                filters.append("atempo=2.0")
                r /= 2.0
            while r < 0.5:
                filters.append("atempo=0.5")
                r /= 0.5
            filters.append(f"atempo={r:.4f}")
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(out_wav),
                    "-filter:a",
                    ",".join(filters),
                    str(stretched),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stretched.replace(out_wav)
            dur = ffprobe_duration(out_wav)
    return dur

