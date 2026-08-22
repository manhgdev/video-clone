"""Audio post-process for TTS (atempo / volume / pitch)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.media import ffprobe_duration


def trim_leading_silence(wav: Path) -> float:
    """Remove encoder/filter padding before a TTS utterance.

    ``atempo`` may add a small silence prefix even after a provider already
    normalized its response.  This runs as the final per-clip audio step so a
    timeline cue and its first spoken phoneme share the same start time.
    """
    if not wav.is_file():
        return 0.0
    trimmed = wav.with_name(wav.stem + "_trim.wav")
    try:
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-i", str(wav), "-map", "0:a:0",
                "-af", "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-45dB",
                "-acodec", "pcm_s16le", str(trimmed),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if trimmed.is_file() and trimmed.stat().st_size > 128:
            trimmed.replace(wav)
    finally:
        trimmed.unlink(missing_ok=True)
    return ffprobe_duration(wav)


def fit_duration(
    wav: Path,
    target_sec: float | None,
    match: str,
    *,
    force_refit: bool = False,
) -> float:
    """none: giữ nguyên. preferVideo/stretch: fit slot. natural ≤1.25×."""
    dur = ffprobe_duration(wav)
    if match == "none":
        return dur
    if not target_sec or target_sec <= 0.08 or dur <= 0.05:
        return dur
    if match == "stretch":
        fit_sec = target_sec
    else:
        if dur <= target_sec * 1.04 and not force_refit:
            return dur
        fit_sec = target_sec
    # Giảm tốc độ tối đa xuống 1.15x để giọng đọc luôn đều, không bị lúc nhanh lúc chậm
    max_speed = 1.15 if match == "natural" else 1.15
    fit_sec = max(fit_sec, dur / max_speed)
    ratio = dur / fit_sec
    if ratio > 1.02 or (match == "stretch" and abs(ratio - 1.0) > 0.03):
        stretched = wav.with_name(wav.stem + "_stretch.wav")
        filters: list[str] = []
        r = float(ratio)
        while r > 2.0 + 1e-9:
            filters.append("atempo=2.0")
            r /= 2.0
        while r < 0.5 - 1e-9:
            filters.append("atempo=0.5")
            r *= 2.0
        r = min(100.0, max(0.5, r))
        if abs(r - 1.0) >= 0.01:
            filters.append(f"atempo={r:.4f}")
        if not filters:
            return dur
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(wav),
                "-filter:a",
                ",".join(filters),
                str(stretched),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        stretched.replace(wav)
        dur = ffprobe_duration(wav)
    return dur


def apply_playback(
    wav: Path,
    *,
    speed: float = 1.0,
    volume: float = 1.0,
    pitch_semitones: float = 0.0,
) -> float:
    """Post-process speed/volume/pitch via ffmpeg. Returns new duration."""
    speed = max(0.5, min(2.0, float(speed or 1.0)))
    volume = max(0.0, min(2.0, float(volume or 1.0)))
    pitch = max(-12.0, min(12.0, float(pitch_semitones or 0.0)))
    if abs(speed - 1.0) < 0.02 and abs(volume - 1.0) < 0.02 and abs(pitch) < 0.1:
        return ffprobe_duration(wav)

    filters: list[str] = []
    # pitch via asetrate + atempo compensate
    if abs(pitch) >= 0.1:
        rate = 2 ** (pitch / 12.0)
        filters.append(f"asetrate=48000*{rate:.6f}")
        filters.append("aresample=48000")
        # undo tempo change from asetrate
        inv = 1.0 / rate
        r = inv
        while r > 2.0 + 1e-9:
            filters.append("atempo=2.0")
            r /= 2.0
        while r < 0.5 - 1e-9:
            filters.append("atempo=0.5")
            r *= 2.0
        r = min(100.0, max(0.5, r))
        if abs(r - 1.0) > 0.01:
            filters.append(f"atempo={r:.4f}")
    if abs(speed - 1.0) >= 0.02:
        r = float(speed)
        while r > 2.0 + 1e-9:
            filters.append("atempo=2.0")
            r /= 2.0
        while r < 0.5 - 1e-9:
            filters.append("atempo=0.5")
            r *= 2.0
        r = min(100.0, max(0.5, r))
        if abs(r - 1.0) >= 0.01:
            filters.append(f"atempo={r:.4f}")
    if abs(volume - 1.0) >= 0.02:
        filters.append(f"volume={volume:.4f}")

    out = wav.with_name(wav.stem + "_fx.wav")
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav),
            "-filter:a",
            ",".join(filters),
            str(out),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    out.replace(wav)
    return ffprobe_duration(wav)
