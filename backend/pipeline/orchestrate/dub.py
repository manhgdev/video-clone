"""run_dub orchestrator."""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from pipeline.asr import asr_paddleocr, asr_whisper
from pipeline.export.burn import cover_and_burn
from pipeline.core.config import PUBLIC_DATA
from pipeline.core.jobs import Cancelled, begin_job, check_cancel, clear_job, short_cmd_error
from pipeline.core.media import (
    crop_export_aspect,
    encode_export_1080,

    ensure_preview_clip,
    extract_audio,
    ffprobe_duration,
    retime_video_segments,
    video_size,
)
from pipeline.export.mux import mux_dub, mux_original_audio, separate_no_vocals
from pipeline.core.project import (
    asr_cache_key,
    audio_cache_tag,
    cache_asr_path,
    cache_audio,
    cache_frames,
    ensure_layout,
    inherit_voice,
    load_meta,
    out_burned,
    out_final,
    preview_tag,
    save_meta,
    set_status,
    trans_cache_key,
    video_fingerprint,
)
from pipeline.core.resources import adaptive_workers
from pipeline.ocr.locate import attach_speech_hardsub_boxes
from pipeline.translate import translate_segments
from pipeline.tts import tts_cache_key, tts_segment

from pipeline.orchestrate.tts_fit import assign_tts_fit_speeds

