"""Hierarchical scene → block → chapter → story graph. Never one giant prompt."""
from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from pipeline.mt.text import _lang_name
from pipeline.core.jobs import check_cancel
from pipeline.review.llm import generate_json

BLOCK = 75
CHAPTER = 4
CLOUD_STORY_WORKERS = 4


def story_workers(model: str | None) -> int:
    """Ceiling for story LLM concurrency. Local models may elastic-scale up to this."""
    name = str(model or "").lower()
    if name.startswith("cloud:"):
        return CLOUD_STORY_WORKERS
    match = re.search(r"(\d+(?:\.\d+)?)b\b", name)
    if not match:
        return 6
    billions = float(match.group(1))
    # Caps only — local Ollama uses adaptive idle scaling beneath these.
    if billions <= 4:
        return 8
    if billions <= 9:
        return 8
    if billions <= 14:
        return 4
    return 2


def story_pool_fixed(model: str | None) -> bool:
    """Cloud stays fixed (rate limits); local Ollama elastically follows machine idle."""
    return str(model or "").lower().startswith("cloud:")


def build_story(
    visuals: list[dict[str, Any]],
    *,
    language: str = "vi",
    model: str | None = None,
    on_progress: Callable[[str, int, int, int], None] | None = None,
    title: str = "",
    job_id: str | None = None,
) -> dict[str, Any]:
    if not visuals:
        return {
            "blocks": [],
            "chapters": [],
            "movie_context": {},
            "story_graph": {},
        }
    chunk_size = max(60, len(visuals) // 3 + 1)
    blocks = [visuals[i : i + chunk_size] for i in range(0, len(visuals), chunk_size)] or [visuals]
    cap = min(len(blocks), story_workers(model))
    fixed = story_pool_fixed(model)
    block_summaries = _parallel_summaries(
        [chunk for chunk in blocks if chunk],
        lambda chunk: _summarize_block(chunk, language, model=model, title=title, job_id=job_id),
        stage="blocks",
        on_progress=on_progress,
        workers=cap,
        fixed=fixed,
        cancel_check=(lambda: check_cancel(job_id)) if job_id else None,
    )
    chapters = []
    for i, b in enumerate(block_summaries):
        chapters.append({
            "index": i,
            "scene_ids": b.get("scene_ids") or [],
            "start": b.get("start") or 0,
            "end": b.get("end") or 0,
            "summary": b.get("summary") or "",
            "characters": b.get("characters") or [],
            "events": b.get("events") or [],
            "themes": [],
        })
    check_cancel(job_id)
    context = _compile_movie_context(chapters, language, model=model, title=title, job_id=job_id)
    graph = _story_graph(block_summaries, chapters, visuals, context)
    return {
        "blocks": block_summaries,
        "chapters": chapters,
        "movie_context": context,
        "story_graph": graph,
    }


def _parallel_summaries(
    items: list[Any],
    summarize: Callable[[Any], dict[str, Any]],
    *,
    stage: str,
    on_progress: Callable[[str, int, int, int], None] | None,
    workers: int,
    fixed: bool = False,
    cancel_check: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Run summaries with elastic pool when local; cloud keeps a fixed cap."""
    if not items:
        return []
    from pipeline.core.resources import run_with_adaptive_workers

    # Local Ollama: kind=gpu so idle CPU/GPU/RAM can raise concurrency under `workers`.
    # Cloud: fixed network pool (rate limits / key rotation handle pressure).
    return run_with_adaptive_workers(
        items,
        summarize,
        kind="network" if fixed else "gpu",
        requested=workers if fixed else 0,
        cap=workers,
        thread_name_prefix=f"review-{stage}",
        on_progress=(
            (lambda done, total, workers: on_progress(stage, done, total, workers))
            if on_progress
            else None
        ),
        cancel_check=cancel_check,
    )


def _safe_float(val: Any, default: float = 0.4) -> float:
    try:
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val or "").strip()
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        return float(m.group(1)) if m else default
    except Exception:
        return default


def _summarize_block(
    scenes: list[dict[str, Any]], language: str, *, model: str | None = None, title: str = "", job_id: str | None = None,
) -> dict[str, Any]:
    ids = [s["scene_id"] for s in scenes]
    # Transcript is first-class evidence.  A scene description is only a
    # fallback for silent footage and must not override spoken facts.
    blob = " | ".join(f"#{s['scene_id']} {s.get('transcript') or s.get('description') or ''}" for s in scenes)[:2000]
    name = _lang_name(language)
    parsed = generate_json(
        f"Video title: {title or 'Unknown'}. "
        f"Summarize these consecutive movie scenes in {name}. "
        f"Write summary and names in {name} only; source dialogue may be another language. "
        "JSON keys: summary, characters, events, importance (0-1). Keep scene_ids.\n"
        "GROUNDING: Treat the supplied scene text as the complete evidence. Do not add objects, food, weapons,"
        " locations, actions, characters, or plot events that are not explicitly supported by it. If evidence is"
        " sparse, use a neutral summary instead of guessing.\n" + blob,
        model=model,
        job_id=job_id,
    )
    if not isinstance(parsed, dict):
        parsed = {
            "summary": " ".join((s.get("description") or "")[:80] for s in scenes[:6]),
            "characters": [],
            "events": [],
            "importance": max((s.get("plot_score") or 0) for s in scenes),
        }
    return {
        "scene_ids": ids,
        "start": scenes[0]["start"],
        "end": scenes[-1]["end"],
        "summary": str(parsed.get("summary") or "")[:500],
        "characters": list(parsed.get("characters") or []),
        "events": list(parsed.get("events") or []),
        "importance": _safe_float(parsed.get("importance"), 0.4),
    }


def _summarize_chapter(
    blocks: list[dict[str, Any]], language: str, *, index: int, model: str | None = None, title: str = "",
) -> dict[str, Any]:
    blob = " ".join(b.get("summary") or "" for b in blocks)[:1500]
    name = _lang_name(language)
    parsed = generate_json(
        f"Video title: {title or 'Unknown'}. "
        f"Summarize this sequence of scene blocks into a single chapter summary in {name}. "
        "Combine only the supplied events. Do not add objects, locations, characters, or actions absent from the"
        " evidence; describe uncertain details neutrally. JSON keys: title, summary, characters, importance (0-1).\n" + blob,
        model=model,
    )
    if not isinstance(parsed, dict):
        parsed = {"summary": blob[:400], "characters": [], "events": [], "themes": []}
    scene_ids: list[int] = []
    for b in blocks:
        scene_ids.extend(b.get("scene_ids") or [])
    return {
        "index": index,
        "scene_ids": scene_ids,
        "start": blocks[0]["start"] if blocks else 0,
        "end": blocks[-1]["end"] if blocks else 0,
        "summary": str(parsed.get("summary") or "")[:1000],
        "characters": list(parsed.get("characters") or []),
        "events": list(parsed.get("events") or []),
        "themes": list(parsed.get("themes") or []),
    }


def _compile_movie_context(
    chapters: list[dict[str, Any]], language: str, *, model: str | None = None, title: str = "", job_id: str | None = None,
) -> dict[str, Any]:
    blob = "\n".join(f"Ch{c['index']}: {c.get('summary')}" for c in chapters)[:4000]
    name = _lang_name(language)
    parsed = generate_json(
        f"Video title: {title or 'Unknown'}. "
        f"Analyze these chronological chapter summaries and compile a global movie context in {name}.\n"
        "Use only the supplied summaries; never invent concrete props, locations, actions, or people. "
        "JSON format: {logline, themes:[], tone:[], spoiler_outline, characters:[{name, role, description}]}.\n" + blob,
        model=model,
        job_id=job_id,
    )
    if not isinstance(parsed, dict):
        parsed = {
            "logline": "",
            "themes": [],
            "tone": "dramatic",
            "spoiler_outline": "",
            "characters": [],
        }
    return parsed


def _story_graph(
    blocks: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    visuals: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    ranked = sorted(visuals, key=lambda s: float(s.get("plot_score") or 0), reverse=True)
    highlights = ranked[:12]
    climax = ranked[:3]
    events = []
    for i, block in enumerate(sorted(blocks, key=lambda item: float(item.get("start") or 0))):
        events.append({
            "event_id": f"evt_{i:03d}",
            "summary": block.get("summary") or "",
            "characters": block.get("characters") or [],
            "scene_ids": block.get("scene_ids") or [],
            "start": block.get("start") or 0,
            "end": block.get("end") or 0,
            "importance": _safe_float(block.get("importance"), 0.0),
            "spoiler_level": 0,
        })
    chars: list[str] = []
    for c in chapters:
        for name in c.get("characters") or []:
            if name and name not in chars:
                chars.append(str(name))
    return {
        "characters": chars or list(context.get("characters") or []),
        "relationships": [],
        "acts": [{"index": c["index"], "summary": c.get("summary"), "scene_ids": c.get("scene_ids")} for c in chapters],
        "events": events,
        "themes": list(context.get("themes") or []),
        "conflicts": [],
        "highlights": [h["scene_id"] for h in highlights],
        "climax": [h["scene_id"] for h in climax],
        "ending": [visuals[-1]["scene_id"]] if visuals else [],
    }
