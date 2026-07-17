"""ElevenLabs TTS engine wrapper."""
from __future__ import annotations

from pathlib import Path

from ..eleven import _el_tts, _el_voice_id, _el_voice_options
from ..schemas import PREFIX_ELEVEN


def list_voices(lang: str | None = None) -> list[dict[str, str]]:
    _ = lang
    return _el_voice_options()


def parse(voice: str) -> str | None:
    return _el_voice_id(voice)


def synthesize(text: str, voice: str, out_wav: Path, lang: str = "vi") -> None:
    vid = parse(voice)
    if not vid:
        raise ValueError(f"Not ElevenLabs voice: {voice}")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _el_tts(text, vid, out_wav, lang=lang)
