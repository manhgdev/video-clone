"""Pipeline orchestrators: ASR→translate, dub, export."""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .asr import asr_paddleocr, asr_whisper
from .export.burn import cover_and_burn
from .core.config import DATA
from .core.jobs import Cancelled, begin_job, check_cancel, clear_job
from .core.media import (
    encode_export_1080,
    ensure_preview_clip,
    extract_audio,
    ffprobe_duration,
    video_size,
)
from .export.mux import mux_dub, mux_original_audio, separate_no_vocals
from .core.project import (
    asr_cache_key,
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
from .core.resources import adaptive_workers
from .translate import translate_segments
from .tts import tts_cache_key, tts_segment

def run_pipeline(project_id: str, settings: dict[str, Any]) -> None:
    meta = load_meta(project_id)
    source = Path(meta["videoPath"])
    ensure_layout(project_id)
    job_gen = begin_job(project_id)
    source_fp = meta.get("sourceFp") or video_fingerprint(source)
    meta["sourceFp"] = source_fp
    preview_sec = max(0, int(settings.get("previewSec") or 0))
    tag = preview_tag(preview_sec)
    a_key = asr_cache_key(settings, source_fp)
    t_key = trans_cache_key(settings)
    cache = meta.get("cache") or {}

    try:
        if preview_sec > 0:
            set_status(
                project_id,
                step="asr",
                progress=3,
                message=f"Cắt preview {preview_sec}s…",
                running=True,
            )
            video = ensure_preview_clip(
                source,
                ensure_layout(project_id) / "cache" / f"preview_{preview_sec}.mp4",
                preview_sec,
                project_id,
            )
        else:
            video = source

        # —— ASR (reuse segments if same engine+lang+video+preview) ——
        if cache.get("asrKey") == a_key and meta.get("segments"):
            segments = meta["segments"]
            set_status(
                project_id,
                step="asr",
                progress=50,
                message=f"Cache ASR — {len(segments)} đoạn",
                running=True,
            )
        else:
            wav = cache_audio(project_id, tag)
            engine = settings.get("engine", "whisper")
            use_ocr = engine in ("paddleocr", "screen")
            if not use_ocr:
                if wav.exists() and wav.stat().st_mtime >= video.stat().st_mtime:
                    set_status(
                        project_id, step="asr", progress=8, message="Cache audio…", running=True
                    )
                else:
                    set_status(
                        project_id, step="asr", progress=5, message="Tách audio…", running=True
                    )
                    extract_audio(video, wav, project_id=project_id)

            segments = []
            frames_ok = any(cache_frames(project_id, tag).glob("*.jpg"))
            if use_ocr:
                set_status(
                    project_id,
                    step="asr",
                    progress=15,
                    message="OCR màn hình (phụ đề trên khung)…",
                    running=True,
                )
                segments = asr_paddleocr(
                    video,
                    project_id,
                    reuse_frames=frames_ok,
                    tag=tag,
                    workers=int(settings.get("workers") or 0),
                    source_lang=str(settings.get("sourceLang") or "auto"),
                )
                if not segments:
                    raise RuntimeError(
                        "Không đọc được chữ trên màn hình. "
                        "Kiểm tra video có phụ đề cứng, hoặc tăng Preview rồi chạy lại. "
                        "Hoặc đổi Nhận dạng → Giọng nói (Whisper)."
                    )
            else:
                w = adaptive_workers(
                    int(settings.get("workers") or 0), kind="cpu", cap=16
                )
                set_status(
                    project_id,
                    step="asr",
                    progress=20,
                    message=f"Whisper ASR ({w} luồng)…",
                    running=True,
                )
                check_cancel(project_id)
                segments = asr_whisper(
                    wav,
                    settings.get("sourceLang", "auto"),
                    workers=w,
                    project_id=project_id,
                )

            if not segments:
                raise RuntimeError("Không nhận được đoạn thoại nào từ video.")

            # giữ bản dịch cũ chỉ khi cùng đúng dòng chữ nguồn
            prev_tr = {
                s["source"]: s["translation"]
                for s in (meta.get("segments") or [])
                if s.get("source") and s.get("translation")
            }
            for seg in segments:
                if not seg.get("translation") and seg["source"] in prev_tr:
                    seg["translation"] = prev_tr[seg["source"]]

            cache_asr_path(project_id).write_text(
                json.dumps({"key": a_key, "segments": segments}, ensure_ascii=False),
                encoding="utf-8",
            )
            cache["asrKey"] = a_key
            if cache.get("transKey") != t_key:
                cache.pop("transKey", None)

        # —— Translate ——
        voice = settings.get("defaultVoice", "system")
        if cache.get("transKey") == t_key and all((s.get("translation") or "").strip() for s in segments):
            set_status(
                project_id,
                step="translate",
                progress=90,
                message=f"Cache dịch — {len(segments)} đoạn",
                running=True,
            )
            for seg in segments:
                seg["voice"] = inherit_voice(seg.get("voice"), voice)
        else:
            if cache.get("transKey") != t_key:
                need_idx = list(range(len(segments)))
            else:
                need_idx = [
                    i for i, s in enumerate(segments) if not (s.get("translation") or "").strip()
                ]

            set_status(
                project_id,
                step="translate",
                progress=55,
                message=(
                    f"Giữ chữ nguồn {len(need_idx)}/{len(segments)} đoạn…"
                    if settings.get("targetLang") in ("none", "off", "source", "")
                    else f"Dịch {len(need_idx)}/{len(segments)} đoạn…"
                ),
                running=True,
            )
            if need_idx:
                texts = [segments[i]["source"] for i in need_idx]
                target = settings.get("targetLang", "vi")
                source = settings.get("sourceLang", "auto")
                check_cancel(project_id)
                if target in ("none", "off", "source", ""):
                    # Không dịch → không chèn caption (để trống, không copy source)
                    translations = [""] * len(texts)
                else:
                    w = adaptive_workers(
                        int(settings.get("workers") or 0),
                        kind="network",
                        cap=16,
                        tasks=len(texts),
                    )
                    translations = translate_segments(
                        texts,
                        target,
                        project_id=project_id,
                        source_lang=source,
                        translator=str(settings.get("translator") or "google"),
                        workers=w,
                    )
                for i, tr in zip(need_idx, translations):
                    segments[i]["translation"] = tr
            for seg in segments:
                seg["voice"] = inherit_voice(seg.get("voice"), voice)
            cache["transKey"] = t_key

        meta["segments"] = segments
        # ô Preview trên UI giữ số >0; lần chạy full (0) không được ghi đè thành 0
        ui_prev = max(0, int(settings.get("previewSec") or 0))
        if preview_sec <= 0:
            prev_ui = max(0, int((meta.get("settings") or {}).get("previewSec") or 0))
            ui_prev = prev_ui if prev_ui > 0 else (ui_prev if ui_prev > 0 else 20)
        settings = {**settings, "previewSec": ui_prev if preview_sec <= 0 else preview_sec}
        meta["settings"] = settings
        meta["cache"] = cache
        meta["previewSec"] = preview_sec
        # clip thật sự đã ASR/dịch — xuất phải dùng đúng file này
        meta["workVideo"] = str(video.resolve())
        save_meta(project_id, meta)
        hint = f"Preview {preview_sec}s — " if preview_sec > 0 else ""
        engine = settings.get("engine", "whisper")
        no_tr = str(settings.get("targetLang") or "") in ("none", "off", "source", "")
        if no_tr:
            next_msg = f"{hint}Xong {len(segments)} đoạn — không dịch, không chèn caption"
        elif engine in ("paddleocr", "screen"):
            next_msg = f"{hint}Xong {len(segments)} đoạn — bấm Xuất bản để che chữ cũ + đè bản dịch"
        else:
            next_msg = f"{hint}Xong {len(segments)} đoạn — tiếp theo: Lồng tiếng → Xuất bản"
        set_status(
            project_id,
            step="translate",
            progress=100,
            message=next_msg,
            running=False,
        )
    except Cancelled:
        set_status(
            project_id,
            step="translate",
            progress=0,
            message="Đã huỷ",
            running=False,
            error="cancelled",
        )
    except Exception as e:
        set_status(
            project_id,
            step="translate",
            progress=0,
            message=str(e),
            running=False,
            error=str(e),
        )
        raise
    finally:
        clear_job(project_id, job_gen)


def run_dub(project_id: str, *, finalize: bool = True, nested: bool = False) -> None:
    meta = load_meta(project_id)
    segments = meta.get("segments") or []
    settings = meta.get("settings") or {}
    match = settings.get("matchDuration", "natural")
    lang = settings.get("targetLang", "vi")
    root = ensure_layout(project_id)
    job_gen: int | None = None
    if not nested:
        job_gen = begin_job(project_id)
    try:
        set_status(project_id, step="dub", progress=5, message="TTS…", running=True)
        default_voice = settings.get("defaultVoice", "system")
        # Một cache key có thể được nhiều segment dùng chung; chỉ synthesize 1 lần.
        jobs: dict[str, dict[str, Any]] = {}
        # Slot TTS = min(end-start, đến start câu sau) — fit đọc hết, ít đè
        ordered = sorted(segments, key=lambda s: float(s.get("start") or 0))
        for i, seg in enumerate(ordered):
            # Title dọc / nhãn: mặc định không TTS; bật lại qua seg.dub=True
            lay = str(seg.get("layout") or "")
            if "dub" in seg:
                want_dub = bool(seg.get("dub"))
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
            target = float(job["target"]) if match != "none" else None
            # Cache cũ dài hơn slot → fit lại (đọc hết + không đè)
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
                        match if match != "none" else "natural",
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
        meta["segments"] = segments
        save_meta(project_id, meta)
        if finalize:
            set_status(project_id, step="dub", progress=100, message="Lồng tiếng xong", running=False)
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
        set_status(project_id, step="dub", progress=0, message=str(e), running=False, error=str(e))
        raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)


