"""VisionProvider: Ollama VL if present, else transcript-grounded heuristic. No downloads."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pipeline.core.jobs import check_cancel
from pipeline.core.media import _ff_bin
from pipeline.review.llm import generate_json, pick_llm, list_ollama_models


def analyze_scenes(
    source: Path,
    scenes: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
    cache_dir: Path,
    *,
    job_id: str | None = None,
    use_vision: bool = True,
) -> list[dict[str, Any]]:
    frames_dir = cache_dir / "keyframes"
    frames_dir.mkdir(parents=True, exist_ok=True)
    vl_model = pick_llm(list_ollama_models(), prefer_vision=True) if use_vision else None
    use_vl = bool(use_vision and vl_model and any(k in vl_model.lower() for k in ("vl", "vision", "llava")))
    out: list[dict[str, Any]] = []
    for i, scene in enumerate(scenes):
        if job_id and i % 20 == 0:
            check_cancel(job_id)
        text = _scene_text(scene, transcript)
        frame = _keyframe(source, scene, frames_dir)
        if use_vl and frame is not None and i % 8 == 0:
            parsed = generate_json(
                "Describe this movie scene JSON with keys characters,location,objects,action,"
                f"emotion,description,visual_score,plot_score,emotion_score,spoiler_score. "
                f"Transcript: {text[:800]}",
                model=vl_model,
                timeout=90,
            )
            row = _normalize(scene, parsed, text)
        else:
            row = _heuristic(scene, text)
        row["keyframe"] = str(frame) if frame else ""
        out.append(row)
        if i > 0 and i % 400 == 0:
            _prune_frames(frames_dir, keep=400)
    return out


def _scene_text(scene: dict[str, Any], transcript: list[dict[str, Any]]) -> str:
    start, end = float(scene["start"]), float(scene["end"])
    bits = [t["text"] for t in transcript if t["end"] >= start and t["start"] <= end]
    return " ".join(bits)[:1200]


def _keyframe(source: Path, scene: dict[str, Any], dest: Path) -> Path | None:
    mid = (float(scene["start"]) + float(scene["end"])) / 2
    out = dest / f"{int(scene['scene_id']):05d}.jpg"
    if out.is_file():
        return out
    try:
        proc = subprocess.run(
            [
                _ff_bin("ffmpeg"), "-y", "-ss", f"{mid:.3f}", "-i", str(source),
                "-frames:v", "1", "-vf", "scale=480:-2", str(out),
            ],
            capture_output=True,
            timeout=30,
        )
        return out if proc.returncode == 0 and out.is_file() else None
    except (OSError, subprocess.SubprocessError):
        return None


def _prune_frames(folder: Path, keep: int) -> None:
    files = sorted(folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


def _heuristic(scene: dict[str, Any], text: str) -> dict[str, Any]:
    low = text.lower()
    action = "dialogue" if text else "visual"
    emotion = "tense" if any(w in low for w in ("kill", "chết", "fight", "đánh", "scream")) else "neutral"
    spoiler = 0.7 if any(w in low for w in ("twist", "kết", "chết", "reveal")) else 0.2
    plot = 0.65 if len(text) > 40 else 0.35
    visual = 0.55 if not text else 0.4
    return _normalize(
        scene,
        {
            "characters": [],
            "location": "",
            "objects": [],
            "action": action,
            "emotion": emotion,
            "description": text[:240] or f"Scene {scene['scene_id']}",
            "visual_score": visual,
            "plot_score": plot,
            "emotion_score": 0.6 if emotion != "neutral" else 0.3,
            "spoiler_score": spoiler,
        },
        text,
    )


def _normalize(scene: dict[str, Any], parsed: Any, text: str) -> dict[str, Any]:
    data = parsed if isinstance(parsed, dict) else {}
    def num(key: str, default: float) -> float:
        try:
            return max(0.0, min(1.0, float(data.get(key, default))))
        except (TypeError, ValueError):
            return default
    return {
        "scene_id": scene["scene_id"],
        "start": scene["start"],
        "end": scene["end"],
        "duration": scene["duration"],
        "characters": list(data.get("characters") or []),
        "location": str(data.get("location") or ""),
        "objects": list(data.get("objects") or []),
        "action": str(data.get("action") or ""),
        "emotion": str(data.get("emotion") or ""),
        "description": str(data.get("description") or text[:240]),
        "visual_score": num("visual_score", 0.4),
        "plot_score": num("plot_score", 0.4),
        "emotion_score": num("emotion_score", 0.3),
        "spoiler_score": num("spoiler_score", 0.2),
        "transcript": text[:800],
    }
