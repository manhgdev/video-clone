"""Review orchestrator using parallel, fully finalized Review parts."""
from __future__ import annotations

import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from pipeline.clone_run.headless import _copy_output
from pipeline.clone_run.open_source import open_local_video
from pipeline.core.jobs import check_cancel, run_cmd, share_cancel
from pipeline.core.media import _ff_bin, ffprobe_duration, video_size
from pipeline.core.project import ensure_layout, save_meta, set_status
from pipeline.export.burn import cover_and_burn
from pipeline.export.mux_audio import mux_dub
from pipeline.queue.store import mutate
from pipeline.review.adapter import (
    _fallback_review_bbox,
    apply_edit_plan,
    caption_export_settings,
    locate_review_caption_bands,
)
from pipeline.review.cache import INVALIDATE_FROM, STAGES, load_json, movie_root, run_dir, save_json
from pipeline.review.compose import compose_video, concat_parts
from pipeline.review.inspect import inspect_media
from pipeline.review.llm import list_ollama_models, pick_llm
from pipeline.review.match import match_voice, resolve_build_mode
from pipeline.review.scenes import detect_scenes
from pipeline.review.script import NARRATION, scrub_script, write_script
from pipeline.review.story import build_story
from pipeline.review.transcript import load_transcript
from pipeline.review.vision import analyze_scenes
from pipeline.tts import tts_segment


REVIEW_PLAN_VERSION = 14
REVIEW_STORY_VERSION = 6
REVIEW_FINALIZE_VERSION = 8
REVIEW_MATCH_VERSION = 4
WINDOW_SOURCE_VERSION = 1


import time as _time


class _timed:
    """Context manager: log elapsed time for a stage to app_log and return seconds."""
    def __init__(self, label: str):
        self.label = label
        self.elapsed = 0.0
    def __enter__(self):
        self._t = _time.monotonic()
        try:
            from pipeline.core.app_log import append_log
            append_log(f"[pipeline] ▶ {self.label}")
        except Exception:
            pass
        return self
    def __exit__(self, *_):
        self.elapsed = _time.monotonic() - self._t
        try:
            from pipeline.core.app_log import append_log
            append_log(f"[pipeline] ✔ {self.label} {self.elapsed:.1f}s")
        except Exception:
            pass


