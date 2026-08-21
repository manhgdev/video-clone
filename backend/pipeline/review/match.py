"""Scene matcher + pacing. Voice duration from TTS is the master clock."""
from __future__ import annotations

from typing import Any

PACE = {
    "tiktok": (1.0, 3.0),
    "normal": (2.0, 5.0),
    "recap": (2.0, 5.0),
    "cinematic": (3.0, 7.0),
    "humorous": (1.5, 4.0),
    "deep": (3.0, 6.0),
}

BUILD_MODES = ("fixed", "stretch", "accumulate", "smart")
PAUSE = {"fast": 0.08, "balanced": 0.32, "slow": 0.65}


def resolve_build_mode(settings: dict[str, Any] | None) -> str:
    raw = dict(settings or {})
    mode = str(raw.get("buildMode") or "").strip()
    if mode in BUILD_MODES:
        return mode
    cut = str(raw.get("cutMode") or "").strip()
    if cut in BUILD_MODES:
        return cut
    return "accumulate"


def tokenize(text: str) -> set[str]:
    return {w.lower() for w in (text or "").replace(".", " ").split() if len(w) > 2}


def score_scene(
    voice: dict[str, Any],
    scene: dict[str, Any],
    *,
    used: dict[int, int],
    spoiler: str,
    last_id: int | None,
) -> float:
    sid = int(scene["scene_id"])
    if spoiler == "none" and float(scene.get("spoiler_score") or 0) > 0.55:
        return -1.0
    preferred = {int(value) for value in voice.get("preferred_scene_ids") or []}
    member_ids = {
        int(value)
        for value in (scene.get("member_scene_ids") or [sid])
    }
    text = " ".join([
        str(voice.get("text") or ""),
        str(voice.get("visual_intent") or ""),
        " ".join(str(x) for x in voice.get("character_refs") or []),
    ])
    scene_text = " ".join([
        str(scene.get("description") or ""),
        str(scene.get("transcript") or ""),
        str(scene.get("action") or ""),
        " ".join(str(x) for x in scene.get("characters") or []),
    ])
    overlap = len(tokenize(text) & tokenize(scene_text))
    score = 0.15 * overlap
    score += 0.25 * float(scene.get("plot_score") or 0)
    score += 0.2 * float(scene.get("visual_score") or 0)
    score += 0.15 * float(scene.get("emotion_score") or 0)
    if preferred & member_ids:
        score += 0.9
    chars = {str(c).lower() for c in (scene.get("characters") or [])}
    if chars and any(str(c).lower() in chars for c in (voice.get("character_refs") or [])):
        score += 0.5
    score -= 0.35 * used.get(sid, 0)
    if last_id is not None and sid == last_id:
        score -= 0.8
    if last_id is not None and abs(sid - last_id) <= 1:
        score -= 0.15
    if voice.get("purpose") == "hook":
        score += 0.1 * float(scene.get("visual_score") or 0)
    return score


def keep_skip_windows(
    visuals: list[dict[str, Any]],
    keep_sec: float,
    skip_sec: float,
) -> list[dict[str, Any]]:
    if not visuals:
        return []
    duration = max(float(s.get("end") or 0) for s in visuals)
    keep = max(0.4, float(keep_sec or 4))
    skip = max(0.0, float(skip_sec or 10))
    out: list[dict[str, Any]] = []
    t = 0.0
    idx = 0
    while t < duration - 0.2:
        end = min(duration, t + keep)
        overlap = [s for s in visuals if float(s.get("end") or 0) > t and float(s.get("start") or 0) < end]
        base = max(overlap, key=lambda s: float(s.get("plot_score") or 0), default=None)
        item = dict(base) if base else {"plot_score": 0, "visual_score": 0, "emotion_score": 0, "spoiler_score": 0}
        item.update({
            "scene_id": idx,
            "member_scene_ids": [
                int(scene["scene_id"]) for scene in overlap
            ],
            "start": round(t, 3),
            "end": round(end, 3),
            "duration": round(end - t, 3),
        })
        out.append(item)
        idx += 1
        t += keep + skip
    return out or visuals


def accumulate_windows(
    visuals: list[dict[str, Any]], *, min_duration: float = 6.0
) -> list[dict[str, Any]]:
    """Coalesce adjacent source cuts so Review does not encode every micro-scene."""
    if not visuals:
        return []
    target = max(1.0, float(min_duration))
    ordered = sorted(visuals, key=lambda scene: float(scene.get("start") or 0))
    out: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    start = 0.0
    end = 0.0

    def flush() -> None:
        if not group:
            return
        base = max(group, key=lambda scene: float(scene.get("plot_score") or 0))
        out.append({
            **base,
            "scene_id": len(out),
            "member_scene_ids": [
                int(scene["scene_id"]) for scene in group
            ],
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.1, end - start), 3),
        })

    for scene in ordered:
        scene_start = float(scene.get("start") or 0)
        scene_end = max(scene_start, float(scene.get("end") or scene_start))
        if not group:
            group, start, end = [scene], scene_start, scene_end
            continue
        # A material source gap starts a separate window instead of showing a
        # jump inside one clip.
        if scene_start > end + 0.5 or end - start >= target:
            flush()
            group, start, end = [scene], scene_start, scene_end
            continue
        group.append(scene)
        end = max(end, scene_end)
    flush()
    return out


