"""Cross-platform output names and batch input scanning."""
from __future__ import annotations

import re
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SKIP_DIR = {"cache", "review_cache", "out", "tts", "__pycache__", ".git", "node_modules"}


def sanitize_filename(name: str, *, fallback: str = "video") -> str:
    stem = _BAD.sub("_", (name or "").strip())
    stem = stem.rstrip(" .")
    if not stem or stem in {".", ".."}:
        stem = fallback
    return stem[:180]


def output_name(source: Path, template: str, settings: dict) -> str:
    name = sanitize_filename(source.stem)
    filled = (template or "{name}_{type}").format(
        name=name,
        type=str(settings.get("type") or "job"),
        style=str(settings.get("style") or "normal"),
        duration=str(settings.get("durationSec") or ""),
        stem=name,
    )
    out = sanitize_filename(filled)
    if not out.lower().endswith(".mp4"):
        out += ".mp4"
    return out


def scan_videos(paths: list[str], *, recursive: bool = True) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        raw = (raw or "").strip()
        if raw.startswith(("http://", "https://")):
            key = raw.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(raw)
            continue
        p = Path(raw).expanduser()
        if not p.exists():
            continue
        files: list[Path]
        if p.is_file():
            files = [p]
        else:
            files = [x for x in (p.rglob("*") if recursive else p.glob("*")) if x.is_file()]
        for item in files:
            if item.suffix.lower() not in VIDEO_EXTS:
                continue
            if any(part.lower() in _SKIP_DIR for part in item.parts):
                continue
            key = str(item.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(str(item.resolve()))
    return found
