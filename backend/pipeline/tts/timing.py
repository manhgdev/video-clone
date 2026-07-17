"""Fit TTS audio duration to SRT / segment slots."""
from __future__ import annotations

from pathlib import Path

from .audio_utils import fit_duration


def fit_to_slot(
    wav: Path,
    target_sec: float | None,
    match: str,
    *,
    force_refit: bool = False,
) -> float:
    return fit_duration(wav, target_sec, match, force_refit=force_refit)
