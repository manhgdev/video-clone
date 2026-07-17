"""System TTS — macOS say / Linux espeak-ng."""
from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path


def list_voices(lang: str | None = None) -> list[dict[str, str]]:
    voices = [{"id": "system", "name": "Giọng hệ thống (theo ngôn ngữ đích)"}]
    if platform.system() != "Darwin":
        return voices
    try:
        raw = subprocess.check_output(["say", "-v", "?"], text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return voices
    prefer = (lang or "vi").split("-")[0].lower()
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        left = line.split("#", 1)[0].rstrip()
        m = re.search(r"\s([a-z]{2}_[A-Z]{2})\s*$", left)
        if not m:
            continue
        locale = m.group(1)
        say_id = left[: m.start()].strip().split(" (", 1)[0].strip()
        if not say_id or say_id in seen:
            continue
        seen.add(say_id)
        rows.append((say_id, locale, f"{say_id} ({locale})"))
    rows.sort(key=lambda x: (0 if x[1].lower().startswith(prefer) else 1, x[0]))
    for say_id, locale, label in rows:
        if locale.startswith(("vi", "en", "zh", "ja", "ko")):
            voices.append({"id": say_id, "name": f"macOS · {label}"})
    return voices


def synthesize(text: str, voice: str, out_wav: Path, lang: str = "vi") -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    resolved = voice if voice and voice != "system" else "Samantha"
    if platform.system() == "Darwin":
        tmp = out_wav.with_suffix(".aiff")
        subprocess.check_call(
            ["say", "-v", resolved, "-o", str(tmp), text or "."],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", str(tmp), str(out_wav)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        tmp.unlink(missing_ok=True)
        return
    v = "vi" if (lang or "").startswith("vi") else resolved
    subprocess.check_call(
        ["espeak-ng", "-v", v, "-w", str(out_wav), text or "."],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