def run_review_job(job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job["id"])
    src = Path(str(job.get("source") or ""))
    settings = dict(job.get("settings_snapshot") or {})
    # v6 adds part-specific evidence so cached generic scripts are rebuilt.
    settings["reviewPlanVersion"] = REVIEW_PLAN_VERSION
    settings["reviewMatchVersion"] = REVIEW_MATCH_VERSION
    mode = resolve_build_mode(settings)
    settings["buildMode"] = mode
    review_mode = str(settings.get("reviewMode") or "llm").strip().lower()
    if review_mode not in {"llm", "cloud", "translate"}:
        review_mode = "llm"
    settings["reviewMode"] = review_mode
    review_provider = str(settings.get("reviewProvider") or "gemini").strip().lower()
    if review_provider not in {"gemini", "grok", "openai"}:
        review_provider = "gemini"
    settings["reviewProvider"] = review_provider
    source_lang = str(settings.get("sourceLang") or "auto")
    lang = str(settings.get("language") or "vi")
    voice = str(settings.get("voice") or "system")
    _note(job_id, f"Nguồn: {src}", stage="metadata", progress=0.04)
    _note(job_id, f"Cài đặt: gốc={source_lang} thoại={lang} mode={mode} voice={voice} caption={settings.get('captionMode') or 'cover'}")
    check_cancel(job_id)
    meta = inspect_media(src)
    root = movie_root(src)
    save_json(root / "metadata.json", meta)
    duration = float(meta.get("duration") or 0)
    _note(
        job_id,
        f"Metadata: {_mmss(duration)} · {int(meta.get('width') or 0)}x{int(meta.get('height') or 0)} · cache {root.name}",
    )
    windows = _windows(duration, mode, settings)
    nwin = max(1, len(windows))
    window_duration = sum(max(0.0, end - start) for start, end in windows)
    part_pipeline = mode == "accumulate" and nwin > 1
    available_models = list_ollama_models() if review_mode == "llm" else []
    requested_model = str(settings.get("reviewModel") or "auto").strip()
    if review_mode == "llm" and requested_model not in {"", "auto"}:
        if requested_model not in available_models:
            raise RuntimeError("REVIEW_LLM_MODEL_UNAVAILABLE")
        review_model = requested_model
    elif review_mode == "llm":
        review_model = pick_llm(available_models) if review_mode == "llm" else None
    elif review_mode == "cloud":
        from pipeline.core.app_config import load_app_config

        cloud = load_app_config()["cloud"].get(review_provider) or {}
        if not str(cloud.get("apiKey") or "").strip():
            raise RuntimeError(f"REVIEW_CLOUD_KEY_REQUIRED:{review_provider}")
        review_model = f"cloud:{review_provider}:{str(cloud.get('reviewModel') or '')}"
    else:
        review_model = None
    if review_mode == "llm" and not review_model:
        raise RuntimeError("REVIEW_LLM_REQUIRED")
    settings["reviewModel"] = review_model or "translate"
    _note(
        job_id,
        (
            f"Review script: AI recap theo block cảnh · model {review_model}"
            if review_model
            else "Review script: dịch tuần tự transcript, không dùng LLM"
        ),
    )

    with _timed("scene_detect") as _t_scenes:
        scenes = [] if part_pipeline else _cached_or(
            root / "scenes.json", lambda: detect_scenes(src, duration, job_id=job_id),
            job_id, "scenes", 0.12, "Cắt cảnh",
        )
    _note(job_id, f"Cảnh: {len(scenes) if isinstance(scenes, list) else 0} đoạn · {_t_scenes.elapsed:.0f}s")
    with _timed("transcript") as _t_tr:
        transcript = [] if part_pipeline else _cached_or(
            root / f"transcript_{source_lang}.json",
            lambda: load_transcript(
                src, root, job_id=job_id, duration=duration,
                sidecar=str(settings.get("subtitleFile") or ""),
                source_lang=source_lang,
            ),
            job_id,
            "transcript",
            0.28,
            f"Transcript ({source_lang})",
        )
    sample = _clip((transcript[0].get("text") if transcript else "") or "")
    _note(job_id, f"Transcript: {len(transcript)} câu · {_t_tr.elapsed:.0f}s" + (f" · ví dụ: {sample}" if sample else ""))
    with _timed("visual_analysis") as _t_vis:
        visuals = [] if part_pipeline else _cached_or(
            root / "visual_analysis" / f"scenes_{review_mode}_{source_lang}.json",
            lambda: analyze_scenes(
                src, scenes, transcript, root, job_id=job_id,
                use_vision=review_mode == "llm",
            ),
            job_id,
            "vision",
            0.42,
            "Phân tích hình",
        )
    _note(job_id, f"Vision: {len(visuals) if isinstance(visuals, list) else 0} cảnh · {_t_vis.elapsed:.0f}s")

    run_id = str(job.get("runId") or uuid.uuid4().hex[:8])
    rd = run_dir(root, run_id)
    save_json(rd / "settings.json", settings)
    mutate(job_id, {"runId": run_id})

    def story_progress(stage: str, done: int, total: int, workers: int) -> None:
        label = "Tóm tắt cảnh" if stage == "blocks" else "Tóm tắt chương"
        _note(
            job_id,
            f"{label}: {done}/{total} · {workers} luồng",
            stage="story_graph",
            progress=0.42 + 0.09 * (done / max(1, total)),
        )

    if review_mode != "translate":
        story_builder = lambda: build_story(
            visuals, language=lang, model=review_model, on_progress=story_progress, title=src.name,
        )
    else:
        story_builder = lambda: _faithful_story(visuals)
    story_model_key = re.sub(r"[^A-Za-z0-9._-]+", "_", review_model or "translate")
    with _timed("story_graph") as _t_story:
        story = {} if part_pipeline else _cached_or(
            root / f"story_graph_v{REVIEW_STORY_VERSION}_{review_mode}_{story_model_key}_{lang}_{source_lang}.json",
            story_builder,
            job_id,
            "story_graph",
            0.52,
            f"Cốt truyện ({lang})",
        )
    ctx = story.get("movie_context") or {}
    save_json(rd / "movie_context.json", ctx)
    save_json(rd / "chapter_summaries.json", story.get("chapters") or [])
    save_json(rd / "segment_summaries.json", story.get("blocks") or [])
    _note(job_id, f"Cốt truyện: {len(story.get('chapters') or [])} chương · {len(story.get('blocks') or [])} khối · {_t_story.elapsed:.0f}s · { _clip(ctx.get('logline') or '') }")

    start, prev_rd, changed = _resume_point(root, settings)
    reuse_script = _reuse(start, "script")
    reuse_tts = _reuse(start, "tts")
    reuse_match = _reuse(start, "matching")
    reuse_tl = _reuse(start, "timeline")
    if part_pipeline:
        # A window now owns its own local-time source and analysis cache.
        # Legacy full-source scripts/audio cannot safely be reused here.
        reuse_script = reuse_tts = reuse_match = reuse_tl = False
    prev_scripts = _load_scripts(prev_rd, nwin) if (reuse_script or reuse_tts or reuse_match) else None
    scripts_scrubbed = False
    if prev_scripts:
        cleaned: list[dict[str, Any]] = []
        for row in prev_scripts:
            scrubbed = scrub_script(row, lang)
            if not scrubbed:
                cleaned = []
                break
            scripts_scrubbed = scripts_scrubbed or scrubbed != row
            cleaned.append(scrubbed)
        prev_scripts = cleaned or None
    if reuse_script and not prev_scripts:
        reuse_script = False
    if not reuse_script:
        reuse_tts = reuse_match = reuse_tl = False
    if scripts_scrubbed:
        reuse_tts = False
    prev_voice = load_json(prev_rd / "voice.json") if prev_rd and reuse_tts else None
    if reuse_tts and (not prev_scripts or not _voice_wavs_ok(prev_scripts, prev_voice)):
        reuse_tts = False
    if not reuse_tts:
        reuse_match = reuse_tl = False
    finalize_key = _finalize_key(settings)
    if reuse_match and not _parts_complete(prev_rd, nwin, finalize_key):
        reuse_match = False
    if not reuse_match:
        reuse_tl = False
    _note(job_id, _resume_log(start, changed, reuse_script=reuse_script, reuse_tts=reuse_tts, reuse_match=reuse_match, reuse_tl=reuse_tl))

    factor = NARRATION.get(str(settings.get("narration") or "default"), 1.0)
    parts_meta = [
        {
            "index": i + 1,
            "sourceStart": a,
            "sourceEnd": b,
            "outputDuration": None,
            "expectedOutputDuration": _script_duration(mode, a, b, window_duration, settings, factor),
            "status": "pending",
        }
        for i, (a, b) in enumerate(windows)
    ]
    _refresh_part_timeline(parts_meta, factor=factor)
    _note(job_id, f"Dựng: {nwin} phần · {_fmt_range(0, duration)}", parts=parts_meta, stage="script", progress=0.56)

    project_id = open_local_video(str(src), kind="review")
    share_cancel(job_id, project_id)
    tts_dir = ensure_layout(project_id) / "tts"
    orig_pct = float(settings.get("originalAudioPct") or 0)
    _note(job_id, f"Project {project_id} · TTS voice={voice} · originalAudio={orig_pct:.0f}%", projectId=project_id)

    all_voiced: list[dict[str, Any]] = []
    combined_segs: list[dict[str, Any]] = []
    raw_part_files: list[Path] = []
    part_files: list[Path] = []
    t_voice = 0.0

    if reuse_script and prev_scripts:
        for i, script in enumerate(prev_scripts):
            save_json(rd / f"script_{i:02d}.json", script)

    if reuse_match and prev_rd:
        prev_plan = load_json(prev_rd / "edit_plan.json") or {}
        combined_segs = [dict(s) for s in (prev_plan.get("segments") or []) if isinstance(s, dict)]
        t_voice = float(prev_plan.get("duration") or 0)
        all_voiced = [dict(v) for v in (load_json(prev_rd / "voice.json") or []) if isinstance(v, dict)]
        cached_durations = _part_durations(combined_segs, nwin)
        for pi in range(nwin):
            raw_part = prev_rd / f"raw_part_{pi:02d}.mp4"
            part = prev_rd / f"part_{pi:02d}.mp4"
            raw_part_files.append(raw_part)
            part_files.append(part)
            parts_meta[pi]["status"] = "done"
            parts_meta[pi]["output"] = str(part)
            parts_meta[pi]["outputDuration"] = cached_durations[pi]
        _refresh_part_timeline(parts_meta, factor=factor)
        _note(job_id, f"Ghép hình: cache {nwin} phần", parts=parts_meta, stage="matching", progress=0.84)
    else:
        part_results: dict[int, tuple[Path, dict[str, Any], list[dict[str, Any]]]] = {}
        pending: list[tuple[int, float, float]] = []
        for pi, (w0, w1) in enumerate(windows):
            check_cancel(job_id)
            raw_part = rd / f"raw_part_{pi:02d}.mp4"
            part = rd / f"part_{pi:02d}.mp4"
            script_dur = _script_duration(mode, w0, w1, window_duration, settings, factor)
            cached_script = load_json(rd / f"script_{pi:02d}.json") or {}
            cached_plan = load_json(rd / f"plan_{pi:02d}.json") or {}
            cached_voice = load_json(rd / f"voice_{pi:02d}.json") or []
            cached_final = load_json(rd / f"final_{pi:02d}.json") or {}
            if (
                not scripts_scrubbed
                and _part_cache_matches(
                    cached_script, script_dur,
                    source_start=w0 if part_pipeline else None,
                    source_end=w1 if part_pipeline else None,
                )
                and _media_artifact_ok(raw_part)
                and _media_artifact_ok(part)
                and isinstance(cached_plan, dict)
                and cached_plan.get("segments")
                and isinstance(cached_voice, list)
                and cached_voice
                and cached_final.get("finalizeKey") == finalize_key
            ):
                voiced = [dict(v) for v in cached_voice if isinstance(v, dict)]
                part_results[pi] = (part, cached_plan, voiced)
                parts_meta[pi].update({
                    "status": "done",
                    "output": str(part),
                    "outputDuration": float(cached_plan.get("duration") or 0),
                })
                _refresh_part_timeline(parts_meta, factor=factor)
                _note(job_id, f"Phần {pi + 1}/{nwin}: giữ kết quả đã xong", parts=parts_meta)
            else:
                pending.append((pi, w0, w1))

        # Window work is independent in accumulate mode. Keep aggregate nested
        # TTS/FFmpeg work bounded while preserving source-index assembly below.
        _outer_workers, bounded_tts_workers, bounded_compose_workers = _accumulate_worker_limits(len(pending))
        # Ceilings only — TTS/compose pools elastically fill up to these.
        tts_workers = bounded_tts_workers if mode == "accumulate" else None
        compose_workers = bounded_compose_workers if mode == "accumulate" else None
        parts_lock = threading.Lock()

        def build_part(item: tuple[int, float, float]) -> tuple[int, Path, dict[str, Any], list[dict[str, Any]]]:
            pi, w0, w1 = item
            check_cancel(job_id)
            with parts_lock:
                parts_meta[pi]["status"] = "running"
                _refresh_part_timeline(parts_meta, factor=factor)
                _note(
                    job_id,
                    f"Phần {pi + 1}/{nwin} {_fmt_range(w0, w1)} — bắt đầu xử lý",
                    parts=parts_meta,
                    stage=f"Phần {pi + 1}/{nwin}",
                    progress=0.10 + 0.46 * (pi / nwin),
                )
            raw_part = rd / f"raw_part_{pi:02d}.mp4"
            part = rd / f"part_{pi:02d}.mp4"
            part_source = src
            part_meta = meta
            part_transcript = transcript
            if part_pipeline:
                part_root = root / "windows" / f"{pi:02d}_{int(w0 * 1000):010d}_{int(w1 * 1000):010d}"
                part_source = _materialize_window(src, part_root, w0, w1, job_id)
                part_duration = max(0.01, float(ffprobe_duration(part_source) or (w1 - w0)))
                part_meta = inspect_media(part_source)
                part_scenes = _cached_or(
                    part_root / "scenes.json",
                    lambda: detect_scenes(part_source, part_duration, job_id=job_id),
                    job_id, "scenes", 0.12, f"Cắt cảnh phần {pi + 1}",
                )
                part_transcript = _cached_or(
                    part_root / f"transcript_{source_lang}.json",
                    lambda: load_transcript(
                        part_source, part_root, job_id=job_id, duration=part_duration,
                        sidecar="", source_lang=source_lang,
                    ),
                    job_id, "transcript", 0.28, f"Transcript phần {pi + 1}",
                )
                vis = _cached_or(
                    part_root / "visual_analysis" / f"scenes_{review_mode}_{source_lang}.json",
                    lambda: analyze_scenes(
                        part_source, part_scenes, part_transcript, part_root, job_id=job_id,
                        use_vision=review_mode == "llm",
                    ),
                    job_id, "vision", 0.42, f"Phân tích hình phần {pi + 1}",
                )
                part_story_model_key = re.sub(r"[^A-Za-z0-9._-]+", "_", review_model or "translate")

                def part_story_progress(stage: str, done: int, total: int, workers: int) -> None:
                    label = "khối cảnh" if stage == "blocks" else "chương"
                    _note(
                        job_id,
                        f"Cốt truyện phần {pi + 1}: {label} {done}/{total} · {workers} luồng",
                        stage="story_graph",
                        progress=0.42 + 0.09 * (done / max(1, total)),
                    )

                story_part = _cached_or(
                    part_root / f"story_graph_v{REVIEW_STORY_VERSION}_{review_mode}_{part_story_model_key}_{lang}_{source_lang}.json",
                    (lambda: build_story(
                        vis, language=lang, model=review_model, on_progress=part_story_progress, title=src.name,
                    ))
                    if review_mode != "translate" else (lambda: _faithful_story(vis)),
                    job_id, "story_graph", 0.52, f"Cốt truyện phần {pi + 1}",
                )
            else:
                vis = visuals
                story_part = story
            script_dur = _script_duration(mode, w0, w1, window_duration, settings, factor)
            with parts_lock:
                _note(
                    job_id,
                    f"Phần {pi + 1}/{nwin} {_fmt_range(w0, w1)} — "
                    f"{'kịch bản cache' if reuse_script else 'viết kịch bản'} · "
                    f"TTS {tts_workers or 1} / FFmpeg {compose_workers or 1} luồng",
                    parts=parts_meta,
                    stage=f"Phần {pi + 1}/{nwin}",
                    progress=0.56 + 0.28 * (pi / nwin),
                )
            if reuse_script and prev_scripts:
                script = dict(prev_scripts[pi])
            else:
                _note(
                    job_id,
                    f"Phần {pi + 1}: LLM đang viết kịch bản (~{round(script_dur*5.0):.0f} từ · {review_model})…",
                    parts=parts_meta, stage=f"LLM {pi+1}/{nwin}",
                    progress=0.56 + 0.28 * (pi / nwin),
                )
            with _timed(f"script_p{pi}") as _t_sc:
                script = write_script(
                    story_part,
                    duration_sec=script_dur,
                    style=str(settings.get("style") or "normal"),
                    language=lang,
                    spoiler=str(settings.get("spoiler") or "none"),
                    narration=str(settings.get("narration") or "default"),
                    notes=str(settings.get("notes") or ""),
                    genre=str(settings.get("genre") or ""),
                    visuals=vis,
                    source_transcript=part_transcript if part_pipeline else [
                        row for row in transcript
                        if float(row.get("end") or 0) >= w0
                        and float(row.get("start") or 0) <= w1
                    ],
                    project_id=project_id,
                    use_llm=review_mode != "translate",
                    llm_model=review_model,
                )
            script["reviewPlanVersion"] = REVIEW_PLAN_VERSION
            script["targetDurationSec"] = round(script_dur, 3)
            if part_pipeline:
                script["sourceStart"] = round(w0, 3)
                script["sourceEnd"] = round(w1, 3)
                script["windowSourceVersion"] = WINDOW_SOURCE_VERSION
            save_json(rd / f"script_{pi:02d}.json", script)
            segs = script.get("segments") or []
            _note(job_id, f"Kịch bản phần {pi + 1}: {len(segs)} đoạn · {script_dur:.0f}s · {_t_sc.elapsed:.0f}s" + (" · cache" if reuse_script else ""))
            if segs:
                _note(job_id, f"Đoạn 1: {_clip(segs[0].get('text') or '', 120)}")
            if reuse_tts:
                voiced = _attach_prev_voice(script, prev_voice, pi) or []
                _note(job_id, f"TTS phần {pi + 1}: cache {len(voiced)} file")
            else:
                with _timed(f"tts_p{pi}") as _t_tts:
                    voiced = _tts_parallel(
                        segs,
                        voice=voice,
                        tts_dir=tts_dir,
                        pi=pi,
                        lang=lang,
                        job_id=job_id,
                        project_id=project_id,
                        max_workers=tts_workers,
                    )
                tts_sum = sum(float(v.get("duration") or 0) for v in voiced)
                _note(job_id, f"TTS xong phần {pi + 1}: {len(voiced)} file · {tts_sum:.1f}s audio · {_t_tts.elapsed:.0f}s real")
            voiced = _cap_voiced_duration(voiced, script_dur)
            plan = match_voice(
                voiced,
                vis,
                style=str(settings.get("style") or "normal"),
                spoiler=str(settings.get("spoiler") or "none"),
                mode=mode,
                keep_sec=float(settings.get("keepSec") or 4),
                skip_sec=float(settings.get("skipSec") or 10),
                pause_pace=str(settings.get("pausePace") or "balanced"),
            )
            clip_count = sum(len(seg.get("clips") or []) for seg in plan.get("segments") or [])
            _note(
                job_id,
                f"Ghép hình phần {pi + 1}: {clip_count} clip từ {len(vis)} cảnh",
            )
            with _timed(f"compose_p{pi}") as _t_comp:
                compose_video(
                    part_source,
                    plan,
                    raw_part,
                    ratio=str(settings.get("ratio") or "16:9"),
                    width=int(part_meta.get("width") or 1920),
                    height=int(part_meta.get("height") or 1080),
                    job_id=job_id,
                    original_pct=100.0 if orig_pct > 0.5 else 0.0,
                    clip_workers=compose_workers,
                )
            _note(job_id, f"FFmpeg compose phần {pi + 1}: {clip_count} clip · {_t_comp.elapsed:.0f}s")
            check_cancel(job_id)
            audio_segments = _part_export_segments(plan, raw_part)
            caption_segments = _review_caption_cues(audio_segments)
            caption_flags = caption_export_settings(settings)
            mux_source = raw_part
            if caption_flags["burnSubs"] or caption_flags["coverHardsubs"]:
                burned = rd / f"burned_part_{pi:02d}.mp4"
                cover_and_burn(
                    raw_part,
                    caption_segments,
                    burned,
                    cover=bool(caption_flags["coverHardsubs"]),
                    burn=bool(caption_flags["burnSubs"]),
                    subtitle_font_size=int(settings.get("subtitleFontSize") or 0),
                    subtitle_font_family=str(settings.get("subtitleFontFamily") or "system"),
                    project_id=project_id,
                    workers=int(compose_workers or 0),
                    caption_placement=str(caption_flags["captionPlacement"]),
                    cover_mask_style=str(settings.get("coverMaskStyle") or "blur"),
                    cover_mask_color=str(settings.get("coverMaskColor") or "#4c1d95"),
                    cover_mask_opacity=int(settings.get("coverMaskOpacity", 40)),
                    caption_text_color=str(settings.get("captionTextColor") or "#ffffff"),
                    caption_bg_style=str(settings.get("captionBgStyle") or "none"),
                    caption_bg_color=str(settings.get("captionBgColor") or "#000000"),
                    caption_bg_opacity=int(settings.get("captionBgOpacity", 55)),
                    caption_stroke=bool(settings.get("captionStroke", True)),
                )
                mux_source = burned
            check_cancel(job_id)
            mux_dub(
                project_id,
                mux_source,
                audio_segments,
                original_audio_mode="original" if orig_pct > 0.5 else "mute",
                original_audio_volume=max(0.0, min(1.0, orig_pct / 100.0)),
                allow_video_slowdown=False,
                match="preferAudio" if mode == "stretch" else "preferVideo",
                destination=part,
                namespace=f"review_{run_id}_part_{pi:02d}",
            )
            save_json(rd / f"plan_{pi:02d}.json", plan)
            save_json(rd / f"voice_{pi:02d}.json", voiced)
            save_json(rd / f"final_{pi:02d}.json", {
                "reviewPlanVersion": REVIEW_PLAN_VERSION,
                "finalizeKey": finalize_key,
                "raw": str(raw_part),
                "finished": str(part),
            })
            part_duration = float(plan.get("duration") or 0)
            with parts_lock:
                parts_meta[pi].update({
                    "status": "done",
                    "output": str(part),
                    "outputDuration": part_duration,
                })
                _refresh_part_timeline(parts_meta, factor=factor)
                _note(job_id, f"Phần {pi + 1} xong · {part.name} · {part.stat().st_size if part.is_file() else 0} bytes", parts=parts_meta)
            return pi, part, plan, voiced

        if mode == "accumulate" and len(pending) > 1:
            _note(
                job_id,
                f"Dựng tuần tự {len(pending)} phần · phần hiện tại dùng "
                f"TTS {bounded_tts_workers} / FFmpeg {bounded_compose_workers} luồng",
            )
        # Strictly finish and persist each part before starting the next one.
        # A one-worker executor still pre-queues later parts after an exception.
        rows = [build_part(item) for item in pending]
        for pi, part, plan, voiced in rows:
            part_results[pi] = (part, plan, voiced)
        raw_part_files = [rd / f"raw_part_{pi:02d}.mp4" for pi in range(nwin)]
        for pi in range(nwin):
            part, plan, voiced = part_results[pi]
            _append_part_plan(combined_segs, plan, t_voice)
            t_voice += float(plan.get("duration") or 0)
            all_voiced.extend(voiced)
            part_files.append(part)

    save_json(rd / "voice.json", [
        {"id": v["id"], "text": v.get("text") or "", "duration": v["duration"], "audio": v["audio"]}
        for v in all_voiced
    ])
    combined = {"type": "review", "duration": round(t_voice, 3), "mode": mode, "segments": combined_segs}
    save_json(rd / "edit_plan.json", combined)

    compiled = ensure_layout(project_id) / "cache" / "review_compiled.mp4"
    _note(job_id, "Timeline Editor: ghép phần thô → review_compiled.mp4", stage="timeline", progress=0.86, parts=parts_meta)
    prev_compiled = Path(str((load_json(root / "pipeline.json") or {}).get("compiled") or ""))
    if reuse_tl and compiled.is_file() and compiled.stat().st_size > 0:
        _note(job_id, "Timeline: cache review_compiled.mp4")
    elif reuse_tl and prev_compiled.is_file():
        shutil.copy2(prev_compiled, compiled)
        _note(job_id, "Timeline: lấy compiled lần trước")
    elif len(raw_part_files) == 1:
        shutil.copy2(raw_part_files[0], compiled)
    else:
        concat_parts(raw_part_files, compiled, job_id=job_id)
    combined["source"] = str(src)
    project_meta = apply_edit_plan(project_id, compiled, combined, settings=settings, voice=voice)
    _apply_fixed_review_bboxes(project_id, project_meta, compiled, settings)
    save_json(rd / "project.json", {"projectId": project_id})

    # Always finish the deliverable: concat per-part muxed videos (TTS + cuts)
    # into outputDir. review_compiled.mp4 above stays for optional Editor.
    check_cancel(job_id)
    final_join = ensure_layout(project_id) / "cache" / "review_final.mp4"
    _note(job_id, "Xuất: nối các phần đã TTS + cắt → video hoàn thiện", stage="render", progress=0.94)
    if len(part_files) == 1:
        shutil.copy2(part_files[0], final_join)
    else:
        concat_parts(
            part_files,
            final_join,
            job_id=job_id,
            reencode_fallback=False,
        )
    check_cancel(job_id)
    dest = _copy_output(final_join, str(src), settings, job)
    _note(job_id, f"Xuất: {dest}")
    artifact_run_id = prev_rd.name if reuse_match and prev_rd else run_id
    cache_refs = {"root": str(root), "run": artifact_run_id}
    mutate(job_id, {"checkpoints": list(STAGES), "cacheRefs": cache_refs, "parts": parts_meta})
    _save_pipeline(
        root,
        settings,
        artifact_run_id,
        compiled=str(compiled) if compiled.is_file() else "",
        project_id=project_id,
    )
    set_status(
        project_id,
        step="export",
        progress=100,
        message="",
        running=False,
        error=None,
    )
    return {"output": dest, "projectId": project_id, "cacheRefs": cache_refs}


