"""run_dub orchestrator."""
from __future__ import annotations

import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from pipeline.asr import asr_paddleocr, asr_whisper
from pipeline.export.burn import cover_and_burn
from pipeline.core.config import PUBLIC_DATA
from pipeline.core.jobs import Cancelled, begin_job, check_cancel, clear_job, is_cancelled, short_cmd_error
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

_PREFER_VIDEO_DEFAULT_TTS_SPEED = 1.1


def _segment_playback(seg: dict[str, Any]) -> tuple[float, float]:
    """Giá trị editor lưu theo × và %, manager nhận × và 0–2."""
    try:
        speed = max(0.5, min(2.0, float(seg.get("ttsSpeed") or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    try:
        raw_volume = seg.get("ttsVolume")
        volume = max(
            0.0,
            min(2.0, float(100.0 if raw_volume is None else raw_volume) / 100.0),
        )
    except (TypeError, ValueError):
        volume = 1.0
    return speed, volume


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
            message=(
                "TTS… chuẩn bị (bỏ cache, gen lại)"
                if force_tts
                else "TTS… chuẩn bị luồng…"
            ),
            running=True,
        )
        default_voice = settings.get("defaultVoice", "system")
        # Một cache key có thể được nhiều segment dùng chung; chỉ synthesize 1 lần.
        jobs: dict[str, dict[str, Any]] = {}
        # Slot TTS = min(end-start, đến start câu sau) — fit đọc hết, ít đè
        # Chỉ none bỏ fit; preferVideo dùng phần video đã chậm rồi tăng TTS nếu vẫn tràn.
        soft_match = match == "none"
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
            # Non-verbal laughter is already present in the original audio. Cloud
            # CapCut intermittently reports TTSInvalidText for these tiny slots, so
            # do not let a sound effect abort the whole dubbing job.
            if re.fullmatch(r"(?i)\s*(?:ha[\s.!?,-]*){2,}\s*", str(text)):
                seg.pop("audioFile", None)
                seg.pop("audioUrl", None)
                seg.pop("audioDuration", None)
                continue
            # Never send untranslated CJK text to a Vietnamese CapCut voice. A
            # stale editor save can restore a source line after translation; that
            # must skip one cue, not abort the entire dubbing job.
            if lang == "vi" and re.search(r"[\u3400-\u9fff\uf900-\ufaff]", str(text)):
                seg.pop("audioFile", None)
                seg.pop("audioUrl", None)
                seg.pop("audioDuration", None)
                continue
            voice = inherit_voice(seg.get("voice"), default_voice)
            seg["voice"] = voice
            tts_speed, tts_volume = _segment_playback(seg)
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
                # Đảm bảo slot tối thiểu bằng thời lượng gốc (window) để tránh 
                # các câu đè nhau (overlap) bị bóp thời gian quá mức gây cắt cụt/đọc quá nhanh.
                target = max(0.15, max(window, next_start - start - 0.03))
            else:
                target = max(0.15, window)
            key = tts_cache_key(
                text,
                voice,
                lang,
                f"{match}|speed={tts_speed:.3f}|volume={tts_volume:.3f}",
            )
            name = f"{key}.wav"
            job = jobs.setdefault(
                key,
                {
                    "text": text,
                    "voice": voice,
                    "name": name,
                    "wav": root / "tts" / name,
                    "target": target,
                    "speed": tts_speed,
                    "volume": tts_volume,
                    "segments": [],
                },
            )
            # cùng text/voice: slot ngắn nhất
            job["target"] = min(float(job["target"]), target)
            job["segments"].append(seg)

        def synthesize(job: dict[str, Any]) -> float:
            from pipeline.core.jobs import set_job_context

            set_job_context(project_id)
            check_cancel(project_id)
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
                        speed=float(job["speed"]),
                        volume=float(job["volume"]),
                        cancel_check=lambda: is_cancelled(project_id),
                    )
                except Exception:
                    check_cancel(project_id)
                    return dur
            last: Exception | None = None
            for attempt in range(3):
                check_cancel(project_id)
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
                        speed=float(job["speed"]),
                        volume=float(job["volume"]),
                        cancel_check=lambda: is_cancelled(project_id),
                    )
                except Exception as exc:
                    check_cancel(project_id)
                    last = exc
                    wav.unlink(missing_ok=True)
                    if "TTSInvalidText" in str(exc):
                        break
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
            assert last is not None
            raise RuntimeError(f"TTS thất bại sau {attempt + 1} lần: {last}") from last

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
        # Auto elastic: rảnh duỗi / bận co (run_with_adaptive_workers)
        from pipeline.tts.engines import vieneu as _vieneu
        from pipeline.core.accel import tts_local_workers
        from pipeline.core.resources import progress_msg, run_with_adaptive_workers, workers_label

        sample_voice = str(
            next((j.get("voice") for j in pending if j.get("voice")), "") or ""
        )
        local_neural = bool(_vieneu.parse_voice(sample_voice)) or any(
            _vieneu.parse_voice(str(j.get("voice") or "")) for j in pending[:8]
        )
        w_kind = "tts" if local_neural else "network"
        if local_neural:
            from pipeline.core.accel import _tts_vram_hard_cap

            hard_cap = _tts_vram_hard_cap()
            workers0 = tts_local_workers(req or None, tasks=len(pending))
        else:
            hard_cap = 24
            workers0 = adaptive_workers(
                req if req > 0 else None, kind="network", cap=hard_cap, tasks=len(pending)
            )
        wbit = workers_label(workers0, kind=w_kind)
        set_status(
            project_id,
            step="dub",
            progress=8,
            message=progress_msg("TTS", 0, len(pending), workers=workers0, extra=f"{len(cached)} cache"),
            running=True,
        )

        def _tts_one(job: dict[str, Any]) -> tuple[dict[str, Any], float]:
            return job, float(synthesize(job))

        def _tts_prog(cur: int, total: int, w_now: int) -> None:
            set_status(
                project_id,
                step="dub",
                progress=int(5 + 90 * cur / max(1, total)),
                message=progress_msg("TTS", cur, total, workers=w_now, extra=f"{len(cached)} cache"),
                running=True,
            )

        try:
            rows = run_with_adaptive_workers(
                pending,
                _tts_one,
                kind=w_kind,
                requested=req if req > 0 else None,
                cap=min(hard_cap, max(1, len(pending))),
                thread_name_prefix="tts",
                on_progress=_tts_prog,
                cancel_check=lambda: check_cancel(project_id),
            )
        except Cancelled:
            raise
        for row in rows:
            if not row:
                continue
            job, dur = row
            for seg in job["segments"]:
                seg["audioFile"] = job["name"]
                seg["audioUrl"] = f"/api/projects/{project_id}/tts/{job['name']}"
                seg["audioDuration"] = dur
        # Fit contract: TTS phát TỰ NHIÊN trên clock hiện tại (ttsBake). Đổi bake
        # sau khi dub → playback scale theo bake/ttsBake (dubMath + mux_audio).
        from pipeline.core.media import meta_baked_speed

        bake_now = meta_baked_speed(meta)
        for seg in segments:
            if seg.get("audioFile") or float(seg.get("audioDuration") or 0) > 0.05:
                seg["ttsBake"] = bake_now
        # Flow «ưu tiên 0.8»: phân tích ở 0.8 rồi nâng timeline 1× → mọi khe co
        # 0.8. Giọng mặc định 1.10× — đều toàn bài, đỡ nén lệch
        # từng câu; user chỉnh khác 1× thì tôn trọng.
        # CHỈ khi dub trên đồng hồ đã nâng (bake≈1) — dub trước khi nâng thì
        # ttsBake tự lo phần ×1.25, không cộng mặc định khi dub trước lúc nâng.
        if match == "preferVideo" and abs(bake_now - 1.0) <= 0.02:
            for seg in segments:
                if seg.get("audioFile") and float(seg.get("ttsSpeed") or 1) == 1.0:
                    seg["ttsSpeed"] = _PREFER_VIDEO_DEFAULT_TTS_SPEED

        # Thước timeline bất khả xâm phạm: TTS dài hơn khe → NÉN AUDIO (atempo
        # ≤2×), không giãn video. Xuất = preview = đúng thời lượng nguồn.
        from pipeline.orchestrate.tts_fit import fit_tts_audio_to_slots

        n_stretch = fit_tts_audio_to_slots(segments, root, match=match, bake=bake_now)
        meta["segments"] = segments
        # Không ép chậm 0.80× lúc dub — chỉ stretch từng câu (videoSpeed) nếu cần
        if "videoSlowFactor" in meta:
            meta.pop("videoSlowFactor", None)
        save_meta(project_id, meta)
        if finalize:
            extra = f" · nén {n_stretch} câu cho vừa khe" if n_stretch else ""
            set_status(
                project_id,
                step="dub",
                progress=100,
                message=progress_msg("TTS xong", len(pending), workers=workers0, extra=extra.strip(" ·") or None),
                running=False,
            )
    except Cancelled:
        # All worker futures have stopped here; release the local neural model
        # instead of retaining RAM/VRAM after an explicit cancellation.
        try:
            from pipeline.tts.engines.vieneu import reset_client

            reset_client()
        except Exception:
            pass
        try:
            from pipeline.tts.engines.vieneu_frozen import shutdown_all_workers

            shutdown_all_workers()
        except Exception:
            pass
        set_status(
            project_id,
            step="dub",
            progress=0,
            message="Đã huỷ lồng tiếng",
            running=False,
            error="cancelled",
        )
        if nested:
            raise
        return
    except Exception as e:
        err = short_cmd_error(e) or type(e).__name__
        # Không ghi error code trần "dub" — UI hiện message
        if err.strip().lower() in ("dub", "error", "exception"):
            err = f"Lồng tiếng thất bại: {type(e).__name__}"
        try:
            from pipeline.core.app_log import append_exception

            append_exception(f"[dub:{project_id}] FAILED", e)
        except Exception:
            pass
        set_status(
            project_id,
            step="dub",
            progress=0,
            message=err,
            running=False,
            error=err,
        )
        # Không re-raise — desktop job thread không được lan exception
        if nested:
            raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)

