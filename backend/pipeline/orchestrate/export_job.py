"""run_export orchestrator."""
from __future__ import annotations

import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def _timeline_is_final(meta: dict[str, Any], video: Path) -> bool:
    """True khi FE đã Áp dụng tốc độ: file bake + segment start/end = đồng hồ display."""
    from pipeline.core.media import meta_has_user_bake

    if meta.get("timelineClock") == "display":
        return True
    if meta_has_user_bake(meta):
        return True
    name = Path(video).name.lower()
    if re.search(r"_s\d{3}", name) or name.startswith("source_s"):
        return True
    work = Path(str(meta.get("workVideo") or ""))
    try:
        if work.is_file() and Path(video).resolve() == work.resolve():
            bake = meta_baked_speed(meta)
            if abs(float(bake) - 1.0) > 0.02 or bool(meta.get("bakedPreferVideo")):
                return True
    except OSError:
        pass
    return False


def _export_retime_base(meta: dict[str, Any], video: Path, match_mode: str) -> float:
    """Global bake chỉ nằm trong file work. Retime export chỉ còn videoSpeed câu (base=1).

    FE là nguồn timeline sau Áp dụng — BE không nhân tốc độ global lần 2.
    """
    _ = match_mode
    _ = video
    _ = meta
    return 1.0