def _part_export_segments(plan: dict[str, Any], video: Path) -> list[dict[str, Any]]:
    """Convert one match plan to timing using sampled source-subtitle boxes."""
    bands = locate_review_caption_bands(video)
    duration = max(0.1, float(ffprobe_duration(video) or 0))
    out: list[dict[str, Any]] = []
    for index, seg in enumerate(plan.get("segments") or []):
        start = float(seg.get("voice_start") or 0)
        end = float(seg.get("voice_end") or start)
        audio = Path(str(seg.get("audio") or ""))
        text = str(seg.get("text") or "")
        fraction = max(0.0, min(1.0, (start + end) * 0.5 / duration))
        bbox = min(bands, key=lambda band: abs(band[0] - fraction))[1]
        out.append({
            "id": str(seg.get("voice_id") or f"voice_{index:03d}"),
            "start": start,
            "end": end,
            "source": "",
            "translation": text,
            "audioFile": audio.name,
            "audioDuration": float(seg.get("audio_duration") or max(0.0, end - start)),
            "ttsSpeed": float(seg.get("tts_speed") or 1.0),
            "bbox": dict(bbox),
            "bboxInherited": True,
            "layout": "horizontal",
        })
    return out


def _review_caption_cues(
    audio_segments: list[dict[str, Any]], *, max_chars: int = 38
) -> list[dict[str, Any]]:
    """Split Review captions into brief one-line cues without splitting TTS audio."""
    cues: list[dict[str, Any]] = []
    for segment in audio_segments:
        text = " ".join(str(segment.get("translation") or "").split())
        words = text.split()
        if not words:
            continue
        chunks: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
        start = float(segment.get("start") or 0)
        end = max(start, float(segment.get("end") or start))
        total = max(1, sum(len(chunk) for chunk in chunks))
        cursor = start
        for index, chunk in enumerate(chunks):
            if index == len(chunks) - 1:
                cue_end = end
            else:
                cue_end = start + (end - start) * sum(
                    len(part) for part in chunks[: index + 1]
                ) / total
            cues.append({
                **segment,
                "id": f"{segment.get('id') or 'caption'}_caption_{index + 1:02d}",
                "start": round(cursor, 3),
                "end": round(cue_end, 3),
                "translation": chunk,
            })
            cursor = cue_end
    return cues


