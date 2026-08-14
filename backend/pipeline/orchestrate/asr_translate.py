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
    preview_clip_matches,
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
from pipeline.core.resources import adaptive_workers, progress_msg
from pipeline.ocr.locate import attach_speech_hardsub_boxes
from pipeline.ocr.extract_parts.textutil import _ocr_fix_zh
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
    # settings.previewSec ở đây = cửa sổ LẦN CHẠY (api_run đã tách khỏi ô UI)
    preview_sec = max(0, int(settings.get("previewSec") or 0))
    tag = preview_tag(preview_sec)
    # v2: vertical speech/SRT cues are split into readable 9:16 display
    # units before translating.  Keep old cache rows from restoring long
    # sentence-sized cues after this behavior changes.
    run_settings = {**settings, "previewSec": preview_sec, "portraitCueLayout": 2}
    a_key = asr_cache_key(run_settings, source_fp)
    t_key = trans_cache_key(run_settings)
    run_caches = meta.get("translationCaches")
    if not isinstance(run_caches, dict):
        run_caches = {}
    # Checkpoint theo cửa sổ clip đang active (meta.previewSec), không theo ô UI
    current_tag = preview_tag(max(0, int(meta.get("previewSec") or 0)))
    current_segments = meta.get("segments") or []
    current_cache = meta.get("cache") or {}
    if current_segments and current_tag != tag:
        # Đổi full↔preview: cất bản đang mở sang tag cũ, không trộn segments
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
    # Chỉ nâng legacy khi cùng cửa sổ (tránh full ăn segments preview)
    legacy_cache = meta.get("cache") or {}
    legacy_ok = (
        not run_cache
        and legacy_cache.get("asrKey") == a_key
        and meta.get("segments")
        and current_tag == tag
    )
    if legacy_ok:
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

        # preferVideo: bake 0.80× TRƯỚC ASR (khối dưới) — xử lý thẳng trên file
        # chậm. Các mode khác: 1×; đổi tốc độ sau bằng «Áp dụng» (rebake-speed).
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
            preview_clip_matches(work.name, preview_sec)
            or work.resolve() == video.resolve()
        ):
            # Cùng cửa sổ preview + đã bake tốc độ → giữ
            video = work
        else:
            meta.pop("bakedPreferVideo", None)
            meta.pop("bakedSpeed", None)
            meta.pop("workDuration", None)
            meta["workVideo"] = str(video.resolve())

        # «Ưu tiên chậm 0.8»: làm chậm clip TRƯỚC rồi ASR/dịch/OCR trên chính
        # file đó (cửa sổ đo rộng 1.25×); PHÂN TÍCH XONG tự nâng timeline về
        # 1.00× (khối cuối) — editor/xuất luôn ở tốc độ thật.
        auto_baked_prefer = False
        if (
            str(settings.get("engine") or "") != "subtitle"
            and
            str(settings.get("matchDuration") or "") == "preferVideo"
            and meta.get("bakedSpeed") is None
        ):
            auto_baked_prefer = True
            from pipeline.core.media import ensure_playback_speed, speed_cache_tag

            set_status(
                project_id,
                step="asr",
                progress=4,
                message="Làm chậm video 0.70× (ưu tiên chậm) trước khi dịch…",
                running=True,
            )
            s070 = speed_cache_tag(0.7)
            dest = ensure_layout(project_id) / "cache" / (
                f"preview_{preview_sec}_{s070}.mp4"
                if preview_sec > 0
                else f"source_{s070}.mp4"
            )
            video = ensure_playback_speed(video, dest, 0.7, project_id=project_id)
            meta["bakedPreferVideo"] = True
            meta["bakedSpeed"] = 0.7
            meta["workDuration"] = float(ffprobe_duration(video) or 0)
            meta["workVideo"] = str(video.resolve())
            meta["timelineClock"] = "display"

        # Cache key theo tốc độ file thật sự ASR (0.8 bake trước ≠ cache 1×)
        from pipeline.core.media import meta_baked_speed as _meta_baked_speed

        work_speed = _meta_baked_speed(meta)
        a_key = asr_cache_key(run_settings, source_fp, speed=work_speed)

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
            engine = settings.get("engine", "whisper")
            if engine == "subtitle":
                source_name = Path(str(settings.get("subtitleSource") or "")).name
                source_file = ensure_layout(project_id) / "subtitles" / source_name
                if not source_name or not source_file.is_file():
                    raise RuntimeError("Chọn file phụ đề SRT trong Clone Video trước khi chạy.")
                from pipeline.subtitles import subtitle_segments

                set_status(project_id, step="asr", progress=35, message="Đọc phụ đề SRT…", running=True)
                segments = subtitle_segments(source_file, preview_sec=preview_sec)
                use_ocr = False
            else:
                wav = cache_audio(project_id, audio_cache_tag(preview_sec, match_mode, speed=work_speed))
                use_ocr = engine in ("paddleocr", "screen")
                segments = []
            if not use_ocr:
                if engine == "subtitle":
                    pass
                elif wav.exists() and wav.stat().st_mtime >= video.stat().st_mtime:
                    set_status(
                        project_id, step="asr", progress=8, message="Cache audio…", running=True
                    )
                else:
                    set_status(
                        project_id, step="asr", progress=5, message="Tách audio…", running=True
                    )
                    extract_audio(video, wav, project_id=project_id)

            frames_ok = any(cache_frames(project_id, tag).glob("*.jpg"))
            if engine == "subtitle":
                pass
            elif use_ocr:
                ocr_req = int(settings.get("workers") or 0)
                analysis_region = settings.get("analysisRegion")
                stable = bool(settings.get("stableCaptionLocate", False))
                # Message chi tiết (N luồng) do asr_paddleocr/_report cập nhật
                set_status(
                    project_id,
                    step="asr",
                    progress=15,
                    message=progress_msg("OCR", workers=(None if ocr_req <= 0 else ocr_req)),
                    running=True,
                )
                segments = asr_paddleocr(
                    video,
                    project_id,
                    reuse_frames=frames_ok,
                    tag=tag,
                    workers=ocr_req,
                    source_lang=str(settings.get("sourceLang") or "auto"),
                    analysis_region=analysis_region,
                    stable=stable,
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
                    message=progress_msg("Whisper", workers=w),
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

            # Whisper/SRT timestamps describe speech, not the small batches of
            # text visible on a vertical video.  Split only true portrait input
            # here; 16:9 keeps sentence-sized cues.  OCR below still finds the
            # actual on-screen position for every resulting cue.
            try:
                input_w, input_h = video_size(video)
            except Exception:
                input_w, input_h = 0, 0
            if input_h > input_w > 0 and engine in ("whisper", "subtitle"):
                from pipeline.subtitles import split_portrait_caption_segments

                segments = split_portrait_caption_segments(segments)

            # Normalize a tiny, whitelisted set of Chinese ASR homophones before
            # translation; this keeps the correction shared by Whisper and OCR.
            fixed_sources = _ocr_fix_zh([str(s.get("source") or "") for s in segments], project_id)
            for seg, fixed in zip(segments, fixed_sources):
                if fixed:
                    seg["source"] = fixed

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
                need_idx = [i for i, s in enumerate(segments) if not s.get("maskOnly")]
            else:
                need_idx = [
                    i for i, s in enumerate(segments) if not (s.get("translation") or "").strip() and not s.get("maskOnly")
                ]

            translations: list[str] = []
            if need_idx:
                texts = [segments[i]["source"] for i in need_idx]
                target = settings.get("targetLang", "vi")
                source = settings.get("sourceLang", "auto")
                check_cancel(project_id)
                if target in ("none", "off", "source", ""):
                    set_status(
                        project_id,
                        step="translate",
                        progress=55,
                        message=f"Giữ chữ nguồn {len(need_idx)}/{len(segments)} đoạn…",
                        running=True,
                    )
                    # Giữ nguyên chữ nguồn — không gọi máy dịch
                    translations = list(texts)
                else:
                    w = adaptive_workers(
                        int(settings.get("workers") or 0),
                        kind="network",
                        cap=16,
                        tasks=len(texts),
                    )
                    set_status(
                        project_id,
                        step="translate",
                        progress=55,
                        message=progress_msg("Dịch", 0, len(need_idx), workers=w),
                        running=True,
                    )
                    translations = translate_segments(
                        texts,
                        target,
                        project_id=project_id,
                        source_lang=source,
                        translator=str(settings.get("translator") or "google"),
                        workers=w,
                        ollama_mode=str(settings.get("ollamaMode") or "cloud"),
                        ollama_model=str(settings.get("ollamaModel") or "minimax-m3:cloud"),
                        ollama_local_tier=str(settings.get("ollamaLocalTier") or "balanced"),
                        durations=[
                            max(
                                0.2,
                                float(segments[i].get("end") or 0)
                                - float(segments[i].get("start") or 0),
                            )
                            for i in need_idx
                        ],
                    )
                # Ghi kết quả dịch vào segments (bug cũ: nằm nhầm trong else → luôn trống)
                for i, tr in zip(need_idx, translations):
                    segments[i]["translation"] = (tr or "").strip() or segments[i].get(
                        "translation"
                    ) or ""
            else:
                set_status(
                    project_id,
                    step="translate",
                    progress=55,
                    message=f"Dịch 0/{len(segments)} đoạn…",
                    running=True,
                )
            for seg in segments:
                seg["voice"] = inherit_voice(seg.get("voice"), voice)
            cache["transKey"] = t_key

        # Whisper/SRT provide text timing, not its on-screen position. OCR only
        # locates the hard-sub area; it does not replace the SRT's text.
        engine = settings.get("engine", "whisper")
        if (
            engine not in ("paddleocr", "screen")
            # Bbox is also needed when only covering existing hard-subs;
            # editor's “Không chèn chữ” disables burnSubs but not coverHardsubs.
            and (bool(settings.get("burnSubs", True)) or bool(settings.get("coverHardsubs", True)))
            and segments
        ):
            # Định vị: ưu tiên GPU (RapidOCR) khi có CUDA — không kẹp kind=cpu → 2 luồng
            try:
                from pipeline.ocr.extract import _rapidocr_gpu_kwargs
                from pipeline.core.resources import pack_gpu_workers

                _loc_cuda = bool(_rapidocr_gpu_kwargs().get("det_use_cuda"))
            except Exception:
                _loc_cuda = False
            if _loc_cuda:
                _loc_cap = pack_gpu_workers(per_job_mb=450, reserve_mb=350, hard_max=16)
                locate_w = adaptive_workers(
                    int(settings.get("workers") or 0), kind="gpu", cap=_loc_cap
                )
            else:
                locate_w = adaptive_workers(
                    int(settings.get("workers") or 0), kind="cpu", cap=14
                )
            set_status(
                project_id,
                step="translate",
                progress=95,
                message=progress_msg("Định vị OCR", workers=locate_w),
                running=True,
            )
            n_box = 0
            locate_ok = False
            locate_layout_version = 3
            # Frozen: probe cv2 TRƯỚC — nếu path hỏng thì bỏ locate (vẫn giữ bản dịch).
            # Tránh native crash OpenCV recursion kéo tắt cả app sau khi dịch xong.
            cv2_ok = True
            try:
                import sys as _sys

                if getattr(_sys, "frozen", False):
                    from pipeline.core.runtime_site import ensure_cv2, prepare_cv2_import_path

                    prepare_cv2_import_path()
                    ensure_cv2()
            except Exception as cv2_e:
                cv2_ok = False
                try:
                    from pipeline.core.app_log import append_exception

                    append_exception("[translate] skip OCR locate (cv2)", cv2_e)
                except Exception:
                    pass
                set_status(
                    project_id,
                    step="translate",
                    progress=96,
                    message="Bỏ định vị OCR (OpenCV) — vẫn giữ bản dịch",
                    running=True,
                )
            if cv2_ok:
                srt_locator = engine == "subtitle"
                if srt_locator:
                    for seg in segments:
                        seg["_locatorProbeEarly"] = True
                try:
                    n_box = attach_speech_hardsub_boxes(
                        video,
                        segments,
                        only_missing=int(meta.get("bboxLocateVersion") or 0) >= locate_layout_version,
                        project_id=project_id,
                        stable=bool(settings.get("stableCaptionLocate", False)),
                        analysis_region=settings.get("analysisRegion"),
                        status_workers=locate_w,
                    )
                    locate_ok = True
                except Cancelled:
                    # Huỷ là ý người dùng — phải nổi lên để job dừng hẳn,
                    # không biến thành "Bỏ định vị OCR" rồi chạy tiếp.
                    raise
                except BaseException as ocr_e:
                    # BaseException: bắt cả lỗi lạ; không để kill thread/app
                    n_box = 0
                    try:
                        from pipeline.core.app_log import append_exception

                        append_exception("[translate] OCR locate failed", ocr_e)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    set_status(
                        project_id,
                        step="translate",
                        progress=96,
                        message=f"Bỏ định vị OCR ({type(ocr_e).__name__}) — vẫn giữ bản dịch",
                        running=True,
                    )
                finally:
                    if srt_locator:
                        for seg in segments:
                            seg.pop("_locatorProbeEarly", None)
            if locate_ok:
                meta["bboxLocateVersion"] = locate_layout_version
            if n_box:
                set_status(
                    project_id,
                    step="translate",
                    progress=97,
                    message=progress_msg("Định vị OCR", n_box, len(segments), workers=locate_w),
                    running=True,
                )

        if (
            engine not in ("paddleocr", "screen")
            and bool(settings.get("coverLogo", False))
            and not (
                isinstance(meta.get("logoDetection"), dict)
                and isinstance(meta["logoDetection"].get("bbox"), dict)
            )
        ):
            try:
                from pipeline.ocr.locate_worker import _detect_logo_via_runtime_subprocess

                set_status(
                    project_id,
                    step="translate",
                    progress=98,
                    message="Định vị logo…",
                    running=True,
                )
                logo_detection = _detect_logo_via_runtime_subprocess(
                    video, project_id=project_id, segments=segments
                )
                if logo_detection:
                    meta["logoDetection"] = logo_detection
                else:
                    meta.pop("logoDetection", None)
            except Cancelled:
                raise
            except Exception as logo_e:
                try:
                    from pipeline.core.app_log import append_exception

                    append_exception("[translate] logo detection failed", logo_e)
                except Exception:
                    pass

        meta["segments"] = segments
        # Ô Preview UI (settings.previewSec) giữ số user gõ — không ghi đè bằng 0 (full)
        prev_settings = meta.get("settings") if isinstance(meta.get("settings"), dict) else {}
        ui_prev = max(0, int(prev_settings.get("previewSec") or 0))
        if ui_prev <= 0:
            ui_prev = 20
        meta["settings"] = {**prev_settings, **{k: v for k, v in settings.items() if k != "previewSec"}, "previewSec": ui_prev}
        meta["cache"] = cache
        run_caches[tag] = {
            "asrKey": cache.get("asrKey"),
            "transKey": cache.get("transKey"),
            "segments": copy.deepcopy(segments),
        }
        meta["translationCaches"] = run_caches
        # Cửa sổ clip lần chạy (0=full) — tách khỏi ô Preview
        meta["previewSec"] = preview_sec
        # clip thật sự đã ASR/dịch — xuất phải dùng đúng file này
        meta["workVideo"] = str(video.resolve())
        # ASR/dịch mới → baseline bake cũ (id/time khác) không còn hợp lệ
        meta.pop("timelineBaseline", None)
        # Phân tích 0.8 xong → nâng timeline + work file về 1.00× (yêu cầu:
        # đầu vào 0.8 chỉ để đo; vào editor là tốc độ thật, khỏi nâng tay).
        # Cache dịch (run_caches) giữ clock 0.8 khớp asrKey s080 cho lần chạy sau.
        if auto_baked_prefer:
            from pipeline.core.media import (
                preview_1x_path,
                remap_timeline_for_speed_change,
            )

            set_status(
                project_id,
                step="translate",
                progress=98,
                message="Phân tích xong — nâng timeline về 1.00×…",
                running=True,
            )
            remap_timeline_for_speed_change(meta, 0.7, 1.0)
            segments = meta.get("segments") or segments
            base_1x = preview_1x_path(project_id, meta)
            meta["bakedSpeed"] = 1.0
            meta.pop("bakedPreferVideo", None)
            meta.pop("workDuration", None)
            meta["workVideo"] = str(base_1x.resolve())
            meta["timelineClock"] = "display"
            # Dấu vết flow «ưu tiên 0.8»: dub sẽ mặc định giọng 1.20× (khe đã co)
            meta["analyzedAtSpeed"] = 0.7
        save_meta(project_id, meta)
        hint = f"Preview {preview_sec}s — " if preview_sec > 0 else ""
        no_tr = str(settings.get("targetLang") or "") in ("none", "off", "source", "")
        if no_tr:
            next_msg = f"{hint}Xong {len(segments)} đoạn — không dịch, không chèn caption"
        elif engine in ("paddleocr", "screen"):
            next_msg = f"{hint}Xong {len(segments)} đoạn — bấm Xuất bản để che chữ cũ + đè bản dịch"
        elif engine == "subtitle":
            next_msg = f"{hint}Dùng phụ đề SRT: {len(segments)} đoạn — tiếp theo: Lồng tiếng → Xuất bản"
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
        return
    except Exception as e:
        # Đã ghi status — không re-raise (desktop: exception thread + OCR native dễ kéo tắt app)
        try:
            from pipeline.core.app_log import append_exception

            append_exception(f"[translate:{project_id}] FAILED", e)
        except Exception:
            pass
        set_status(
            project_id,
            step="translate",
            progress=0,
            message=short_cmd_error(e),
            running=False,
            error=short_cmd_error(e),
        )
    finally:
        clear_job(project_id, job_gen)
