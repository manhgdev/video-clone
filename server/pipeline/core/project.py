"""Project layout, meta.json, cache keys, fingerprints."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from .config import DATA

T = TypeVar("T")

_meta_locks: dict[str, threading.RLock] = {}
_meta_locks_guard = threading.Lock()


def _meta_lock(project_id: str) -> threading.RLock:
    with _meta_locks_guard:
        lock = _meta_locks.get(project_id)
        if lock is None:
            lock = threading.RLock()
            _meta_locks[project_id] = lock
        return lock

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


def audio_cache_tag(preview_sec: int, match_duration: str) -> str:
    """Tag wav theo preview + speed bake — tránh reuse audio_full khi đổi preferVideo."""
    slow = "s080" if str(match_duration or "") == "preferVideo" else "s1"
    return f"{preview_tag(preview_sec)}_{slow}"


def resolve_project_video(meta: dict[str, Any], project_id: str) -> Path:
    """Clip đang làm việc: workVideo (ASR/OCR timeline) trước, rồi preview cache, rồi source."""
    work = str(meta.get("workVideo") or "")
    if work:
        wp = Path(work)
        if wp.is_file():
            return wp
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    if preview_sec > 0:
        cached = ensure_layout(project_id) / "cache" / f"preview_{preview_sec}.mp4"
        if cached.is_file():
            return cached
    return Path(meta["videoPath"])

def video_fingerprint(path: Path) -> str:
    """Full-file sha256 — head/tail 2MB collided when two clips share size + ends."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
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
    # o20: quét cả nhãn ngang ở 10–22% phía trên khung.
    # a15: Whisper siết biên theo words, KHÔNG tách 1 câu thành nhiều mảnh.
    ver = "o20" if engine in ("paddleocr", "screen") else "a15"
    # preferVideo bake 0.80× trước ASR → timeline khác bản 1×
    match = str(settings.get("matchDuration") or "")
    slow = "s080" if match == "preferVideo" else "s1"
    return f"{engine}|{src}|{source_fp}|p{prev}|{ver}|{slow}"


def trans_cache_key(settings: dict[str, Any]) -> str:
    # g4: free fallback Google→TikTok→MyMemory
    eng = str(settings.get("translator") or "google")
    return f"{eng}|{settings.get('targetLang', 'vi')}|g4"


def _read_meta_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # ponytail: file bị append/ghi đè một phần → lấy JSON object đầu
        obj, _end = json.JSONDecoder().raw_decode(raw.lstrip())
    if not isinstance(obj, dict):
        raise json.JSONDecodeError("meta root must be object", raw, 0)
    return obj


def _write_meta_file(path: Path, meta: dict[str, Any]) -> None:
    payload = json.dumps(meta, ensure_ascii=False, indent=2)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt >= 9:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def load_meta(project_id: str) -> dict[str, Any]:
    path = project_dir(project_id) / "meta.json"
    with _meta_lock(project_id):
        if not path.exists():
            return {}
        try:
            return _read_meta_file(path)
        except json.JSONDecodeError:
            # recovery: ghi lại bản sạch dưới cùng lock
            try:
                obj, _end = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8").lstrip())
            except json.JSONDecodeError:
                raise
            if isinstance(obj, dict):
                _write_meta_file(path, obj)
                return obj
            raise


def save_meta(project_id: str, meta: dict[str, Any]) -> None:
    path = project_dir(project_id) / "meta.json"
    with _meta_lock(project_id):
        _write_meta_file(path, meta)


def mutate_meta(project_id: str, fn: Callable[[dict[str, Any]], T]) -> T:
    """Read-modify-write atomic — tránh race khi nhiều PUT segment."""
    path = project_dir(project_id) / "meta.json"
    with _meta_lock(project_id):
        meta: dict[str, Any] = _read_meta_file(path) if path.exists() else {}
        out = fn(meta)
        _write_meta_file(path, meta)
        return out


def set_status(project_id: str, **kwargs: Any) -> None:
    def apply(meta: dict[str, Any]) -> None:
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

    mutate_meta(project_id, apply)
