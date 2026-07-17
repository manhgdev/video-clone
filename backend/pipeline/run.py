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
from .core.config import PUBLIC_DATA
from .core.jobs import Cancelled, begin_job, check_cancel, clear_job
from .core.media import (
    crop_export_aspect,
    encode_export_1080,
    ensure_playback_speed,
    ensure_preview_clip,
    extract_audio,
    ffprobe_duration,
    retime_video_segments,
    video_size,
)
from .export.mux import mux_dub, mux_original_audio, separate_no_vocals
from .core.project import (
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
from .core.resources import adaptive_workers
from .ocr.locate import attach_speech_hardsub_boxes
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

        # preferVideo: bake chậm 0.80× TRƯỚC ASR — timeline/chữ/TTS cùng nhịp file thật
        match_mode = str(settings.get("matchDuration") or "preferVideo")
        if match_mode == "preferVideo":
            from .export.mux import PREFER_VIDEO_SPEED

            set_status(
                project_id,
                step="asr",
                progress=5,
                message="Khớp thời lượng: chậm video 0.80×…",
                running=True,
            )
            cache_dir = ensure_layout(project_id) / "cache"
            if preview_sec > 0:
                slow_dest = cache_dir / f"preview_{preview_sec}_s080.mp4"
            else:
                slow_dest = cache_dir / "source_s080.mp4"
            video = ensure_playback_speed(
                video, slow_dest, PREFER_VIDEO_SPEED, project_id=project_id
            )
            meta["bakedPreferVideo"] = True
            meta["bakedSpeed"] = float(PREFER_VIDEO_SPEED)
            meta["workDuration"] = float(ffprobe_duration(video) or 0)
        else:
            meta.pop("bakedPreferVideo", None)
            meta["bakedSpeed"] = 1.0
            meta.pop("workDuration", None)

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
            wav = cache_audio(project_id, audio_cache_tag(preview_sec, match_mode))
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
                    message=f"Whisper ASR ({w} luồng) — % có thể đứng lâu, vẫn chạy…",
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

            # giữ bản dịch + chỉnh preview (bbox, font…) khi cùng dòng chữ nguồn
            prev_by_source: dict[str, dict[str, Any]] = {}
            for s in meta.get("segments") or []:
                src = (s.get("source") or "").strip()
                if src:
                    prev_by_source[src] = s
            for seg in segments:
                old = prev_by_source.get((seg.get("source") or "").strip())
                if not seg.get("translation") and old and old.get("translation"):
                    seg["translation"] = old["translation"]
                if old:
                    # Whisper: đừng kế thừa bbox đáy bake; chỉ giữ mid/vertical/label đã OCR
                    old_lay = str(old.get("layout") or "")
                    if old.get("bbox") and (
                        use_ocr or old_lay in ("mid", "vertical", "label")
                    ):
                        seg["bbox"] = old["bbox"]
                        if old_lay in ("vertical", "label"):
                            seg["layout"] = old_lay
                        else:
                            # suy mid/horizontal từ cy bbox — không giữ layout trống
                            from .ocr.locate import _retag_layout_from_bbox

                            # fh tạm: giữ layout cũ nếu mid, else retag sau khi biết video size
                            if old_lay == "mid":
                                seg["layout"] = "mid"
                            else:
                                bb = old["bbox"]
                                try:
                                    cy = float(bb["y"]) + float(bb["h"]) * 0.5
                                    # giả định khung dọc phổ biến; attach sẽ retag chính xác
                                    seg["layout"] = (
                                        "mid" if 1920 * 0.18 < cy < 1920 * 0.78 else "horizontal"
                                    )
                                except (KeyError, TypeError, ValueError):
                                    seg["layout"] = old_lay or "horizontal"
                    for k in ("fontSize", "videoSpeed", "ttsVolume", "ttsSpeed"):
                        if old.get(k) is not None:
                            seg[k] = old[k]

            cache_asr_path(project_id).write_text(
                json.dumps({"key": a_key, "segments": segments}, ensure_ascii=False),
                encoding="utf-8",
            )
            cache["asrKey"] = a_key
            if cache.get("transKey") != t_key:
                cache.pop("transKey", None)

        # Preserve Whisper's sentence boundaries. The old CJK fragment merger
        # joined any short, adjacent segments, including complete sentences,
        # so translation received fewer and much longer captions than ASR made.

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
                    # Giữ nguyên chữ nguồn — không gọi máy dịch
                    translations = list(texts)
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

        # Whisper: speech timing ≠ vị trí chữ — OCR gắn bbox/layout (mid/đáy)
        # Cần khi burnSubs (kéo mid/dọc đúng chỗ) — không chỉ khi coverHardsubs.
        engine = settings.get("engine", "whisper")
        if (
            engine not in ("paddleocr", "screen")
            and bool(settings.get("burnSubs", True))
            and segments
        ):
            set_status(
                project_id,
                step="translate",
                progress=95,
                message="Định vị caption trên khung (OCR)…",
                running=True,
            )
            n_box = attach_speech_hardsub_boxes(
                video, segments, only_missing=True, project_id=project_id
            )
            if n_box:
                set_status(
                    project_id,
                    step="translate",
                    progress=97,
                    message=f"Đã gắn vị trí {n_box}/{len(segments)} câu…",
                    running=True,
                )

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
            error=None,
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


def _assign_tts_fit_speeds(
    segments: list[dict[str, Any]],
    *,
    match: str,
) -> int:
    """TTS dài hơn khe timeline → videoSpeed < 1: kéo dài span câu, đẩy trước/sau.

    retime: out_span = (end-start)/speed; gap sau end giữ 1×; câu sau map_time muộn hơn.
    stretch mode: không gán (khớp bằng atempo TTS).
    """
    if match == "stretch":
        for seg in segments:
            seg.pop("videoSpeed", None)
        return 0

    soft = 1.03
    min_speed = 0.35  # chậm tối đa ~2.86×
    ordered = sorted(segments, key=lambda s: float(s.get("start") or 0))
    n = 0
    for i, seg in enumerate(ordered):
        ad = float(seg.get("audioDuration") or 0)
        if ad <= 0.08:
            seg.pop("videoSpeed", None)
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        window = max(0.12, end - start)
        next_start = None
        for j in range(i + 1, len(ordered)):
            ns = float(ordered[j].get("start") or 0)
            if ns > start + 0.02:
                next_start = ns
                break
        # Câu cuối: không có khe sau → gap_after=0 (trước gán 1e9 → không bao giờ giãn)
        gap_after = max(0.0, next_start - end) if next_start is not None else 0.0
        need_speech = max(0.12, ad - gap_after + 0.05)
        if need_speech <= window * soft:
            seg.pop("videoSpeed", None)
            continue
        speed = max(min_speed, min(1.0, window / need_speech))
        speed = round(speed, 3)
        if speed >= 0.995:
            seg.pop("videoSpeed", None)
            continue
        if next_start is not None and window / speed + gap_after < ad * 0.98:
            extra = min(gap_after * 0.85, max(0.0, ad - window / min_speed))
            if extra > 0.05:
                new_end = min(next_start - 0.02, end + extra)
                if new_end > end + 0.04:
                    seg["end"] = round(new_end, 3)
                    window = max(0.12, new_end - start)
                    gap_after = max(0.0, next_start - new_end)
                    need_speech = max(0.12, ad - gap_after + 0.05)
                    speed = max(min_speed, min(1.0, window / need_speech))
                    speed = round(speed, 3)
        if speed >= 0.995:
            seg.pop("videoSpeed", None)
            continue
        seg["videoSpeed"] = speed
        n += 1
    return n


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
        n_stretch = _assign_tts_fit_speeds(segments, match=match)
        meta["segments"] = segments
        if match == "preferVideo":
            from .export.mux import PREFER_VIDEO_FACTOR

            if meta.get("bakedPreferVideo"):
                # bake 0.80× toàn cục đã xong; chỉ còn stretch từng câu (videoSpeed)
                meta.pop("videoSlowFactor", None)
            else:
                meta["videoSlowFactor"] = round(PREFER_VIDEO_FACTOR, 4)
        elif "videoSlowFactor" in meta:
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
        set_status(project_id, step="dub", progress=0, message=str(e), running=False, error=str(e))
        raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)


