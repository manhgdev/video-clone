"""TTS shared types / constants."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VoiceOption:
    id: str
    name: str
    engine: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


PREFIX_CAPCUT = "cc:"
PREFIX_ELEVEN = "el:"
PREFIX_VIENEU = "vn:"
PREFIX_SYSTEM = "system"

VIENEU_TTS_VER = "vn-v3turbo-1"