def _cap_voiced_duration(
    voiced: list[dict[str, Any]], target_duration: float
) -> list[dict[str, Any]]:
    """Fit overlong narration to its part target without dropping spoken text."""
    total = sum(max(0.0, float(row.get("duration") or 0)) for row in voiced)
    target = max(1.0, float(target_duration or 0))
    if total <= target + 0.05 or total <= 0:
        return voiced
    scale = target / total
    fitted: list[dict[str, Any]] = []
    for row in voiced:
        item = dict(row)
        original = max(0.05, float(item.get("duration") or 0.05))
        item["audio_duration"] = original
        item["duration"] = round(original * scale, 3)
        item["ttsSpeed"] = round(min(4.0, max(1.0, 1.0 / scale)), 4)
        fitted.append(item)
    return fitted


def _floor_voiced_duration(
    voiced: list[dict[str, Any]], target_duration: float
) -> list[dict[str, Any]]:
    """Slow slightly short narration so each Review part reaches its target."""
    total = sum(max(0.0, float(row.get("duration") or 0)) for row in voiced)
    target = max(1.0, float(target_duration or 0))
    if total >= target * 0.97 or total <= 0:
        return voiced
    scale = target / total
    # Voice quality degrades below 0.70×; the script prompt supplies the rest.
    speed = max(0.70, min(1.0, 1.0 / scale))
    effective_scale = 1.0 / speed
    fitted: list[dict[str, Any]] = []
    for row in voiced:
        item = dict(row)
        original = max(0.05, float(item.get("duration") or 0.05))
        item["audio_duration"] = original
        item["duration"] = round(original * effective_scale, 3)
        item["ttsSpeed"] = round(speed, 4)
        fitted.append(item)
    return fitted