def match_voice(
    voices: list[dict[str, Any]],
    visuals: list[dict[str, Any]],
    *,
    style: str,
    spoiler: str,
    mode: str = "fixed",
    keep_sec: float = 4,
    skip_sec: float = 10,
    pause_pace: str = "balanced",
) -> dict[str, Any]:
    lo, hi = PACE.get(style, PACE["normal"])
    if mode == "smart":
        pool = keep_skip_windows(visuals, keep_sec, skip_sec)
    elif mode == "accumulate":
        pool = accumulate_windows(visuals)
    else:
        pool = list(visuals)
    sequential = mode in {"accumulate", "smart"}
    stretch = mode == "stretch"
    pause = PAUSE.get(pause_pace, 0.32) if stretch else 0.0
    used: dict[int, int] = {}
    last: int | None = None
    cursor = 0
    ordered = sorted(pool, key=lambda s: float(s.get("start") or 0))
    segments: list[dict[str, Any]] = []
    t = 0.0
    for vi, voice in enumerate(voices):
        dur = max(0.4, float(voice.get("duration") or 3.0))
        cover = dur + (pause if vi < len(voices) - 1 else 0.0)
        clips: list[dict[str, Any]] = []
        remain = cover
        while remain > 0.05:
            if sequential:
                scene = _next_sequential(ordered, cursor, voice, used, spoiler, last)
                if scene is not None:
                    cursor = ordered.index(scene) + 1
            else:
                scene = _best_scene(pool, voice, used, spoiler, last)
            if scene is None:
                # Sequential Review consumes the source in order. Reusing a
                # clip is only a last resort when the source is shorter than
                # the selected review duration.
                scene = ordered[-1] if sequential and ordered else (pool[0] if pool else None)
            if scene is None:
                break
            span = float(scene.get("duration") or remain)
            if stretch:
                take = min(span, max(remain, span * 0.6))
                start = float(scene["start"])
                clips.append({
                    "scene_id": scene["scene_id"],
                    "source_start": round(start, 3),
                    "source_end": round(start + min(span, take), 3),
                    "target_duration": round(remain, 3),
                })
                remain = 0.0
            else:
                target = min(hi, max(lo, remain if remain < hi + 0.4 else lo + (hi - lo) * 0.5))
                take = min(target, remain, span)
                start = float(scene["start"])
                offset = max(0.0, (span - take) / 3) if not sequential else 0.0
                clips.append({
                    "scene_id": scene["scene_id"],
                    "source_start": round(start + offset, 3),
                    "source_end": round(start + offset + take, 3),
                })
                remain -= take
            used[int(scene["scene_id"])] = used.get(int(scene["scene_id"]), 0) + 1
            last = int(scene["scene_id"])
        segments.append({
            "voice_id": voice["id"],
            "voice_start": round(t, 3),
            "voice_end": round(t + dur, 3),
            "text": voice.get("text") or "",
            "audio": voice.get("audio") or "",
            "audio_duration": float(voice.get("audio_duration") or dur),
            "tts_speed": float(voice.get("ttsSpeed") or 1.0),
            "clips": clips,
        })
        t += cover
    return {"type": "review", "duration": round(t, 3), "mode": mode, "segments": segments}


def _best_scene(pool, voice, used, spoiler, last):
    ranked = sorted(
        pool,
        key=lambda s: score_scene(voice, s, used=used, spoiler=spoiler, last_id=last),
        reverse=True,
    )
    return next((s for s in ranked if score_scene(voice, s, used=used, spoiler=spoiler, last_id=last) >= 0), None)


def _next_sequential(ordered, cursor, voice, used, spoiler, last):
    """Walk the source forward.

    If the narration names preferred scenes still ahead, seek to that window.
    Otherwise take the next window in order — never score-jump to a later
    high-plot cut (that stole part-2+ evidence and desynced voice vs video).
    """
    n = len(ordered)
    if not n or cursor >= n:
        return None
    preferred = {
        int(value) for value in (voice.get("preferred_scene_ids") or [])
        if str(value).lstrip("-").isdigit() or isinstance(value, int)
    }
    if preferred:
        for scene in ordered[cursor:]:
            members = {
                int(value)
                for value in (scene.get("member_scene_ids") or [scene["scene_id"]])
                if str(value).lstrip("-").isdigit() or isinstance(value, int)
            }
            if preferred & members:
                if score_scene(voice, scene, used=used, spoiler=spoiler, last_id=last) >= 0:
                    return scene
                break
    scene = ordered[cursor]
    if score_scene(voice, scene, used=used, spoiler=spoiler, last_id=last) >= 0:
        return scene
    # Spoiler-blocked current window: skip forward to the next usable one.
    for scene in ordered[cursor + 1 :]:
        if score_scene(voice, scene, used=used, spoiler=spoiler, last_id=last) >= 0:
            return scene
    return None