from pipeline.asr import asr_paddleocr, asr_whisper
from pipeline.export.burn import cover_and_burn
from pipeline.core.config import PUBLIC_DATA, export_display_path
from pipeline.core.jobs import Cancelled, begin_job, check_cancel, clear_job, short_cmd_error
from pipeline.core.media import (
    encode_export_1080,
    ensure_preview_clip,
    extract_audio,
    ffprobe_duration,
    meta_baked_speed,
    retime_audio_track,
    retime_timeline_time,
    retime_video_segments,
    resolve_export_crop,
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
from pipeline.export.compound import expand_compound_segments
from pipeline.export.source_video import export_source_video
from pipeline.ocr.locate import attach_speech_hardsub_boxes
from pipeline.translate import translate_segments
from pipeline.tts import tts_cache_key, tts_segment

from pipeline.orchestrate.export_outputs import _project_slug, write_export_artifacts
from pipeline.orchestrate.export_overlays import build_text_overlay_cues
from pipeline.orchestrate.tts_fit import assign_tts_fit_speeds


def _color_adjusted_video(project_id: str, root: Path, video: Path, settings: dict[str, Any]) -> Path:
    """Apply the same project color contract before every export path."""
    adjust = settings.get("colorAdjust") if isinstance(settings.get("colorAdjust"), dict) else {}
    bright = max(-100.0, min(100.0, float(adjust.get("brightness") or 0)))
    contrast = max(-100.0, min(100.0, float(adjust.get("contrast") or 0)))
    saturation = max(0.0, min(200.0, float(adjust.get("saturation") if adjust.get("saturation") is not None else 100)))
    temp = max(-100.0, min(100.0, float(adjust.get("temperature") or 0)))
    tint = max(-100.0, min(100.0, float(adjust.get("tint") or 0)))
    lut_id = str(settings.get("lutAssetId") or "")
    neutral = not any((bright, contrast, temp, tint)) and abs(saturation - 100.0) < 0.01 and not lut_id
    if neutral:
        return video
    filters = [f"eq=brightness={bright / 100:.4f}:contrast={1 + contrast / 100:.4f}:saturation={saturation / 100:.4f}"]
    if temp or tint:
        filters.append(f"colorbalance=rs={temp / 100:.4f}:bs={-temp / 100:.4f}:gs={tint / 100:.4f}")
    if lut_id:
        asset = next((item for item in settings.get("mediaAssets", []) if isinstance(item, dict) and item.get("id") == lut_id), None)
        # mediaAssets lives on meta in normal projects; caller may inject it below.
        if asset and str(asset.get("file") or "").lower().endswith(".cube"):
            lut = root / "assets" / str(asset["file"])
            if lut.is_file():
                filters.append("lut3d=file=" + str(lut).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'"))
    out = root / "cache" / "color_pipeline.mp4"
    from pipeline.core.jobs import run_cmd
    run_cmd(project_id, ["ffmpeg", "-y", "-i", str(video), "-vf", ",".join(filters), "-map", "0:a?", "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(out)])
    return out


def _logo_mask_cue(
    meta: dict[str, Any],
    video: Path,
    duration: float,
    project_id: str,
) -> dict[str, Any] | None:
    settings = meta.get("settings") or {}
    # Color pipeline resolves LUTs from the project asset library without
    # widening the public settings schema with raw filesystem paths.
    settings = {**settings, "mediaAssets": meta.get("mediaAssets") or []}
    if not bool(settings.get("coverLogo", False)):
        return None
    detection = meta.get("logoDetection")
    bbox = detection.get("bbox") if isinstance(detection, dict) else None
    if not isinstance(bbox, dict):
        from pipeline.ocr.locate_worker import _detect_logo_via_runtime_subprocess

        set_status(
            project_id,
            step="export",
            progress=12,
            message="Định vị logo trước khi xuất…",
            running=True,
        )
        detection = _detect_logo_via_runtime_subprocess(
            video,
            project_id=project_id,
            segments=meta.get("segments") if isinstance(meta.get("segments"), list) else [],
        )
        if not detection:
            set_status(
                project_id,
                step="export",
                progress=14,
                message="Không tìm thấy logo — tiếp tục xuất…",
                running=True,
            )
            return None
        meta["logoDetection"] = detection
        save_meta(project_id, meta)
        bbox = detection.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = max(0.0, min(1.0, float(bbox.get("x") or 0)))
        y = max(0.0, min(1.0, float(bbox.get("y") or 0)))
        bw = max(0.0, min(1.0 - x, float(bbox.get("w") or 0)))
        bh = max(0.0, min(1.0 - y, float(bbox.get("h") or 0)))
    except (TypeError, ValueError):
        return None
    if bw < 0.002 or bh < 0.002:
        return None
    fw, fh = video_size(video)
    return {
        "id": "detected-logo-mask",
        "start": 0.0,
        "end": max(0.04, float(duration)),
        "coverStart": 0.0,
        "coverEnd": max(0.04, float(duration)),
        "translation": "",
        "source": "",
        "layout": "horizontal",
        "bbox": {
            "x": round(x * fw),
            "y": round(y * fh),
            "w": max(2, round(bw * fw)),
            "h": max(2, round(bh * fh)),
        },
        "maskOnly": True,
        "coverMaskStyle": "inpaint",
        "coverMaskOpacity": 100,
    }


def _logo_mask_cues(
    meta: dict[str, Any], video: Path, duration: float, project_id: str
) -> list[dict[str, Any]]:
    """Build both the legacy fixed-logo mask and time-bound moving masks."""
    detection = meta.get("logoDetection")
    tracks = detection.get("tracks") if isinstance(detection, dict) else None
    settings = meta.get("settings") or {}
    excluded = {
        str(text).strip()
        for text in (settings.get("hiddenLogoTexts") or [])
        if str(text).strip()
    }
    # A static watermark may be promoted to an editable effect clip by the
    # editor. In that case the overlay is the single source of truth for bbox
    # and duration; do not apply the detector mask a second time.
    editable_watermarks = [
        item for item in (meta.get("overlays") or [])
        if isinstance(item, dict) and item.get("watermarkSource")
    ]

    def is_excluded(text: Any) -> bool:
        value = str(text or "").strip()
        if value in excluded:
            return True
        # OCR can misread a glyph while a platform watermark moves.  A user
        # toggle for an @handle applies to all OCR variants of that handle.
        if value.startswith("@") and any(item.startswith("@") for item in excluded):
            return True
        # Paddle OCR occasionally reads the final + as 十.  The checkbox is
        # one logical AI watermark, so all those variants must follow it.
        return "生成" in value and any("生成" in item for item in excluded)
    # A detector result with dynamic tracks intentionally has no fixed bbox.
    # Do not invoke OCR a second time merely because that bbox is absent.
    fixed = (
        None
        if isinstance(tracks, list) and not isinstance((detection or {}).get("bbox"), dict)
        else _logo_mask_cue(meta, video, duration, project_id)
    )
    if not isinstance(tracks, list):
        return [fixed] if fixed else []

    fw, fh = video_size(video)
    cues: list[dict[str, Any]] = []
    if fixed:
        cues.append(fixed)
    for index, track in enumerate(tracks):
        if not isinstance(track, dict) or not isinstance(track.get("bbox"), dict):
            continue
        # TikTok/Douyin handles move around the frame.  Their per-frame OCR
        # boxes cannot produce a reliable clean result, so leave them alone
        # unless/ until a true motion tracker is available.
        if str(track.get("text") or "").strip().startswith("@"):
            continue
        label = str(track.get("text") or "").strip()
        if "生成" in label and any("生成" in str(item.get("watermarkSource") or "") for item in editable_watermarks):
            continue
        if is_excluded(track.get("text")):
            continue
        bbox = track["bbox"]
        try:
            x = max(0.0, min(1.0, float(bbox.get("x") or 0)))
            y = max(0.0, min(1.0, float(bbox.get("y") or 0)))
            bw = max(0.0, min(1.0 - x, float(bbox.get("w") or 0)))
            bh = max(0.0, min(1.0 - y, float(bbox.get("h") or 0)))
            start = max(0.0, float(track.get("start") or 0.0))
            end = min(float(duration), float(track.get("end") or duration))
        except (TypeError, ValueError):
            continue
        if bw < 0.002 or bh < 0.002 or end <= start:
            continue
        cues.append(
            {
                "id": f"detected-moving-logo-mask-{index}",
                "start": start,
                "end": end,
                "coverStart": start,
                "coverEnd": end,
                "translation": "",
                "source": "",
                "layout": "horizontal",
                "bbox": {
                    "x": round(x * fw), "y": round(y * fh),
                    "w": max(2, round(bw * fw)), "h": max(2, round(bh * fh)),
                },
                "maskOnly": True,
                "coverMaskStyle": "inpaint",
                "coverMaskOpacity": 100,
            }
        )
    return cues


def run_export(project_id: str, *, nested: bool = False) -> Path:
    job_gen: int | None = None
    if not nested:
        job_gen = begin_job(project_id)
        set_status(
            project_id,
            step="export",
            progress=2,
            message="Đang xuất…",
            running=True,
            error=None,
        )
    meta = load_meta(project_id)
    if not meta:
        raise RuntimeError("Không tìm thấy project")
    video, preview_sec = export_source_video(project_id, meta)
    root = ensure_layout(project_id)
    source_dur = ffprobe_duration(video)
    settings = meta.get("settings") or {}
    match_mode = str(settings.get("matchDuration") or "preferVideo")
    export_start = max(0, float(meta.get("exportStartSec") or 0))
    export_end = float(meta.get("exportEndSec") or 0)
    # exportEndSec = mốc nguồn tuyệt đối (sourceStart + span) hoặc duration khi start=0
    if export_end > export_start > 0:
        export_clip_dur = export_end - export_start
    elif export_end > 0:
        export_clip_dur = export_end
    else:
        export_clip_dur = 0.0
    if export_start > 0 or (
        export_clip_dur > 0 and source_dur > 0 and export_start + export_clip_dur < source_dur - 0.02
    ):
        video = ensure_preview_clip(
            video,
            root / "cache"
            / f"export_{round(export_start * 1000)}_{round((export_start + export_clip_dur) * 1000)}.mp4",
            max(0.05, min(export_clip_dur, max(0.05, source_dur - export_start))),
            project_id,
            start=export_start,
        )
    # Chuyển cue về mốc 0 của clip xuất. Preview luôn dùng mốc timeline này;
    # giữ mốc nguồn ở đây sẽ làm bbox/caption/TTS lệch khi exportStartSec > 0.
    vid_dur = ffprobe_duration(video) or 1e9
    segments = [dict(s) for s in (meta.get("segments") or [])]
    no_translate = str(settings.get("targetLang") or "") in ("none", "off", "source", "")
    subtitle_track = str(settings.get("subtitleExportTrack") or "dub")
    for cue in segments:
        source_text = str(cue.get("sourceSubtitle") if cue.get("sourceSubtitle") is not None else cue.get("source") or "")
        dub_text = str(cue.get("dubSubtitle") if cue.get("dubSubtitle") is not None else cue.get("translation") or "")
        # Không dịch: luôn dùng chữ nguồn làm caption (bất kể có bản dịch cũ hay không)
        if no_translate:
            dub_text = source_text
        cue["sourceSubtitle"] = source_text
        cue["dubSubtitle"] = dub_text
        # Renderer still consumes `translation`; adapt only its render view so
        # legacy TTS/metadata remains untouched.
        cue["translation"] = source_text if subtitle_track == "source" else ("\n".join(x for x in (source_text, dub_text) if x) if subtitle_track == "both" else dub_text)
    if export_start > 0:
        for s in segments:
            for key in ("start", "end", "coverStart", "coverEnd"):
                if s.get(key) is None:
                    continue
                try:
                    s[key] = max(0.0, float(s[key]) - export_start)
                except (TypeError, ValueError):
                    pass
    segments = [s for s in segments if float(s.get("start") or 0) < vid_dur - 0.05]
    segments = expand_compound_segments(segments)
    for s in segments:
        if float(s.get("end") or 0) > vid_dur:
            s["end"] = vid_dur
    source_timeline_duration = vid_dur
    source_timeline_segments = [dict(s) for s in segments]
    # FE timeline sau Áp dụng = chuẩn. base luôn 1.0.
    # videoSpeed <1 (TTS-fit cũ) đã khai tử — project cũ còn sót thì dọn ở đây
    # để xuất đúng thước; chỉ còn nướng speed user đặt (≥1, menu Tốc độ video).
    from pipeline.orchestrate.tts_fit import strip_auto_video_speeds

    strip_auto_video_speeds(segments)
    timeline_final = _timeline_is_final(meta, video)
    retime_base = _export_retime_base(meta, video, match_mode)
    video, segments = retime_video_segments(
        video,
        segments,
        root / "cache",
        project_id,
        base_speed=retime_base,
    )
    video = _color_adjusted_video(project_id, root, video, settings)
    vid_dur = ffprobe_duration(video) or vid_dur
    text_overlays = build_text_overlay_cues(
        meta,
        root,
        project_id,
        export_start=export_start,
        vid_dur=vid_dur,
        source_timeline_duration=source_timeline_duration,
        source_timeline_segments=source_timeline_segments,
        retime_base=retime_base,
    )
    out = out_final(project_id)

    # Cờ xuất độc lập — mặc định video=True nếu không có gì được chọn
    do_video = bool(settings.get("exportVideo", True))
    do_audio = bool(settings.get("exportAudio", False))
    do_srt   = bool(settings.get("exportSrt",   False))
    do_gif   = bool(settings.get("exportGif",   False))
    if not any([do_video, do_audio, do_srt, do_gif]):
        do_video = True
    if do_video:
        text_overlays.extend(_logo_mask_cues(meta, video, vid_dur, project_id))

    cover = bool(settings.get("coverHardsubs", True))
    burn = bool(settings.get("burnSubs", True))

    # ── Fast path: chỉ xuất SRT (không cần render video) ──
    if do_srt and not do_video and not do_audio and not do_gif:
        try:
            from pipeline.export.srt import write_subtitle
            _custom_dir = str(settings.get("exportOutputDir") or "").strip()
            _slug = _project_slug(meta)
            exports = (Path(_custom_dir) if _custom_dir else PUBLIC_DATA / "exports") / _slug
            exports.mkdir(parents=True, exist_ok=True)
            render_id = f"{project_id}-{time.time_ns()}"
            render_name = str(meta.pop("pendingRenderName", "")).strip() or f"Render {project_id}"
            set_status(project_id, step="export", progress=50, message="Xuất chú thích (SRT)…", running=True)
            check_cancel(project_id)
            fmt = str(settings.get("exportSrtFormat") or "srt").lower()
            if fmt not in ("srt", "vtt", "txt"):
                fmt = "srt"
            cues = [
                {"start": float(s.get("start") or 0), "end": float(s.get("end") or 0),
                 "text": (str(s.get("translation") or s.get("source") or "")).strip()}
                for s in segments
                if not s.get("maskOnly")
                and (str(s.get("translation") or s.get("source") or "")).strip()
            ]
            import re as _re_ext
            safe_name = _re_ext.sub(r'[^\w\s-]', '', render_name).strip()
            safe_name = _re_ext.sub(r'[-\s]+', '-', safe_name)
            if not safe_name:
                safe_name = project_id
            srt_out     = exports / f"{safe_name}.{fmt}"
            write_subtitle(srt_out, cues, fmt, capcut=False)
            (exports / f"{safe_name}.json").write_text(
                json.dumps({"name": render_name, "projectId": project_id, "kind": "srt"}, ensure_ascii=False),
                encoding="utf-8",
            )
            rel = export_display_path(srt_out)
            meta["lastRenderId"] = render_id
            meta["lastRenderName"] = render_name
            meta["exportOutputDir"] = str(exports)
            save_meta(project_id, meta)
            set_status(project_id, step="export", progress=100,
                       message=f"Xong · {len(cues)} câu — {rel}",
                       running=False, outputRel=rel, error=None)
        except Cancelled:
            set_status(project_id, step="export", progress=0, message="Đã huỷ xuất bản", running=False, error="cancelled")
        except Exception as e:
            err = short_cmd_error(e)
            try:
                from pipeline.core.app_log import append_exception
                append_exception(f"[export:{project_id}] SRT FAILED", e)
            except Exception:
                pass
            set_status(project_id, step="export", message=f"Xuất lỗi: {err}", running=False, error=err)
        finally:
            if not nested and job_gen is not None:
                clear_job(project_id, job_gen)
        return


    try:
        hint = f"preview {preview_sec}s — " if preview_sec > 0 else ""
        # videoSpeed was baked into the cached video/timeline before overlays.
        # Keep this defensive cleanup for legacy speed=1 payloads.
        for segment in segments:
            segment.pop("videoSpeed", None)
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
        # P1.5: tính crop khung + resolution TRƯỚC burn để ffgraph gộp vào
        # cùng lệnh (video sau retime cùng kích thước với burned/out sau mux).
        crop_box = None
        target_height = None
        legacy_scale = float(settings.get("videoScale") or 100.0)
        video_scale_x = max(1.0, min(500.0, float(settings.get("videoScaleX") or legacy_scale)))
        video_scale_y = max(1.0, min(500.0, float(settings.get("videoScaleY") or legacy_scale)))
        aspect = str(settings.get("previewAspectRatio") or "original")
        if do_video:
            custom_crop = settings.get("previewCrop")
            if aspect not in ("", "original") and (aspect != "custom" or custom_crop):
                sw, sh = video_size(video)
                crop_box = resolve_export_crop(sw, sh, aspect, custom_crop)
            resolution = str(settings.get("exportResolution") or "1080").lower()
            allowed_resolutions = {"144", "240", "360", "480", "720", "1080", "1440", "2160"}
            if resolution != "original" and resolution not in allowed_resolutions:
                resolution = "1080"
            target_height = None if resolution == "original" else int(resolution)
        render_info: dict[str, Any] = {}

        burned = out_burned(project_id)
        if not do_video:
            # Audio-only: không cần render video frame → copy nguồn làm temp để trích audio
            import shutil as _shutil
            _shutil.copy2(str(video), str(burned))
        elif cover or burn or text_overlays:
            place = str(settings.get("captionPlacement") or "below").lower()
            if place not in ("below", "above"):
                place = "below"
            exp_w = adaptive_workers(
                int(settings.get("workers") or 0),
                kind="cpu",
                cap=16,
            )
            set_status(
                project_id,
                step="export",
                progress=22,
                message=progress_msg(msg.rstrip("…"), workers=exp_w),
                running=True,
            )
            cover_and_burn(
                video,
                segments + text_overlays,
                burned,
                cover=cover,
                burn=burn or bool(text_overlays),
                subtitle_font_size=int(settings.get("subtitleFontSize", 0)),
                subtitle_font_family=str(settings.get("subtitleFontFamily") or "system"),
                project_id=project_id,
                workers=exp_w,
                caption_placement=place,
                cover_mask_style=str(settings.get("coverMaskStyle") or "blur"),
                cover_mask_color=str(settings.get("coverMaskColor") or "#4c1d95"),
                cover_mask_opacity=int(settings.get("coverMaskOpacity", 40)),
                caption_text_color=str(settings.get("captionTextColor") or "#ffffff"),
                caption_bg_style=str(settings.get("captionBgStyle") or "none"),
                caption_bg_color=str(settings.get("captionBgColor") or "#000000"),
                caption_bg_opacity=int(settings.get("captionBgOpacity", 55)),
                caption_stroke=bool(settings.get("captionStroke", True)),
                post_crop=crop_box,
                post_height=target_height,
                video_scale_x=video_scale_x,
                video_scale_y=video_scale_y,
                render_info=render_info,
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
                # Stem tách từ file 1×. Nếu timeline display (đã bake): chậm đều stem
                # về đồng hồ FE rồi mới áp videoSpeed câu (base=1).
                bake_for_stem = meta_baked_speed(meta)
                if timeline_final and abs(bake_for_stem - 1.0) > 0.02:
                    from pipeline.core.jobs import run_cmd
                    from pipeline.core.media import atempo_chain, _atomic_replace

                    stem_dest = (
                        root
                        / "cache"
                        / f"stem_display_s{int(round(bake_for_stem * 100)):03d}.wav"
                    )
                    if not stem_dest.is_file() or stem_dest.stat().st_size < 64:
                        tmp = stem_dest.with_suffix(".tmp.wav")
                        run_cmd(
                            project_id,
                            [
                                "ffmpeg",
                                "-y",
                                "-hide_banner",
                                "-loglevel",
                                "error",
                                "-i",
                                str(source_audio),
                                "-filter:a",
                                atempo_chain(bake_for_stem),
                                "-c:a",
                                "pcm_s16le",
                                str(tmp),
                            ],
                        )
                        _atomic_replace(tmp, stem_dest)
                    source_audio = stem_dest
                source_audio = retime_audio_track(
                    source_audio,
                    source_timeline_segments,
                    root / "cache",
                    project_id,
                    base_speed=1.0,
                    source_start=export_start,
                    source_duration=source_timeline_duration,
                )
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
            mux_dub(
                project_id,
                burned,
                segments,
                original_audio_mode="original" if source_audio else audio_mode,
                source_audio=source_audio,
                original_audio_volume=bg_vol,
                # Không chậm cả video lúc mux — timeline editor là chuẩn
                allow_video_slowdown=False,
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

        if do_video:
            resolution_label = "gốc" if target_height is None else f"{target_height}p"
            aspect_hint = f" · khung {aspect}" if crop_box else ""
            post_done = bool(render_info.get("post_applied"))
            set_status(
                project_id,
                step="export",
                progress=90,
                message=progress_msg(
                    "Đóng gói video" if post_done else f"Encode {resolution_label}",
                    extra=aspect_hint.strip(" ·") or None,
                ),
                running=True,
            )
            check_cancel(project_id)
            # P1.5: ffgraph đã gộp crop+scale vào lệnh burn → chỉ còn remux
            # (video copy, audio chuẩn hoá aac) thay vì encode toàn bộ lần 2.
            encode_export_1080(
                out,
                out,
                project_id=project_id,
                target_height=None if post_done else target_height,
                crop=None if post_done else crop_box,
                video_scale_x=100.0 if post_done else video_scale_x,
                video_scale_y=100.0 if post_done else video_scale_y,
            )

        exports, easy, audio_rel, render_id, render_name = write_export_artifacts(
            meta, settings, out, project_id, segments, do_video,
        )
        out_dur = ffprobe_duration(out)
        ow, oh = video_size(out) if do_video else (0, 0)
        easy_rel = export_display_path(easy) if do_video else audio_rel
        if do_video:
            meta["outputPath"] = str(out.resolve())
            meta["outputRel"] = easy_rel
            meta["exportCopy"] = easy_rel
            meta["exportSize"] = f"{ow}x{oh}"
        meta["lastRenderId"] = render_id
        meta["lastRenderName"] = render_name
        meta["exportOutputDir"] = str(exports)
        save_meta(project_id, meta)
        parts = []
        if do_video: parts.append(f"Video {ow}x{oh} ({out_dur:.1f}s)")
        if do_audio: parts.append("Audio")
        if do_srt:   parts.append("SRT")
        if do_gif:   parts.append("GIF")
        prefix = f"preview {preview_sec}s" if preview_sec > 0 else "full"
        done = f"Xong {prefix} · " + " + ".join(parts) + (f" -- {easy_rel}" if easy_rel else "")
        set_status(
            project_id,
            step="export",
            progress=100,
            message=done,
            running=False,
            outputRel=easy_rel or None,
            outputPath=str(out.resolve()) if do_video else None,
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
        if nested:
            raise
        return
    except Exception as e:
        # Giữ progress hiện tại — UI thấy dừng ở đâu, không nhảy 0 rồi biến mất
        err = short_cmd_error(e)
        try:
            from pipeline.core.app_log import append_exception

            append_exception(f"[export:{project_id}] FAILED", e)
        except Exception:
            pass
        set_status(
            project_id,
            step="export",
            message=f"Xuất lỗi: {err}",
            running=False,
            error=err,
        )
        if nested:
            raise
    finally:
        if not nested and job_gen is not None:
            clear_job(project_id, job_gen)