def _apply_fixed_review_bboxes(
    project_id: str,
    meta: dict[str, Any],
    compiled: Path,
    settings: dict[str, Any],
) -> None:
    """Persist sampled OCR boxes so Editor preview matches the Review export."""
    if caption_export_settings(settings)["burnSubs"]:
        bands = locate_review_caption_bands(compiled)
        duration = max(0.1, float(ffprobe_duration(compiled) or 0))
        for segment in meta.get("segments") or []:
            start = float(segment.get("start") or 0)
            end = max(start, float(segment.get("end") or start))
            fraction = max(0.0, min(1.0, (start + end) * 0.5 / duration))
            segment["bbox"] = dict(min(bands, key=lambda band: abs(band[0] - fraction))[1])
            segment["bboxInherited"] = True
            segment["layout"] = "horizontal"
        meta["bboxLocateVersion"] = 4
    save_meta(project_id, meta)


def _faithful_story(visuals: list[dict[str, Any]]) -> dict[str, Any]:
    """Chronological source beats for a translation-led Review without an LLM."""
    blocks: list[dict[str, Any]] = []
    for index in range(0, len(visuals), 20):
        chunk = visuals[index : index + 20]
        if not chunk:
            continue
        text = " ".join(
            str(scene.get("transcript") or scene.get("description") or "").strip()
            for scene in chunk
        ).strip()
        blocks.append({
            "scene_ids": [scene.get("scene_id") for scene in chunk],
            "start": float(chunk[0].get("start") or 0),
            "end": float(chunk[-1].get("end") or 0),
            "summary": text[:2400],
            "characters": [],
            "events": [],
            "importance": max(float(scene.get("plot_score") or 0) for scene in chunk),
        })
    events = [
        {
            "event_id": f"evt_{index:03d}",
            "summary": block["summary"],
            "scene_ids": block["scene_ids"],
            "start": block["start"],
            "end": block["end"],
            "importance": block["importance"],
            "spoiler_level": 0,
        }
        for index, block in enumerate(blocks)
    ]
    return {
        "blocks": blocks,
        "chapters": list(blocks),
        "movie_context": {"logline": "", "themes": [], "characters": []},
        "story_graph": {"events": events, "highlights": [], "climax": [], "ending": []},
    }


