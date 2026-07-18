"""run_export orchestrator."""
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
    segments = expand_compound_segments(segments)
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
            from pipeline.core.jobs import run_cmd

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
            # Stem cache theo videoPath gốc — đã tách lúc xem trước thì không Demucs lại
            source_audio = separate_no_vocals(project_id, report=True)
            if source_audio and source_audio.is_file():
                set_status(
                    project_id,
                    step="export",
                    progress=68,
                    message=f"{hint}Dùng stem xóa lời đã cache…",
                    running=True,
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
            from pipeline.core.media import meta_baked_speed

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
                # Wav TTS 1× → atempo theo bake (0.8 / 1.23 / 2…) khớp timeline
                bake_speed=meta_baked_speed(meta),
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
        err = short_cmd_error(e)
        set_status(
            project_id,
            step="export",
            message=f"Xuất lỗi: {err}",
            running=False,
            error=err,
        )
        raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)

