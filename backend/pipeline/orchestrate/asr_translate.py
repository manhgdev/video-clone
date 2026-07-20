"""run_pipeline orchestrator."""
from __future__ import annotations

import copy
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
    run_caches = meta.get("translationCaches")
    if not isinstance(run_caches, dict):
        run_caches = {}
    current_tag = preview_tag(max(0, int(meta.get("previewSec") or 0)))
    current_segments = meta.get("segments") or []
    current_cache = meta.get("cache") or {}
    if current_segments:
        checkpoint = run_caches.get(current_tag)
        if not isinstance(checkpoint, dict):
            checkpoint = {}
        checkpoint.update({
            "asrKey": current_cache.get("asrKey", checkpoint.get("asrKey")),
            "transKey": current_cache.get("transKey", checkpoint.get("transKey")),
            "segments": copy.deepcopy(current_segments),
        })
        run_caches[current_tag] = checkpoint
    run_cache = run_caches.get(tag)
    if not isinstance(run_cache, dict):
        run_cache = {}
    # Nâng cache đơn cũ vào đúng cửa sổ hiện tại nếu key còn khớp.
    legacy_cache = meta.get("cache") or {}
    if not run_cache and legacy_cache.get("asrKey") == a_key and meta.get("segments"):
        run_cache = {
            "asrKey": legacy_cache.get("asrKey"),
            "transKey": legacy_cache.get("transKey"),
            "segments": meta.get("segments"),
        }
    cached_segments = copy.deepcopy(run_cache.get("segments") or [])
    cache = {
        "asrKey": run_cache.get("asrKey"),
        "transKey": run_cache.get("transKey"),
    }

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

        # Lần đầu dịch/lồng tiếng: luôn 1× (không bake 0.80).
        # Chậm/nhanh chỉ khi user bấm «Áp dụng tốc độ» trong editor (rebake-speed).
        match_mode = str(settings.get("matchDuration") or "preferVideo")
        user_baked = abs(float(meta.get("bakedSpeed") or 1.0) - 1.0) > 0.02
        work = Path(str(meta.get("workVideo") or ""))
        work_ok = work.is_file()
        # preview_20.mp4 / preview_20_s123.mp4 — không được tái dùng khi Dịch cả (full)
        work_is_short_preview = work_ok and "preview_" in work.name.lower()

        if preview_sec <= 0:
            # Full: luôn source (hoặc bake full), tuyệt đối không dính clip preview ngắn
            if user_baked and work_ok and not work_is_short_preview:
                video = work
            else:
                # pop bakedSpeed (không ghi 1.0) — 1.0 chỉ sau user «Áp dụng 1×»
                meta.pop("bakedPreferVideo", None)
                meta.pop("bakedSpeed", None)
                meta.pop("workDuration", None)
                video = source
                meta["workVideo"] = str(video.resolve())
        elif user_baked and work_ok and (
            f"preview_{preview_sec}" in work.name
            or work.resolve() == video.resolve()
        ):
            # Cùng cửa sổ preview + đã bake tốc độ → giữ
            video = work
        else:
            meta.pop("bakedPreferVideo", None)
            meta.pop("bakedSpeed", None)
            meta.pop("workDuration", None)
            meta["workVideo"] = str(video.resolve())

        # Đồng bộ cửa sổ làm việc ngay (status/editor không kẹt Ns cũ)
        meta["previewSec"] = preview_sec

        # —— ASR (reuse segments if same engine+lang+video+preview) ——
        if cache.get("asrKey") == a_key and cached_segments:
            segments = cached_segments
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
            for s in cached_segments:
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
                        if old.get("bboxInherited") is not None:
                            seg["bboxInherited"] = old["bboxInherited"]
                        if old.get("captionLayout"):
                            seg["captionLayout"] = old["captionLayout"]
                        if old_lay in ("vertical", "label"):
                            seg["layout"] = old_lay
                        else:
                            # suy mid/horizontal từ cy bbox — không giữ layout trống
                            from pipeline.ocr.locate import _retag_layout_from_bbox

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
                    for k in (
                        "fontSize",
                        "videoSpeed",
                        "ttsVolume",
                        "ttsSpeed",
                        "audioFile",
                        "audioUrl",
                        "audioDuration",
                        "coverStart",
                        "coverEnd",
                        "dub",
                    ):
                        if old.get(k) is not None and seg.get(k) is None:
                            seg[k] = old[k]

            cache_asr_path(project_id, tag).write_text(
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
                video,
                segments,
                only_missing=True,
                project_id=project_id,
                stable=bool(settings.get("stableCaptionLocate", False)),
                analysis_region=settings.get("analysisRegion"),
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
        run_caches[tag] = {
            "asrKey": cache.get("asrKey"),
            "transKey": cache.get("transKey"),
            "segments": copy.deepcopy(segments),
        }
        meta["translationCaches"] = run_caches
        meta["previewSec"] = preview_sec
        # clip thật sự đã ASR/dịch — xuất phải dùng đúng file này
        meta["workVideo"] = str(video.resolve())
        # ASR/dịch mới → baseline bake cũ (id/time khác) không còn hợp lệ
        meta.pop("timelineBaseline", None)
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
            message=short_cmd_error(e),
            running=False,
            error=short_cmd_error(e),
        )
        raise
    finally:
        clear_job(project_id, job_gen)