def export_source_video(project_id: str, meta: dict[str, Any]) -> tuple[Path, int]:
    """Clip xuất = đúng độ dài lần dịch (meta.previewSec), không lấy nhầm source full."""
    source = Path(meta["videoPath"]).resolve()
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    # preferVideo đã bake → dùng đúng workVideo (ASR/che chữ cùng timeline)
    if meta.get("bakedPreferVideo"):
        work = Path(str(meta.get("workVideo") or ""))
        if work.is_file():
            return work, preview_sec
        cache = ensure_layout(project_id) / "cache"
        if preview_sec > 0:
            slow = cache / f"preview_{preview_sec}_s080.mp4"
            if slow.is_file():
                return slow, preview_sec
        slow_full = cache / "source_s080.mp4"
        if slow_full.is_file():
            return slow_full, preview_sec
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
    # Free text + effect regions (làm mờ tự do): cùng hệ tọa độ pixel.
    text_overlays: list[dict[str, Any]] = []
    for item in meta.get("overlays") or []:
        if float(item.get("start") or 0) >= vid_dur:
            continue
        kind = str(item.get("kind") or "text").lower()
        x = float(item.get("x") or 0)
        y = float(item.get("y") or 0)
        w = float(item.get("w") or 0)
        h = float(item.get("h") or 0)
        if w < 4 or h < 4:
            continue
        st = float(item.get("start") or 0)
        en = float(item.get("end") or 0)
        if kind == "effect":
            # Vùng hiệu ứng: chỉ mask, không chữ
            text_overlays.append(
                {
                    "id": f"fx-{item.get('id', '')}",
                    "start": st,
                    "end": en,
                    "coverStart": st,
                    "coverEnd": en,
                    "translation": "",
                    "source": "",
                    "layout": "horizontal",
                    "bbox": {"x": x, "y": y, "w": w, "h": h},
                    "maskOnly": True,
                    "skipCoverMask": False,
                    "coverMaskStyle": str(item.get("maskStyle") or "blur"),
                    "coverMaskColor": str(item.get("maskColor") or "#4c1d95"),
                    "coverMaskOpacity": int(item.get("maskOpacity") if item.get("maskOpacity") is not None else 40),
                }
            )
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        fs = int(item.get("fontSize") or 42)
        lines = [ln if ln.strip() else " " for ln in text.splitlines()] or [text]
        text_overlays.append(
            {
                "id": f"overlay-{item.get('id', '')}",
                "start": st,
                "end": en,
                "translation": text,
                "source": "",
                "layout": "horizontal",
                "fontSize": fs,
                "textColor": str(item.get("color") or "#ffffff"),
                "bbox": {"x": x, "y": y, "w": w, "h": h},
                "captionLayout": {
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "lines": lines,
                    "fontSize": fs,
                },
                # Preview không blur dưới free-text — không mask khi burn
                "skipCoverMask": True,
            }
        )
    settings = meta.get("settings") or {}
    root = ensure_layout(project_id)
    match_mode = str(settings.get("matchDuration") or "preferVideo")
    baked_prefer = bool(meta.get("bakedPreferVideo"))
    # preferVideo đã bake vào file → không setpts thêm lúc mux
    # stretch: khớp TTS theo slot — không chậm video
    prefer_global_slow = (
        match_mode in ("preferVideo", "none", "natural") and not baked_prefer
    )
    manual_video_speed = any(
        abs(float(segment.get("videoSpeed") or 1) - 1.0) > 0.001
        for segment in segments
    )
    out = out_final(project_id)
    job_gen: int | None = None
    if not nested:
        job_gen = begin_job(project_id)

    # cover / burn độc lập; "Không dịch" → không chèn caption
    no_translate = str(settings.get("targetLang") or "") in ("none", "off", "source", "")
    cover = bool(settings.get("coverHardsubs", True))
    burn = bool(settings.get("burnSubs", True)) and not no_translate

    try:
        hint = f"preview {preview_sec}s — " if preview_sec > 0 else ""
        # TTS dài hơn slot → videoSpeed < 1: kéo dài span câu + đẩy timeline sau
        # (preferVideo bake 0.80× vẫn retime thêm từng câu khi cần)
        if manual_video_speed and match_mode != "stretch":
            set_status(
                project_id,
                step="export",
                progress=10,
                message=f"{hint}Giãn timeline khớp lồng tiếng…",
                running=True,
            )
            video, segments = retime_video_segments(
                video, segments, root / "cache", project_id
            )
            # timeline đã map — mux chỉ đặt TTS full, không cascade cắt
            meta["segments"] = segments
            save_meta(project_id, meta)
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
        if cover or burn or text_overlays:
            place = str(settings.get("captionPlacement") or "below").lower()
            if place not in ("below", "above"):
                place = "below"
            cover_and_burn(
                video,
                segments + text_overlays,
                burned,
                cover=cover,
                burn=burn or bool(text_overlays),
                subtitle_font_size=int(settings.get("subtitleFontSize", 0)),
                project_id=project_id,
                workers=int(settings.get("workers") or 0),
                caption_placement=place,
                cover_mask_style=str(settings.get("coverMaskStyle") or "blur"),
                cover_mask_color=str(settings.get("coverMaskColor") or "#4c1d95"),
                cover_mask_opacity=int(settings.get("coverMaskOpacity", 40)),
                caption_text_color=str(settings.get("captionTextColor") or "#ffffff"),
                caption_bg_style=str(settings.get("captionBgStyle") or "none"),
                caption_bg_color=str(settings.get("captionBgColor") or "#000000"),
                caption_bg_opacity=int(settings.get("captionBgOpacity", 55)),
                caption_stroke=bool(settings.get("captionStroke", True)),
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
            # Stem từ videoPath gốc (không phải clip preview) — cùng cache với xem trước.
            source_audio = separate_no_vocals(
                project_id,
                # Dùng đúng clip export (work bake / retime TTS-fit) — cùng timeline TTS
                Path(video),
            )
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
                allow_video_slowdown=(
                    (prefer_global_slow or not manual_video_speed) and not baked_prefer
                ),
                match=match_mode,
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

        # Cắt khung đúng previewAspectRatio (editor) trước khi scale 1080
        aspect = str(settings.get("previewAspectRatio") or "original")
        if aspect not in ("", "original", "custom"):
            set_status(
                project_id,
                step="export",
                progress=88,
                message=f"{hint}Cắt khung {aspect}…",
                running=True,
            )
            check_cancel(project_id)
            crop_export_aspect(out, out, aspect, project_id=project_id)

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

        # bản dễ tìm: backend/public/exports/<id>.mp4
        exports = PUBLIC_DATA / "exports"
        exports.mkdir(exist_ok=True)
        easy = exports / f"{project_id}.mp4"
        shutil.copy2(out, easy)

        out_dur = ffprobe_duration(out)
        ow, oh = video_size(out)
        rel = f"backend/public/{project_id}/out/final.mp4"
        easy_rel = f"backend/public/exports/{project_id}.mp4"
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
        # Giữ progress hiện tại — UI thấy dừng ở đâu, không nhảy 0 rồi biến mất
        set_status(
            project_id,
            step="export",
            message=f"Xuất lỗi: {e}",
            running=False,
            error=str(e),
        )
        raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)
