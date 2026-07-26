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

from pipeline.orchestrate.tts_fit import assign_tts_fit_speeds


def _logo_schedule(item: dict[str, Any], st: float, en: float, x: float, y: float) -> list[tuple[float, float, float, float, float]]:
    if str(item.get("motion") or "fixed") != "random":
        return [(st, en, x, y, 0.0)]
    frames = item.get("positionKeyframes") or [{"at": st, "x": x, "y": y}]
    visible = max(0.5, float(item.get("visibleSec") or 4))
    fade = min(max(0.0, float(item.get("fadeSec") or 0.5)), visible / 2)
    return [
        (fst, min(en, fst + visible), float(frame.get("x") or 0), float(frame.get("y") or 0), fade)
        for frame in frames
        if (fst := max(st, float(frame.get("at") if frame.get("at") is not None else st))) < en
    ]

import re as _re

def _project_slug(meta: dict) -> str:
    """Slug an toàn từ tên file video nguồn — dùng làm subfolder trong exports."""
    vp = str(meta.get("videoPath") or "")
    stem = Path(vp).stem if vp else ""
    slug = _re.sub(r"[^\w\s-]", "", stem).strip()
    slug = _re.sub(r"[\s_]+", "-", slug)
    slug = slug[:48].strip("-") or "project"
    return slug.lower()


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
    # FE timeline sau Áp dụng = chuẩn. base luôn 1.0 — chỉ nướng videoSpeed câu (TTS-fit).
    timeline_final = _timeline_is_final(meta, video)
    retime_base = _export_retime_base(meta, video, match_mode)
    video, segments = retime_video_segments(
        video,
        segments,
        root / "cache",
        project_id,
        base_speed=retime_base,
    )
    vid_dur = ffprobe_duration(video) or vid_dur
    # Free text + effect regions (làm mờ tự do): cùng hệ tọa độ pixel.
    text_overlays: list[dict[str, Any]] = []
    for item in meta.get("overlays") or []:
        item_start = float(item.get("start") or 0) - export_start
        if item_start >= vid_dur:
            continue
        kind = str(item.get("kind") or "text").lower()
        x = float(item.get("x") or 0)
        y = float(item.get("y") or 0)
        w = float(item.get("w") or 0)
        h = float(item.get("h") or 0)
        if w < 4 or h < 4:
            continue
        st = retime_timeline_time(
            item_start,
            source_timeline_duration,
            source_timeline_segments,
            base_speed=retime_base,
        )
        en = retime_timeline_time(
            float(item.get("end") or 0) - export_start,
            source_timeline_duration,
            source_timeline_segments,
            base_speed=retime_base,
        )
        if kind == "logo":
            asset_path = ""
            asset_url = str(item.get("assetUrl") or "")
            if asset_url.startswith(f"/data/{project_id}/"):
                candidate = (root / asset_url.split(f"/data/{project_id}/", 1)[1]).resolve()
                if root.resolve() in candidate.parents and candidate.is_file():
                    asset_path = str(candidate)
            for index, (fst, fen, fx, fy, fade) in enumerate(_logo_schedule(item, st, en, x, y)):
                if fen <= fst:
                    continue
                source = str(item.get("logoSource") or "text")
                text = str(item.get("text") or "Logo").strip() or "Logo"
                fs = int(item.get("fontSize") or 42)
                text_overlays.append({
                    "id": f"logo-{item.get('id', '')}-{index}", "start": fst, "end": fen,
                    "translation": text if source == "text" else "logo", "source": "", "layout": "horizontal",
                    "fontSize": fs, "fontFamily": str(item.get("fontFamily") or "system"),
                    "textColor": str(item.get("color") or "#ffffff"),
                    "bbox": {"x": fx, "y": fy, "w": w, "h": h},
                    "captionLayout": {"x": fx, "y": fy, "w": w, "h": h, "lines": [text], "fontSize": fs},
                    "skipCoverMask": True, "logoText": source == "text",
                    "logoAssetPath": asset_path if source != "text" else "",
                    "logoOpacity": max(0, min(100, int(item.get("opacity") or 85))) / 100,
                    "logoFadeInEnd": fst + fade, "logoFadeOutStart": fen - fade,
                })
            continue
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
                # textarea preview: top-align + line-height 1.25 (css mode overlay)
                "overlayText": True,
            }
        )
    out = out_final(project_id)

    # Cờ xuất độc lập — mặc định video=True nếu không có gì được chọn
    do_video = bool(settings.get("exportVideo", True))
    do_audio = bool(settings.get("exportAudio", False))
    do_srt   = bool(settings.get("exportSrt",   False))
    do_gif   = bool(settings.get("exportGif",   False))
    if not any([do_video, do_audio, do_srt, do_gif]):
        do_video = True

    # cover / burn độc lập; "Không dịch" → không chèn caption
    no_translate = str(settings.get("targetLang") or "") in ("none", "off", "source", "")
    cover = bool(settings.get("coverHardsubs", True))
    burn = bool(settings.get("burnSubs", True)) and not no_translate

    # ── Fast path: chỉ xuất SRT (không cần render video) ──
    if do_srt and not do_video and not do_audio and not do_gif:
        try:
            from pipeline.export.srt import write_srt
            _custom_dir = str(settings.get("exportOutputDir") or "").strip()
            _slug = _project_slug(meta)
            exports = (Path(_custom_dir) if _custom_dir else PUBLIC_DATA / "exports") / _slug
            exports.mkdir(parents=True, exist_ok=True)
            render_id = f"{project_id}-{time.time_ns()}"
            render_name = str(meta.pop("pendingRenderName", "")).strip() or f"Render {project_id}"
            set_status(project_id, step="export", progress=50, message="Xuất chú thích (SRT)…", running=True)
            check_cancel(project_id)
            fmt = str(settings.get("exportSrtFormat") or "srt").lower()
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
            write_srt(srt_out, cues, capcut=False)
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
            # Crop khung + encode độ phân giải trong **một** pass (tránh encode 2 lần → chậm)
            aspect = str(settings.get("previewAspectRatio") or "original")
            custom_crop = settings.get("previewCrop")
            crop_box = None
            if aspect not in ("", "original") and (aspect != "custom" or custom_crop):
                sw, sh = video_size(out)
                crop_box = resolve_export_crop(sw, sh, aspect, custom_crop)

            resolution = str(settings.get("exportResolution") or "1080").lower()
            allowed_resolutions = {"144", "240", "360", "480", "720", "1080", "1440", "2160"}
            if resolution != "original" and resolution not in allowed_resolutions:
                resolution = "1080"
            target_height = None if resolution == "original" else int(resolution)
            resolution_label = "gốc" if target_height is None else f"{target_height}p"
            aspect_hint = f" · khung {aspect}" if crop_box else ""
            set_status(
                project_id,
                step="export",
                progress=90,
                message=progress_msg(f"Encode {resolution_label}", extra=aspect_hint.strip(" ·") or None),
                running=True,
            )
            check_cancel(project_id)
            encode_export_1080(
                out,
                out,
                project_id=project_id,
                target_height=target_height,
                crop=crop_box,
            )

        # ban de tim: backend/public/exports/<slug>/<id>.mp4
        _custom_dir = str(settings.get("exportOutputDir") or "").strip()
        if _custom_dir:
            exports = Path(_custom_dir)
            _slug = _project_slug(meta)
            exports = exports / _slug
        else:
            exports = PUBLIC_DATA / project_id / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        render_id = f"{project_id}-{time.time_ns()}"
        render_name = str(meta.pop("pendingRenderName", "")).strip() or f"Render {project_id}"
        import re as _re_ext
        safe_name = _re_ext.sub(r'[^\w\s-]', '', render_name).strip()
        safe_name = _re_ext.sub(r'[-\s]+', '-', safe_name)
        if not safe_name:
            safe_name = project_id

        img_out = None
        if str(settings.get("coverDataUrl") or "").startswith("data:image/"):
            try:
                import base64
                cover_data_url = str(settings.get("coverDataUrl"))
                header, encoded = cover_data_url.split(",", 1)
                img_data = base64.b64decode(encoded)
                ext = "jpg" if "jpeg" in header else "png"
                img_out = exports / f"{safe_name}.{ext}"
                img_out.write_bytes(img_data)
            except Exception as e:
                print(f"[export] Cover image decode error: {e}", flush=True)

        if do_video:
            easy = exports / f"{safe_name}.mp4"
            if img_out and img_out.is_file():
                try:
                    from pipeline.core.jobs import run_cmd as _run_cmd
                    _run_cmd(project_id, ["ffmpeg", "-y", "-i", str(out), "-i", str(img_out), "-map", "0", "-map", "1", "-c", "copy", "-disposition:v:1", "attached_pic", str(easy)])
                except Exception as e:
                    print(f"[export] Cover image embed error: {e}", flush=True)
                    shutil.copy2(out, easy)
            else:
                shutil.copy2(out, easy)
            (exports / f"{safe_name}.json").write_text(
                json.dumps({"name": render_name, "projectId": project_id}, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            easy = out  # audio/gif dung file tam; khong luu mp4 dau ra

        # Xuất Âm thanh (MP3/WAV) nếu người dùng chọn
        audio_rel = ""
        if bool(settings.get("exportAudio", False)):
            try:
                from pipeline.core.jobs import run_cmd as _run_cmd
                fmt = str(settings.get("exportAudioFormat") or "mp3").lower()
                audio_out = exports / f"{safe_name}.{fmt}"
                acodec = "libmp3lame" if fmt == "mp3" else "pcm_s16le" if fmt == "wav" else "aac"
                _run_cmd(project_id, ["ffmpeg", "-y", "-i", str(out), "-vn", "-acodec", acodec, str(audio_out)])
                if audio_out.is_file():
                    audio_rel = export_display_path(audio_out)
                    if not do_video:
                        # Audio-only → ghi render JSON để xuất hiện trong danh sách
                        (exports / f"{safe_name}.json").write_text(
                            json.dumps({"name": render_name, "projectId": project_id, "kind": "audio"}, ensure_ascii=False),
                            encoding="utf-8",
                        )
            except Exception as ae:
                print(f"[export] Audio export error: {ae}", flush=True)

        # Xuất Chú thích (SRT) nếu người dùng chọn
        if bool(settings.get("exportSrt", False)):
            try:
                from pipeline.export.srt import write_srt
                srt_out = exports / f"{safe_name}.srt"
                cues = []
                for s in segments:
                    if not s.get("maskOnly") and (str(s.get("translation") or s.get("source") or "")).strip():
                        cues.append({
                            "start": float(s.get("start") or 0),
                            "end": float(s.get("end") or 0),
                            "text": (str(s.get("translation") or s.get("source") or "")).strip(),
                        })
                write_srt(srt_out, cues, capcut=False)
            except Exception as se:
                print(f"[export] SRT export error: {se}", flush=True)

        # Xuất GIF nếu người dùng chọn
        if bool(settings.get("exportGif", False)):
            try:
                from pipeline.core.jobs import run_cmd as _run_cmd
                gif_out = exports / f"{safe_name}.gif"
                res = int(settings.get("exportGifRes") or 480)
                vf = f"fps=10,scale={res}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
                _run_cmd(project_id, ["ffmpeg", "-y", "-i", str(out), "-vf", vf, "-loop", "0", str(gif_out)])
                if gif_out.is_file():
                    gif_rel = export_display_path(gif_out)
                    results.append(f"GIF    : {gif_rel}")
            except Exception as ge:
                print(f"[export] GIF export error: {ge}", flush=True)

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