def run_dub(project_id: str, *, finalize: bool = True, nested: bool = False) -> None:
    meta = load_meta(project_id)
    segments = meta.get("segments") or []
    settings = meta.get("settings") or {}
    match = settings.get("matchDuration", "preferVideo")
    lang = settings.get("targetLang", "vi")
    root = ensure_layout(project_id)
    job_gen: int | None = None
    if not nested:
        job_gen = begin_job(project_id)
    try:
        force_tts = bool(meta.pop("forceTts", False) or settings.get("forceTts"))
        set_status(
            project_id,
            step="dub",
            progress=5,
            message="TTS… (bỏ cache, gen lại)" if force_tts else "TTS…",
            running=True,
        )
        default_voice = settings.get("defaultVoice", "system")
        # Một cache key có thể được nhiều segment dùng chung; chỉ synthesize 1 lần.
        jobs: dict[str, dict[str, Any]] = {}
        # Slot TTS = min(end-start, đến start câu sau) — fit đọc hết, ít đè
        # preferVideo/none: không ép atempo theo slot
        soft_match = match in ("none", "preferVideo")
        ordered = sorted(segments, key=lambda s: float(s.get("start") or 0))
        if force_tts:
            # Xóa file wav/mp3 TTS cũ — tránh preview lệch timeline mới
            tts_dir = root / "tts"
            if tts_dir.is_dir():
                for f in tts_dir.glob("*"):
                    if f.suffix.lower() in (".wav", ".mp3", ".aiff"):
                        try:
                            f.unlink(missing_ok=True)
                        except OSError:
                            pass
            for seg in ordered:
                seg.pop("audioFile", None)
                seg.pop("audioUrl", None)
                seg.pop("audioDuration", None)
                seg.pop("videoSpeed", None)
        for i, seg in enumerate(ordered):
            # Title dọc / nhãn: mặc định không TTS; bật lại qua seg.dub=True
            # dub=null / thiếu key → theo layout (không coi null là False)
            lay = str(seg.get("layout") or "")
            dub_flag = seg.get("dub")
            if dub_flag is True:
                want_dub = True
            elif dub_flag is False:
                want_dub = False
            else:
                want_dub = lay not in ("vertical", "label")
            if not want_dub:
                seg.pop("audioFile", None)
                seg.pop("audioUrl", None)
                seg.pop("audioDuration", None)
                continue
            text = seg.get("translation") or seg.get("source") or "."
            voice = inherit_voice(seg.get("voice"), default_voice)
            seg["voice"] = voice
            start = float(seg.get("start") or 0)
            end = float(seg.get("end") or start)
            window = max(0.12, end - start)
            next_start = None
            for j in range(i + 1, len(ordered)):
                ns = float(ordered[j].get("start") or 0)
                if ns > start + 0.02:
                    next_start = ns
                    break
            if next_start is not None:
                # Slot cứng = đến câu sau (không dùng window*0.7 — gây tràn/cắt)
                target = max(0.15, next_start - start - 0.03)
            else:
                target = max(0.15, window)
            key = tts_cache_key(text, voice, lang, match)
            name = f"{key}.wav"
            job = jobs.setdefault(
                key,
                {
                    "text": text,
                    "voice": voice,
                    "name": name,
                    "wav": root / "tts" / name,
                    "target": target,
                    "segments": [],
                },
            )
            # cùng text/voice: slot ngắn nhất
            job["target"] = min(float(job["target"]), target)
            job["segments"].append(seg)

        def synthesize(job: dict[str, Any]) -> float:
            wav: Path = job["wav"]
            target = None if soft_match else float(job["target"])
            # Cache cũ dài hơn slot → fit lại (chỉ natural/stretch)
            if wav.exists() and wav.stat().st_size > 128:
                dur = ffprobe_duration(wav)
                if target is None or dur <= target * 1.06:
                    return dur
                try:
                    return tts_segment(
                        job["text"],
                        job["voice"],
                        wav,
                        target,
                        match,
                        lang=lang,
                        force_refit=True,
                    )
                except Exception:
                    return dur
            last: Exception | None = None
            for attempt in range(3):
                try:
                    wav.unlink(missing_ok=True)
                    wav.with_suffix(".mp3").unlink(missing_ok=True)
                    return tts_segment(
                        job["text"],
                        job["voice"],
                        wav,
                        target,
                        match,
                        lang=lang,
                    )
                except Exception as exc:
                    last = exc
                    wav.unlink(missing_ok=True)
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
            assert last is not None
            raise RuntimeError(f"TTS thất bại sau 3 lần: {last}") from last

        pending = list(jobs.values())
        if not pending:
            spoken = sum(
                1
                for s in segments
                if str(s.get("layout") or "") not in ("vertical", "label")
                and (s.get("translation") or s.get("source") or "").strip()
            )
            msg = (
                "Không có đoạn nào bật lồng tiếng — kiểm tra track Dub / layout"
                if spoken
                else "Không có đoạn để lồng tiếng"
            )
            meta["segments"] = segments
            save_meta(project_id, meta)
            if finalize:
                set_status(
                    project_id,
                    step="dub",
                    progress=100,
                    message=msg,
                    running=False,
                )
            return

        cached = [j for j in pending if j["wav"].exists() and j["wav"].stat().st_size > 128]
        req = int(settings.get("workers") or 0)
        workers = adaptive_workers(
            req, kind="network", cap=16, tasks=len(pending)
        )
        completed = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tts") as pool:
            future_jobs = {pool.submit(synthesize, job): job for job in pending}
            for future in as_completed(future_jobs):
                check_cancel(project_id)
                job = future_jobs[future]
                dur = future.result()
                for seg in job["segments"]:
                    seg["audioFile"] = job["name"]
                    seg["audioUrl"] = f"/api/projects/{project_id}/tts/{job['name']}"
                    seg["audioDuration"] = dur
                completed += 1
                set_status(
                    project_id,
                    step="dub",
                    progress=int(5 + 90 * completed / max(1, len(pending))),
                    message=(
                        f"TTS song song {completed}/{len(pending)}"
                        f" · {len(cached)} cache · {workers} luồng"
                    ),
                    running=True,
                )
        # TTS dài hơn cửa sổ câu → videoSpeed < 1 (kéo dài span, đẩy câu sau).
        # Export gọi retime_video_segments — không cascade cắt audio.
        n_stretch = assign_tts_fit_speeds(segments, match=match)
        meta["segments"] = segments
        # Không ép chậm 0.80× lúc dub — chỉ stretch từng câu (videoSpeed) nếu cần
        if "videoSlowFactor" in meta:
            meta.pop("videoSlowFactor", None)
        save_meta(project_id, meta)
        if finalize:
            extra = f" · giãn {n_stretch} câu" if n_stretch else ""
            set_status(
                project_id,
                step="dub",
                progress=100,
                message=f"Lồng tiếng xong · {len(pending)} đoạn{extra}",
                running=False,
            )
    except Cancelled:
        set_status(
            project_id,
            step="dub",
            progress=0,
            message="Đã huỷ lồng tiếng",
            running=False,
            error="cancelled",
        )
        raise
    except Exception as e:
        err = short_cmd_error(e)
        set_status(project_id, step="dub", progress=0, message=err, running=False, error=err)
        raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)

