"""TTS engine protocol."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TtsEngine(Protocol):
    def list_voices(self, lang: str | None = None) -> list[dict[str, str]]: ...

    def synthesize(self, text: str, voice: str, out_wav: Path, **kwargs) -> None: ...