def export_source_video(project_id: str, meta: dict[str, Any]) -> tuple[Path, int]:
    """Clip xuất = đúng độ dài lần dịch (meta.previewSec), không lấy nhầm source full."""
    source = Path(meta["videoPath"]).resolve()
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    if preview_sec > 0:
        clip = ensure_preview_clip(
            source,
            ensure_layout(project_id) / "cache" / f"preview_{preview_sec}.mp4",
            preview_sec,
            project_id,
        )
        return clip, preview_sec
    return source, 0


def run_export(project_id: str, *, nested: bool = False) -> Path:
    meta = load_meta(project_id)
    video, preview_sec = export_source_video(project_id, meta)
    # chỉ giữ cue trong độ dài clip (tránh đoạn full khi nhầm cache)
    vid_dur = ffprobe_duration(video) or 1e9
    segments = [
        dict(s)
        for s in (meta.get("segments") or [])
        if float(s.get("start") or 0) < vid_dur - 0.05
    ]
    for s in segments:
        if float(s.get("end") or 0) > vid_dur:
            s["end"] = vid_dur
    settings = meta.get("settings") or {}
    root = ensure_layout(project_id)
    out = out_final(project_id)
    job_gen: int | None = None
    if not nested:
        job_gen = begin_job(project_id)

    # cover / burn độc lập; "Không dịch" → không chèn caption
    no_translate = str(settings.get("targetLang") or "") in ("none", "off", "source", "")
    cover = bool(settings.get("coverHardsubs", False))
    burn = bool(settings.get("burnSubs", True)) and not no_translate

    try:
        hint = f"preview {preview_sec}s — " if preview_sec > 0 else ""
        place = str(settings.get("captionPlacement") or "below").lower()
        if cover and burn:
            msg = f"{hint}Che chữ cũ + chèn bản dịch…"
        elif burn and place == "above":
            msg = f"{hint}Chèn bản dịch phía trên…"
        elif burn:
            msg = f"{hint}Chèn bản dịch phía dưới…"
        elif cover:
            msg = f"{hint}Che chữ cũ…"
        else:
            msg = f"{hint}Xuất video…"
        set_status(
            project_id,
            step="export",
            progress=20,
            message=msg,
            running=True,
        )
        burned = out_burned(project_id)
        if cover or burn:
            place = str(settings.get("captionPlacement") or "below").lower()
            if place not in ("below", "above"):
                place = "below"
            cover_and_burn(
                video,
                segments,
                burned,
                cover=cover,
                burn=burn,
                subtitle_font_size=int(settings.get("subtitleFontSize", 0)),
                project_id=project_id,
                workers=int(settings.get("workers") or 0),
                caption_placement=place,
            )
        else:
            # Không burn/cover — remux bỏ metadata (không copy2 nguyên file nguồn)
            from .core.jobs import run_cmd

            run_cmd(
                project_id,
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video),
                    "-map_metadata",
                    "-1",
                    "-map_chapters",
                    "-1",
                    "-c",
                    "copy",
                    str(burned),
                ],
            )

        has_tts = any(
            (root / "tts" / (s.get("audioFile") or f"{s['id']}.wav")).exists() for s in segments
        )
        audio_mode = (
            str(settings.get("originalAudioMode") or "original")
            if settings.get("processOriginalAudio")
            else "auto"
        )
        # Giá trị "music" từ UI cũ được nâng cấp thành tách AI no_vocals.
        if audio_mode == "music":
            audio_mode = "no_vocals"
        source_audio = None
        if audio_mode == "no_vocals":
            set_status(
                project_id,
                step="export",
                progress=65,
                message=f"{hint}AI đang xóa lời, giữ nhạc và hiệu ứng…",
                running=True,
            )
            check_cancel(project_id)
            # Audio không đổi khi burn phụ đề; dùng source video để cache stem
            # ổn định giữa các lần chỉnh chữ và xuất lại.
            source_audio = separate_no_vocals(project_id, video)
        if has_tts:
            set_status(
                project_id,
                step="export",
                progress=70,
                message=f"{hint}Ghép audio lồng tiếng…",
                running=True,
            )
            check_cancel(project_id)
            bg_vol = max(0, min(100, int(settings.get("originalAudioVolume") or 100))) / 100.0
            mux_dub(
                project_id,
                burned,
                segments,
                original_audio_mode="original" if source_audio else audio_mode,
                source_audio=source_audio,
                original_audio_volume=bg_vol,
            )
        elif audio_mode != "auto":
            audio_labels = {
                "original": "Giữ âm thanh gốc",
                "vocals": "Tách lời khỏi âm thanh gốc",
                "no_vocals": "Xóa lời, giữ nhạc và hiệu ứng",
                "mute": "Tắt âm thanh gốc",
            }
            set_status(
                project_id,
                step="export",
                progress=70,
                message=f"{hint}{audio_labels.get(audio_mode, 'Lọc âm thanh gốc')}…",
                running=True,
            )
            check_cancel(project_id)
            bg_vol = max(0, min(100, int(settings.get("originalAudioVolume") or 100))) / 100.0
            mux_original_audio(
                project_id,
                burned,
                "original" if source_audio else audio_mode,
                source_audio=source_audio,
                original_audio_volume=bg_vol,
            )
        else:
            shutil.copy2(burned, out)

        # Chuẩn hóa 1080p + encode chất lượng (mọi nhánh)
        set_status(
            project_id,
            step="export",
            progress=92,
            message=f"{hint}Encode 1080p…",
            running=True,
        )
        check_cancel(project_id)
        encode_export_1080(out, out, project_id=project_id)

        # bản dễ tìm: server/data/exports/<id>.mp4
        exports = DATA / "exports"
        exports.mkdir(exist_ok=True)
        easy = exports / f"{project_id}.mp4"
        shutil.copy2(out, easy)

        out_dur = ffprobe_duration(out)
        ow, oh = video_size(out)
        rel = f"server/data/{project_id}/out/final.mp4"
        easy_rel = f"server/data/exports/{project_id}.mp4"
        meta["outputPath"] = str(out.resolve())
        meta["outputRel"] = rel
        meta["exportCopy"] = easy_rel
        meta["exportSize"] = f"{ow}x{oh}"
        save_meta(project_id, meta)
        if preview_sec > 0:
            done = f"Xong preview {preview_sec}s · {ow}×{oh} ({out_dur:.1f}s) — {easy_rel}"
        else:
            done = f"Xong full · {ow}×{oh} ({out_dur:.1f}s) — {easy_rel}"
        set_status(
            project_id,
            step="export",
            progress=100,
            message=done,
            running=False,
            outputRel=easy_rel,
            outputPath=str(out.resolve()),
            error=None,
        )
        return out
    except Cancelled:
        # giữ step export — đừng nhảy về Video (UI trông như reset lỗi)
        set_status(
            project_id,
            step="export",
            progress=0,
            message="Đã huỷ xuất bản",
            running=False,
            error="cancelled",
        )
        raise
    except Exception as e:
        set_status(project_id, step="export", progress=0, message=str(e), running=False, error=str(e))
        raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)
