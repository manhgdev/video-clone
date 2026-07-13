"""Project layout, meta.json, cache keys, fingerprints."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import DATA

def project_dir(project_id: str) -> Path:
    p = DATA / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_layout(project_id: str) -> Path:
    """
    data/<id>/
      source.*  meta.json
      cache/audio.wav  cache/frames/  cache/asr.json
      tts/<hash>.wav
      out/burned.mp4  out/final.mp4
    """
    root = project_dir(project_id)
    (root / "cache").mkdir(exist_ok=True)
    (root / "tts").mkdir(exist_ok=True)
    (root / "out").mkdir(exist_ok=True)
    # migrate flat leftovers once
    moves = [
        (root / "audio.wav", root / "cache" / "audio_full.wav"),
        (root / "cache" / "audio.wav", root / "cache" / "audio_full.wav"),
        (root / "burned.mp4", root / "out" / "burned.mp4"),
        (root / "output.mp4", root / "out" / "final.mp4"),
    ]
    for src, dst in moves:
        if src.exists() and not dst.exists():
            src.replace(dst)
    for old_name, new_name in (("frames", "frames_full"),):
        frames_old = root / old_name
        frames_mid = root / "cache" / old_name
        frames_new = root / "cache" / new_name
        if frames_old.is_dir() and not frames_new.exists():
            frames_old.rename(frames_new)
        elif frames_mid.is_dir() and not frames_new.exists():
            frames_mid.rename(frames_new)
    return root


def cache_audio(project_id: str, tag: str = "full") -> Path:
    return ensure_layout(project_id) / "cache" / f"audio_{tag}.wav"


def cache_frames(project_id: str, tag: str = "full") -> Path:
    return ensure_layout(project_id) / "cache" / f"frames_{tag}"


def cache_asr_path(project_id: str) -> Path:
    return ensure_layout(project_id) / "cache" / "asr.json"


def out_burned(project_id: str) -> Path:
    return ensure_layout(project_id) / "out" / "burned.mp4"


def out_final(project_id: str) -> Path:
    return ensure_layout(project_id) / "out" / "final.mp4"


def preview_tag(preview_sec: int) -> str:
    return f"p{int(preview_sec)}" if preview_sec > 0 else "full"

def video_fingerprint(path: Path) -> str:
    """ponytail: size + head/tail 2MB; full hash if collisions show up."""
    st = path.stat()
    h = hashlib.sha256()
    h.update(str(st.st_size).encode())
    with path.open("rb") as f:
        h.update(f.read(2 << 20))
        if st.st_size > 4 << 20:
            f.seek(-2 << 20, 2)
            h.update(f.read())
    return h.hexdigest()[:20]


def find_project_by_fp(fp: str) -> str | None:
    for p in DATA.iterdir():
        if not p.is_dir() or p.name.startswith("_") or p.name.startswith("."):
            continue
        meta_path = p / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("sourceFp") == fp and Path(meta.get("videoPath") or "").exists():
            return p.name
    return None


def inherit_voice(seg_voice: str | None, default: str) -> str:
    v = (seg_voice or "").strip()
    if not v or v == "system":
        return default or "system"
    return v


def asr_cache_key(settings: dict[str, Any], source_fp: str) -> str:
    engine = settings.get("engine", "paddleocr")
    src = settings.get("sourceLang", "auto")
    prev = int(settings.get("previewSec") or 0)
    # o16: nhãn giữa khung (mid graphic) + multi-line join
    ver = "o16" if engine in ("paddleocr", "screen") else "a1"
    return f"{engine}|{src}|{source_fp}|p{prev}|{ver}"


def trans_cache_key(settings: dict[str, Any]) -> str:
    # g4: free fallback Google→TikTok→MyMemory
    eng = str(settings.get("translator") or "google")
    return f"{eng}|{settings.get('targetLang', 'vi')}|g4"


def load_meta(project_id: str) -> dict[str, Any]:
    path = project_dir(project_id) / "meta.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(project_id: str, meta: dict[str, Any]) -> None:
    (project_dir(project_id) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def set_status(project_id: str, **kwargs: Any) -> None:
    meta = load_meta(project_id)
    status = meta.get("status") or {
        "step": "video",
        "progress": 0,
        "message": "",
        "running": False,
    }
    status.update(kwargs)
    if "error" in kwargs and kwargs["error"] is None:
        status.pop("error", None)
    meta["status"] = status
    save_meta(project_id, meta)