def _windows(duration: float, mode: str, settings: dict[str, Any]) -> list[tuple[float, float]]:
    duration = max(duration, 1.0)
    if mode != "accumulate":
        return [(0.0, duration)]
    step = max(60.0, float(settings.get("chunkMinutes") or 15) * 60.0)
    # ponytail: cap 40 parts so a 12h file cannot spawn unbounded LLM/TTS loops. Upgrade: stream parts as child jobs.
    full_parts = max(1, int(duration // step))
    remainder = duration - full_parts * step
    part_count = full_parts if 0 < remainder < step * 0.5 else full_parts + bool(remainder > 0.5)
    part_count = min(40, int(part_count))
    balanced = duration / part_count
    return [
        (round(index * balanced, 6), round(duration if index == part_count - 1 else (index + 1) * balanced, 6))
        for index in range(part_count)
    ]


def _accumulate_worker_limits(part_count: int) -> tuple[int, int, int]:
    """One Review window at a time; inner TTS/FFmpeg use adaptive ceilings."""
    # ponytail: outer=1 avoids multi-part disk thrash; inner caps are ceilings
    # for run_with_adaptive_workers (requested=0), not fixed thread counts.
    del part_count
    import os
    cores = max(1, os.cpu_count() or 4)
    # VideoToolbox/NVENC clip jobs are GPU-bound and short — more workers = faster.
    compose_cap = max(12, min(24, int(cores * 0.90)))
    return 1, 24, compose_cap


def _part_cache_matches(
    script: object,
    target_duration: float,
    *,
    source_start: float | None = None,
    source_end: float | None = None,
) -> bool:
    """Reject old plans while preserving finalized interrupted work."""
    if not isinstance(script, dict) or script.get("reviewPlanVersion") != REVIEW_PLAN_VERSION:
        return False
    try:
        cached_target = float(script.get("targetDurationSec") or 0)
    except (TypeError, ValueError):
        return False
    if not (cached_target > 0 and abs(cached_target - target_duration) <= 0.05):
        return False
    if source_start is None or source_end is None:
        return True
    try:
        return (
            script.get("windowSourceVersion") == WINDOW_SOURCE_VERSION
            and abs(float(script.get("sourceStart")) - source_start) <= 0.05
            and abs(float(script.get("sourceEnd")) - source_end) <= 0.05
        )
    except (TypeError, ValueError):
        return False


def _finalize_key(settings: dict[str, Any]) -> str:
    keys = (
        "reviewPlanVersion",
        "subtitle",
        "captionMode",
        "originalAudioPct",
        "subtitleFontSize",
        "subtitleFontFamily",
        "coverMaskStyle",
        "coverMaskColor",
        "coverMaskOpacity",
        "captionTextColor",
        "captionBgStyle",
        "captionBgColor",
        "captionBgOpacity",
        "captionStroke",
        "quality",
    )
    settings_key = "|".join(f"{key}={_norm_setting(settings.get(key))}" for key in keys)
    return f"finalizeVersion={REVIEW_FINALIZE_VERSION}|{settings_key}"


def _media_artifact_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and ffprobe_duration(path) > 0.05
    except (OSError, RuntimeError, ValueError):
        return False


def _materialize_window(src: Path, cache_dir: Path, start: float, end: float, job_id: str) -> Path:
    """Create a locally timed source so every window can be analyzed alone."""
    duration = max(0.01, end - start)
    source = cache_dir / "source.mp4"
    manifest = cache_dir / "source.json"
    expected = {
        "version": WINDOW_SOURCE_VERSION,
        "source": str(src.resolve()),
        "start": round(start, 3),
        "end": round(end, 3),
    }
    cached = load_json(manifest) or {}
    if cached == expected and _media_artifact_ok(source):
        actual = ffprobe_duration(source)
        if abs(actual - duration) <= max(1.0, duration * 0.02):
            return source

    cache_dir.mkdir(parents=True, exist_ok=True)
    source.unlink(missing_ok=True)
    # Accurate re-encode is required: stream-copy starts at a keyframe and
    # leaves local scene/transcript timestamps shifted.
    run_cmd(job_id, [
        _ff_bin("ffmpeg"), "-y", "-ss", f"{start:.3f}", "-i", str(src),
        "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-movflags", "+faststart", "-avoid_negative_ts", "make_zero",
        str(source),
    ])
    if not _media_artifact_ok(source):
        raise RuntimeError(f"REVIEW_WINDOW_CUT_FAILED:{start:.3f}-{end:.3f}")
    actual = ffprobe_duration(source)
    if abs(actual - duration) > max(1.0, duration * 0.02):
        source.unlink(missing_ok=True)
        raise RuntimeError(f"REVIEW_WINDOW_CUT_DURATION_INVALID:{start:.3f}-{end:.3f}")
    save_json(manifest, expected)
    return source


def _script_duration(mode: str, w0: float, w1: float, duration: float, settings: dict[str, Any], factor: float) -> float:
    if mode == "accumulate":
        # `durationSec` is the total review length. Source windows only make
        # the work resumable/parallel-friendly; each gets its proportional
        # share so concatenating them does not recreate the whole movie.
        target = max(30.0, float(settings.get("durationSec") or 900))
        share = max(0.0, w1 - w0) / max(1.0, duration)
        return target * share
    if mode == "fixed":
        return max(30.0, float(settings.get("durationSec") or 900) * factor)
    if mode == "smart":
        keep = max(0.4, float(settings.get("keepSec") or 4))
        skip = max(0.0, float(settings.get("skipSec") or 10))
        return max(30.0, duration * (keep / (keep + skip)) * factor)
    return max(30.0, float(settings.get("durationSec") or 900) * factor)


def _slice_story(story: dict[str, Any], visuals: list[dict[str, Any]]) -> dict[str, Any]:
    if not visuals:
        return story
    ids = {int(v["scene_id"]) for v in visuals if v.get("scene_id") is not None}
    t0 = min(float(v.get("start") or 0) for v in visuals)
    t1 = max(float(v.get("end") or 0) for v in visuals)

    def overlap(item: dict[str, Any]) -> bool:
        s0, s1 = float(item.get("start") or 0), float(item.get("end") or 0)
        if s1 > s0:
            return s1 > t0 and s0 < t1
        scene_ids = {int(x) for x in (item.get("scene_ids") or []) if str(x).isdigit() or isinstance(x, int)}
        return bool(scene_ids & ids)

    graph = dict(story.get("story_graph") or {})
    graph["acts"] = [a for a in (graph.get("acts") or []) if overlap(a)]
    graph["events"] = [
        ev for ev in (graph.get("events") or [])
        if overlap(ev) or {int(x) for x in (ev.get("scene_ids") or []) if str(x).isdigit() or isinstance(x, int)} & ids
    ]
    return {
        **story,
        "blocks": [b for b in (story.get("blocks") or []) if overlap(b)],
        "chapters": [c for c in (story.get("chapters") or []) if overlap(c)],
        "story_graph": graph,
    }


def _slice_visuals(visuals: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    out = []
    for scene in visuals:
        s0 = float(scene.get("start") or 0)
        s1 = float(scene.get("end") or 0)
        if s1 <= start or s0 >= end:
            continue
        item = dict(scene)
        item["start"] = max(s0, start)
        item["end"] = min(s1, end)
        item["duration"] = round(item["end"] - item["start"], 3)
        out.append(item)
    return out


def _append_part_plan(combined: list[dict[str, Any]], plan: dict[str, Any], offset: float) -> None:
    for seg in plan.get("segments") or []:
        item = dict(seg)
        item["voice_start"] = round(float(seg.get("voice_start") or 0) + offset, 3)
        item["voice_end"] = round(float(seg.get("voice_end") or 0) + offset, 3)
        combined.append(item)


def _part_durations(segments: list[dict[str, Any]], count: int) -> list[float | None]:
    starts: list[float | None] = [None] * count
    final_end = 0.0
    for seg in segments:
        match = re.match(r"p(\d+)_", str(seg.get("voice_id") or ""))
        if not match:
            continue
        index = int(match.group(1))
        if index >= count:
            continue
        start = float(seg.get("voice_start") or 0)
        starts[index] = start if starts[index] is None else min(float(starts[index]), start)
        final_end = max(final_end, float(seg.get("voice_end") or start))
    durations: list[float | None] = [None] * count
    for index, start in enumerate(starts):
        if start is None:
            continue
        next_start = next((float(value) for value in starts[index + 1:] if value is not None), final_end)
        durations[index] = max(0.0, next_start - float(start))
    return durations


def _refresh_part_timeline(parts: list[dict[str, Any]], *, factor: float = 1.0) -> None:
    cursor = 0.0
    for part in parts:
        source_duration = max(1.0, float(part.get("sourceEnd") or 0) - float(part.get("sourceStart") or 0))
        duration = float(
            part.get("outputDuration")
            or part.get("expectedOutputDuration")
            or source_duration * factor
        )
        part["start"] = round(cursor, 3)
        cursor += max(1.0, duration)
        part["end"] = round(cursor, 3)
        part["label"] = _fmt_range(part["start"], part["end"])


def _fmt_range(start: float, end: float) -> str:
    return f"{_mmss(start)} - {_mmss(end)}"


def _mmss(t: float) -> str:
    t = max(0, int(float(t or 0)))
    return f"{t // 60:02d}:{t % 60:02d}"


def _clip(text: Any, n: int = 180) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _note(job_id: str, msg: str, **patch: Any) -> None:
    mutate(job_id, patch, log=msg)


def _cached_or(path: Path, fn, job_id: str, stage: str, progress: float, label: str = ""):
    tag = label or stage
    cached = load_json(path)
    if cached:
        _note(job_id, f"{tag}: cache ({_qty(cached)}) — {path.name}", stage=stage, progress=progress)
        return cached
    _note(job_id, f"{tag}: đang chạy…", stage=stage, progress=progress)
    data = fn()
    save_json(path, data)
    _note(job_id, f"{tag}: xong ({_qty(data)})")
    return data


def _qty(data: Any) -> str:
    if isinstance(data, list):
        return f"{len(data)} mục"
    if isinstance(data, dict):
        return f"{len(data)} khóa"
    return "ok"


def invalidate_from(settings_changed: set[str]) -> str:
    order = list(STAGES)
    earliest = order[-1]
    for key in settings_changed:
        stage = INVALIDATE_FROM.get(key)
        if stage and order.index(stage) < order.index(earliest):
            earliest = stage
    return earliest


def _reuse(start: str, stage: str) -> bool:
    return STAGES.index(start) > STAGES.index(stage)


def _norm_setting(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    return str(value).strip()


def _settings_diff(prev: dict[str, Any], cur: dict[str, Any]) -> set[str]:
    return {key for key in INVALIDATE_FROM if _norm_setting(prev.get(key)) != _norm_setting(cur.get(key))}


def _resume_point(root: Path, settings: dict[str, Any]) -> tuple[str, Path | None, set[str]]:
    prev = load_json(root / "pipeline.json") or {}
    run = str(prev.get("run") or "")
    prev_rd = root / "runs" / run if run else None
    prev_settings = dict(prev.get("settings") or {})
    if not prev_rd or not prev_rd.is_dir():
        runs = sorted(
            (p for p in (root / "runs").glob("*") if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        prev_rd = runs[0] if runs else None
        if prev_rd:
            prev_settings = load_json(prev_rd / "settings.json") or prev_settings
    if not prev_rd:
        return "metadata", None, set()
    if not prev_settings:
        return "script", prev_rd, set()
    changed = _settings_diff(prev_settings, settings)
    return invalidate_from(changed), prev_rd, changed


def _resume_log(start: str, changed: set[str], **reuse: bool) -> str:
    names = {
        "script": "kịch bản",
        "tts": "TTS / giọng",
        "matching": "ghép hình",
        "timeline": "timeline",
        "render": "xuất video",
        "metadata": "đầu pipeline",
        "transcript": "transcript",
        "story_graph": "cốt truyện",
    }
    keys = ", ".join(sorted(changed)) if changed else "không đổi cài đặt"
    kept = []
    if reuse.get("reuse_script"):
        kept.append("kịch bản")
    if reuse.get("reuse_tts"):
        kept.append("giọng")
    if reuse.get("reuse_match"):
        kept.append("ghép hình")
    if reuse.get("reuse_tl"):
        kept.append("compiled")
    extra = f" · giữ {', '.join(kept)}" if kept else ""
    return f"Cache: làm lại từ {names.get(start, start)} · {keys}{extra}"


def _save_pipeline(root: Path, settings: dict[str, Any], run_id: str, *, compiled: str, project_id: str) -> None:
    save_json(root / "pipeline.json", {
        "settings": {key: settings.get(key) for key in INVALIDATE_FROM},
        "run": run_id,
        "compiled": compiled,
        "projectId": project_id,
    })


def _load_scripts(prev_rd: Path | None, nwin: int) -> list[dict[str, Any]] | None:
    if not prev_rd or nwin < 1:
        return None
    rows = [load_json(prev_rd / f"script_{i:02d}.json") for i in range(nwin)]
    if all(isinstance(s, dict) and (s.get("segments") or []) for s in rows):
        return rows  # type: ignore[return-value]
    return _scripts_from_plan(load_json(prev_rd / "edit_plan.json") or {}, nwin)


def _scripts_from_plan(plan: dict[str, Any], nwin: int) -> list[dict[str, Any]] | None:
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(nwin)]
    for seg in plan.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        vid = str(seg.get("voice_id") or "")
        if len(vid) < 5 or vid[0] != "p" or vid[3] != "_":
            return None
        try:
            pi = int(vid[1:3])
        except ValueError:
            return None
        if pi < 0 or pi >= nwin:
            return None
        buckets[pi].append({
            "id": vid[4:],
            "text": str(seg.get("text") or ""),
            "purpose": "body",
            "visual_intent": "",
            "character_refs": [],
            "event_refs": [],
            "preferred_scene_ids": [],
        })
    if any(not bucket for bucket in buckets):
        return None
    return [{"segments": bucket} for bucket in buckets]


def _attach_prev_voice(script: dict[str, Any], prev_voice: Any, pi: int) -> list[dict[str, Any]] | None:
    by_id = {str(v.get("id")): v for v in (prev_voice or []) if isinstance(v, dict)}
    out: list[dict[str, Any]] = []
    for seg in script.get("segments") or []:
        vid = f"p{pi:02d}_{seg['id']}"
        prev = by_id.get(vid)
        wav = Path(str((prev or {}).get("audio") or ""))
        if (
            not prev
            or str(prev.get("text") or "") != str(seg.get("text") or "")
            or not wav.is_file()
        ):
            return None
        out.append({**seg, "id": vid, "duration": float(prev.get("duration") or 0) or 2.5, "audio": str(wav)})
    return out


def _voice_wavs_ok(scripts: list[dict[str, Any]], prev_voice: Any) -> bool:
    return all(_attach_prev_voice(script, prev_voice, i) for i, script in enumerate(scripts))


def _parts_complete(prev_rd: Path | None, nwin: int, finalize_key: str) -> bool:
    if not prev_rd:
        return False
    for index in range(nwin):
        script = load_json(prev_rd / f"script_{index:02d}.json") or {}
        plan = load_json(prev_rd / f"plan_{index:02d}.json") or {}
        voice = load_json(prev_rd / f"voice_{index:02d}.json") or []
        final = load_json(prev_rd / f"final_{index:02d}.json") or {}
        if (
            script.get("reviewPlanVersion") != REVIEW_PLAN_VERSION
            or not plan.get("segments")
            or not isinstance(voice, list)
            or not voice
            or final.get("reviewPlanVersion") != REVIEW_PLAN_VERSION
            or final.get("finalizeKey") != finalize_key
            or not _media_artifact_ok(prev_rd / f"raw_part_{index:02d}.mp4")
            or not _media_artifact_ok(prev_rd / f"part_{index:02d}.mp4")
        ):
            return False
    return True


def _tts_parallel(
    segs: list[dict[str, Any]],
    *,
    voice: str,
    tts_dir: Path,
    pi: int,
    lang: str,
    job_id: str,
    project_id: str,
    max_workers: int | None = None,
) -> list[dict[str, Any]]:
    from pipeline.core.accel import tts_local_workers
    from pipeline.core.jobs import set_job_context
    from pipeline.core.resources import adaptive_workers, progress_msg, run_with_adaptive_workers, workers_label
    from pipeline.tts.engines import vieneu as _vieneu

    pending = [
        {"i": i, "seg": seg, "text": str(seg.get("text") or " "), "wav": tts_dir / f"p{pi:02d}_{seg['id']}.wav"}
        for i, seg in enumerate(segs)
    ]
    if not pending:
        return []
    local = bool(_vieneu.parse_voice(voice))
    kind = "tts" if local else "network"
    if local:
        from pipeline.core.accel import _tts_vram_hard_cap
        hard = _tts_vram_hard_cap()
        w0 = tts_local_workers(max_workers, tasks=len(pending))
    else:
        hard = 24
        w0 = adaptive_workers(max_workers, kind="network", cap=hard, tasks=len(pending))
    cap = min(hard, max(1, int(max_workers or hard)), len(pending))
    _note(job_id, f"TTS phần {pi + 1}: {len(pending)} đoạn{workers_label(w0, kind=kind)}")

    def one(job: dict[str, Any]) -> tuple[dict[str, Any], float]:
        set_job_context(job_id)
        check_cancel(job_id)
        dur = tts_segment(job["text"], voice, job["wav"], None, "none", lang=lang)
        return job, float(dur or 0) or 2.5

    def prog(cur: int, total: int, w_now: int) -> None:
        if cur == total or cur % max(1, max(1, total // 5)) == 0:
            _note(job_id, progress_msg("TTS", cur, total, workers=w_now), stage=f"TTS {cur}/{total}")

    rows = run_with_adaptive_workers(
        pending,
        one,
        kind=kind,
        # Cap is the ceiling; requested=0 lets idle CPU/network raise concurrency.
        requested=0 if max_workers else max_workers,
        cap=cap,
        thread_name_prefix="rv-tts",
        on_progress=prog,
        cancel_check=lambda: check_cancel(job_id),
    )
    by_i: dict[int, tuple[dict[str, Any], float]] = {}
    for row in rows:
        if not row:
            continue
        job, dur = row
        by_i[int(job["i"])] = (job, dur)
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(segs):
        job, dur = by_i[i]
        out.append({**seg, "id": f"p{pi:02d}_{seg['id']}", "duration": dur, "audio": str(job["wav"])})
    return out
