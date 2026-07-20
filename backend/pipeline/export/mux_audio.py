"""Mux dub / original audio onto video (ffmpeg)."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..core.jobs import run_cmd
from ..core.media import _has_audio_stream, ffprobe_duration, h264_encoder_args
from ..core.project import ensure_layout, out_final, set_status



from .stem import (
    _num,
    find_cached_no_vocals,
    read_stem_progress,
    resolve_stem_source_video,
    set_stem_progress,
)
from pipeline.core.jobs import run_cmd, check_cancel
from pipeline.core.media import ffprobe_duration
from pipeline.core.project import ensure_layout, load_meta, set_status

# preferVideo: chậm cố định 0.80× (setpts 1/0.8)
PREFER_VIDEO_SPEED = 0.80
PREFER_VIDEO_FACTOR = 1.0 / PREFER_VIDEO_SPEED  # 1.25


def _bg_duck_expr(
    segments: list[dict[str, Any]],
    keep: float = 0.35,
    duck: float = 0.12,
    *,
    force_flat: bool = False,
) -> str:
    """ffmpeg volume= expr: duck during speech windows, else keep BGM.

    force_flat / >12 cửa sổ / video dài → volume hằng (tránh expr hàng trăm between).
    """
    keep_s = f"{float(keep):.4f}"
    duck_s = f"{float(duck):.4f}"
    if force_flat:
        # stem no_vocals: không cần duck theo từng câu
        return keep_s
    ranges: list[tuple[float, float]] = []
    for seg in segments:
        try:
            s, e = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e <= s:
            continue
        ranges.append((s, e))
    ranges.sort()
    merged: list[list[float]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1] + 0.5:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    if not merged:
        return keep_s
    # Hard cap — Windows/ffmpeg gãy với if(between+…)+ dài
    if len(merged) > 12:
        mid = (float(keep) + float(duck)) * 0.5
        return f"{mid:.4f}"
    windows = [f"between(t\\,{s:.3f}\\,{e:.3f})" for s, e in merged]
    expr = f"if({'+'.join(windows)}\\,{duck_s}\\,{keep_s})"
    if len(expr) > 800:
        mid = (float(keep) + float(duck)) * 0.5
        return f"{mid:.4f}"
    return expr


def _source_audio_filter(mode: str) -> str:
    """FFmpeg stem approximation for stereo sources (fast, no model download)."""
    stereo = "aformat=channel_layouts=stereo"
    if mode == "vocals":
        return (
            stereo
            + ",pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
            + ",dialoguenhance=enhance=2.0:voice=4.0"
        )
    if mode == "music":
        # Hạ mid (lời thường ở giữa), giữ side (nhạc), bù gain mạnh hơn.
        return stereo + ",stereotools=mlev=0.22:slev=1.25,volume=3.6"
    return "anull"


def _atempo_chain(ratio: float) -> str:
    from pipeline.core.media import atempo_chain

    return atempo_chain(ratio)


def plan_video_slowdown_factor(
    segments: list[dict[str, Any]],
    root: Path,
    *,
    match: str = "preferVideo",
) -> float:
    """Chỉ tính video_factor (>1 = chậm toàn video). Dùng lúc dub + xuất."""
    _clips, vf = _tts_clip_plan(segments, root, allow_video_slowdown=True, match=match)
    return float(vf)


def _tts_clip_plan(
    segments: list[dict[str, Any]],
    root: Path,
    *,
    allow_video_slowdown: bool = True,
    match: str = "preferVideo",
    bake_speed: float = 1.0,
) -> tuple[list[tuple[Path, float, float, float, float]], float]:
    """Trả (clips, video_factor).

    video_factor > 1 = chậm **toàn bộ** video để TTS gần tốc độ tự nhiên.
    clips: (wav, start_sec_scaled, play_sec, tts_speed, volume)

    bake_speed: tốc độ đã bake vào video (0.5–2). Wav TTS luôn 1× →
    atempo *= bake_speed để giọng nhanh/chậm cùng nhịp timeline đã scale.

    preferVideo đã bake 0.80×: **cascade** — không atrim giữa câu.
    start_i = max(seg.start, prev_end + gap); speed nhẹ ≤1.25; full audio.
    """
    bake = max(0.5, min(2.0, float(bake_speed or 1.0)))
    ordered = sorted(
        [s for s in segments if s],
        key=lambda s: float(s.get("start") or 0),
    )
    gap = 0.03
    # Timeline đã giãn bằng retime_video_segments (videoSpeed) khi TTS dài.
    # Ở đây: full TTS, speed ≈ 1; chỉ atempo nhẹ nếu vẫn tràn.
    baked_prefer = match == "preferVideo" and not allow_video_slowdown
    if match == "preferVideo":
        max_video_factor = PREFER_VIDEO_FACTOR
        soft_tts_speed = 1.06
        hard_tts_cap = 1.15  # sau retime hiếm khi cần; không cắt
        fixed_factor = 1.0 if baked_prefer else (
            PREFER_VIDEO_FACTOR if allow_video_slowdown else 1.0
        )
    elif match == "none":
        max_video_factor = 1.45
        soft_tts_speed = 1.08
        hard_tts_cap = 1.30
        fixed_factor = None
    else:
        max_video_factor = 1.35
        soft_tts_speed = 1.12
        hard_tts_cap = 1.45
        fixed_factor = None

    raw: list[tuple[Path, float, float, float, float, float]] = []
    for i, seg in enumerate(ordered):
        name = seg.get("audioFile") or f"{seg['id']}.wav"
        wav = root / "tts" / name
        if not wav.exists():
            wav = root / "tts" / f"{seg['id']}.wav"
        if not wav.exists():
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        ad = float(seg.get("audioDuration") or 0)
        if ad <= 0.05:
            ad = ffprobe_duration(wav) or 0.0
        next_start = None
        for j in range(i + 1, len(ordered)):
            ns = float(ordered[j].get("start") or 0)
            if ns > start + 0.02:
                next_start = ns
                break
        if next_start is not None:
            slot0 = max(0.12, next_start - start - gap)
        else:
            # câu cuối / sau retime: đủ chỗ full TTS
            slot0 = max(0.15, ad + 0.15 if ad > 0.05 else end - start + 0.12)
        # Lồng tiếng giữ nguyên 1x, không nhân với bake speed
        manual = max(0.75, min(1.5, _num(seg.get("ttsSpeed"), 1)))
        manual = max(0.5, min(2.0, manual))
        raw.append(
            (
                wav,
                start,
                slot0,
                ad,
                max(0.0, min(2.0, _num(seg.get("ttsVolume"), 100) / 100)),
                manual,
            )
        )

    if not raw:
        return [], (fixed_factor if fixed_factor is not None else 1.0)

    video_factor = 1.0
    if fixed_factor is not None:
        video_factor = float(fixed_factor)
    elif allow_video_slowdown:
        needs: list[float] = []
        for _wav, _start, slot0, ad, _volume, manual_speed in raw:
            ad_m = ad / manual_speed
            if ad_m > 0.08 and slot0 > 0.05 and ad_m > slot0 * soft_tts_speed:
                needs.append(ad_m / (slot0 * soft_tts_speed))
        if needs:
            needs.sort()
            idx = min(len(needs) - 1, max(0, int(len(needs) * 0.90) - 1))
            video_factor = needs[idx]
            mid = needs[len(needs) // 2]
            video_factor = max(video_factor, min(mid, max_video_factor))
        video_factor = min(max_video_factor, max(1.0, video_factor))

    # Full TTS: trim = toàn bộ audio sau atempo nhẹ; không atrim theo slot ngắn
    clips: list[tuple[Path, float, float, float, float]] = []
    for wav, start, slot0, ad, volume, manual_speed in raw:
        slot = slot0 * video_factor
        ad_eff = ad / max(manual_speed, 0.05) if ad > 0.05 else slot
        speed = 1.0
        if ad_eff > 0.08 and ad_eff > slot * soft_tts_speed:
            speed = min(hard_tts_cap, ad_eff / max(slot, 0.05))
            speed = max(1.0, speed)
        played = ad_eff / max(speed, 0.05) if ad_eff > 0.05 else slot
        # Ưu tiên đọc hết — trim = full play (slot đã giãn bởi retime)
        trim = max(0.08, played + 0.04)
        # sp cuối cùng luôn trong khoảng atempo chain xử lý được
        sp_out = max(0.5, min(4.0, speed * manual_speed))
        clips.append((wav, start * video_factor, trim, sp_out, volume))
    clips.sort(key=lambda c: c[1])
    out: list[tuple[Path, float, float, float, float]] = []
    for i, (wav, start, trim, sp, volume) in enumerate(clips):
        next_start = clips[i + 1][1] if i + 1 < len(clips) else None
        if next_start is not None and trim > next_start - start - 0.02:
            # retime đã đẩy câu sau — hiếm; chỉ siết nếu vẫn đè
            room = max(0.08, next_start - start - 0.02)
            if trim > room * 1.02:
                # tăng tốc thêm thay vì cắt (giữ full nếu có thể)
                need_sp = sp * (trim / room)
                if need_sp <= hard_tts_cap * 1.05:
                    sp = min(hard_tts_cap, need_sp)
                    trim = room
                else:
                    trim = room
        out.append((wav, start, trim, sp, volume))
    return out, video_factor


def _mix_tts_track(
    project_id: str,
    segments: list[dict[str, Any]],
    root: Path,
    *,
    video_factor: float = 1.0,
    allow_video_slowdown: bool = True,
    match: str = "preferVideo",
    bake_speed: float = 1.0,
) -> Path:
    """Trộn TTS theo timeline đã scale. TTS atempo = manual × bake_speed."""
    ordered_plan, plan_vf = _tts_clip_plan(
        segments,
        root,
        allow_video_slowdown=allow_video_slowdown,
        match=match,
        bake_speed=bake_speed,
    )
    # Dùng plan (đã tính factor); video_factor chỉ để cache key khớp mux_dub
    if abs(video_factor - plan_vf) > 0.02 and video_factor > 1.0:
        # re-scale starts/slots if caller forces different factor
        scale = video_factor / max(plan_vf, 1e-6)
        ordered_plan = [
            (w, s * scale, slot * scale, sp, volume)
            for w, s, slot, sp, volume in ordered_plan
        ]
        plan_vf = video_factor

    if not ordered_plan:
        raise RuntimeError("Chưa có audio TTS — chạy Lồng tiếng trước.")

    signature = [
        f"{w.name}@{s:.3f}@{slot:.3f}@{sp:.3f}@{volume:.3f}"
        for w, s, slot, sp, volume in ordered_plan
    ]
    key = hashlib.sha1(
        (f"v11|retime-fit|vf{plan_vf:.3f}|{match}|" + "|".join(signature)).encode()
    ).hexdigest()[:16]
    out = root / "cache" / f"tts_mix_{key}.wav"
    if out.exists():
        return out

    batch_size = 20
    batches: list[Path] = []
    for batch_i, offset in enumerate(range(0, len(ordered_plan), batch_size)):
        batch = ordered_plan[offset : offset + batch_size]
        batch_out = root / "cache" / f"tts_mix_{key}_part{batch_i}.wav"
        inputs: list[str] = []
        filters: list[str] = []
        labels: list[str] = []
        for i, (wav, start_sec, max_sec, speed, volume) in enumerate(batch):
            delay_ms = max(0, int(start_sec * 1000))
            inputs += ["-i", str(wav)]
            parts: list[str] = []
            # Luôn qua chain — không emit atempo thô <0.5
            sp = max(0.25, min(4.0, float(speed) or 1.0))
            if abs(sp - 1.0) >= 0.03:
                parts.append(_atempo_chain(sp))
            parts.append(f"volume={max(0.0, min(2.0, float(volume))):.3f}")
            # max_sec = full play duration (cascade); pad nhỏ tránh cắt sample cuối
            play_sec = max(0.08, float(max_sec) + 0.05)
            fade = min(0.03, max(0.012, play_sec * 0.02))
            st_fade = max(0.0, play_sec - fade)
            parts.append(f"atrim=0:{play_sec:.3f}")
            parts.append("asetpts=PTS-STARTPTS")
            parts.append(f"afade=t=out:st={st_fade:.3f}:d={fade:.3f}")
            parts.append(f"adelay={delay_ms}|{delay_ms}")
            filters.append(f"[{i}:a]" + ",".join(parts) + f"[a{i}]")
            labels.append(f"[a{i}]")
        filters.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95[aout]"
        )
        fc = root / "cache" / f"tts_mix_{key}_part{batch_i}_fc.txt"
        fc.write_text(";\n".join(filters) + "\n", encoding="utf-8")
        try:
            run_cmd(
                project_id,
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    *inputs,
                    "-filter_complex_script",
                    str(fc),
                    "-map",
                    "[aout]",
                    "-c:a",
                    "pcm_s16le",
                    str(batch_out),
                ],
            )
        finally:
            try:
                fc.unlink(missing_ok=True)
            except OSError:
                pass
        batches.append(batch_out)

    if len(batches) == 1:
        batches[0].replace(out)
    else:
        inputs = [arg for wav in batches for arg in ("-i", str(wav))]
        labels = "".join(f"[{i}:a]" for i in range(len(batches)))
        fc_join = root / "cache" / f"tts_mix_{key}_join_fc.txt"
        fc_join.write_text(
            labels
            + f"amix=inputs={len(batches)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95[aout]\n",
            encoding="utf-8",
        )
        try:
            run_cmd(
                project_id,
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    *inputs,
                    "-filter_complex_script",
                    str(fc_join.resolve()),
                    "-map",
                    "[aout]",
                    "-c:a",
                    "pcm_s16le",
                    str(out),
                ],
            )
        finally:
            try:
                fc_join.unlink(missing_ok=True)
            except OSError:
                pass
        for wav in batches:
            wav.unlink(missing_ok=True)
    return out


def mux_dub(
    project_id: str,
    video: Path,
    segments: list[dict[str, Any]],
    *,
    original_audio_mode: str = "auto",
    source_audio: Path | None = None,
    original_audio_volume: float = 1.0,
    allow_video_slowdown: bool = True,
    match: str = "preferVideo",
    bake_speed: float = 1.0,
) -> Path:
    """Đặt TTS theo timeline; atempo TTS theo bake_speed (wav 1×)."""
    root = ensure_layout(project_id)
    duration = ffprobe_duration(video)
    _clips, video_factor = _tts_clip_plan(
        segments,
        root,
        allow_video_slowdown=allow_video_slowdown,
        match=match,
        bake_speed=bake_speed,
    )
    voice_track = _mix_tts_track(
        project_id,
        segments,
        root,
        video_factor=video_factor,
        allow_video_slowdown=allow_video_slowdown,
        match=match,
        bake_speed=bake_speed,
    )
    out_dur = duration * video_factor
    vol_mul = max(0.0, min(1.0, float(original_audio_volume)))
    inputs = ["-i", str(video)]
    filters: list[str] = []
    source_audio_index = 0
    next_input_index = 1
    use_preseparated = (
        source_audio is not None
        and source_audio.exists()
        and _has_audio_stream(source_audio)
    )
    if use_preseparated:
        inputs += ["-i", str(source_audio)]
        source_audio_index = 1
        next_input_index = 2
    voice_idx = next_input_index
    inputs += ["-i", str(voice_track)]

    # Stem đã xóa lời: nền to, duck nhẹ khi TTS. Audio gốc: duck mạnh hơn.
    # vol_mul (0–1) từ slider UI nhân vào keep/duck.
    if use_preseparated:
        keep, duck = 1.0 * vol_mul, 0.62 * vol_mul
    else:
        keep, duck = 0.42 * vol_mul, 0.14 * vol_mul
    has_bg = (
        vol_mul > 0.001
        and original_audio_mode != "mute"
        and (use_preseparated or _has_audio_stream(video))
    )
    # Duck windows scale theo video_factor
    duck_segs = segments
    if abs(video_factor - 1.0) > 0.001:
        duck_segs = []
        for s in segments:
            ss = dict(s)
            ss["start"] = float(s.get("start") or 0) * video_factor
            ss["end"] = float(s.get("end") or 0) * video_factor
            duck_segs.append(ss)

    # Chuẩn hóa TTS stereo fltp (tránh amix fail / exit -34)
    filters.append(
        f"[{voice_idx}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=whole_dur={out_dur:.3f}[voice]"
    )

    if has_bg:
        vol = _bg_duck_expr(
            duck_segs,
            keep=keep,
            duck=duck,
            force_flat=bool(use_preseparated),
        )
        if use_preseparated:
            source_filter = "anull"
        elif original_audio_mode in ("vocals", "music", "no_vocals"):
            mode = "music" if original_audio_mode == "no_vocals" else original_audio_mode
            source_filter = _source_audio_filter(mode)
        else:
            source_filter = "anull"
        # video_factor lớn → tempo = 1/vf có thể <0.5 → bắt buộc chain
        bg_tempo = (
            _atempo_chain(1.0 / max(video_factor, 1e-6))
            if video_factor > 1.001
            else "anull"
        )
        vol_clean = str(vol).strip()
        try:
            float(vol_clean)
            vol_part = f"volume={vol_clean}"
        except ValueError:
            vol_part = f"volume='{vol_clean}':eval=frame"
        filters.append(
            f"[{source_audio_index}:a]{source_filter},{bg_tempo},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"apad=whole_dur={out_dur:.3f},{vol_part}[bg]"
        )
        filters.append(
            "[bg][voice]amix=inputs=2:duration=first:dropout_transition=0:"
            "normalize=0[aout]"
        )
        map_audio = "[aout]"
    else:
        map_audio = "[voice]"

    # Video: chậm nhẹ nếu cần (setpts > 1)
    if video_factor > 1.001:
        filters.append(f"[0:v]setpts={video_factor:.4f}*PTS[vout]")
        map_video = "[vout]"
        vcodec = h264_encoder_args(fast=True)
    else:
        map_video = "0:v"
        vcodec = ["-c:v", "copy"]

    out = out_final(project_id)
    # LUÔN script file — không bao giờ nhét filter vào argv (Windows)
    fc_path = root / "cache" / "mux_fc.txt"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    # filter_complex_script: dùng ';' một dòng hoặc xuống dòng — không BOM
    fc_body = ";\n".join(filters) + "\n"
    fc_path.write_text(fc_body, encoding="utf-8")
    # Giữ bản debug khi fail
    fc_dbg = root / "cache" / "mux_fc_last.txt"
    try:
        fc_dbg.write_text(fc_body, encoding="utf-8")
    except OSError:
        pass
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex_script",
            str(fc_path.resolve()),
            "-map",
            map_video,
            "-map",
            map_audio,
            *vcodec,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-shortest",
            "-t",
            f"{float(out_dur):.3f}",
            str(out),
        ]
        # Guard: argv không được chứa filter_complex dài
        assert all(
            not (isinstance(a, str) and a.startswith("[") and "between(t" in a)
            for a in cmd
        ), "filter leak into argv"
        run_cmd(project_id, cmd)
    finally:
        try:
            fc_path.unlink(missing_ok=True)
        except OSError:
            pass
    return out


def mux_original_audio(
    project_id: str,
    video: Path,
    mode: str,
    *,
    source_audio: Path | None = None,
    original_audio_volume: float = 1.0,
) -> Path:
    """Xuất video chỉ với track gốc đã lọc, hoặc bỏ hoàn toàn track âm thanh."""
    out = out_final(project_id)
    vol_mul = max(0.0, min(1.0, float(original_audio_volume)))
    use_preseparated = (
        source_audio is not None
        and source_audio.exists()
        and _has_audio_stream(source_audio)
    )
    cmd = ["ffmpeg", "-y", "-i", str(video)]
    if use_preseparated:
        cmd += ["-i", str(source_audio)]
    cmd += ["-map", "0:v", "-c:v", "copy"]
    if mode == "mute" or vol_mul <= 0.001:
        cmd += ["-an"]
    elif use_preseparated:
        # Stem Demucs — volume slider
        if abs(vol_mul - 1.0) > 0.01:
            cmd += [
                "-map", "1:a:0",
                "-af", f"volume={vol_mul:.3f}",
                "-c:a", "aac",
            ]
        else:
            cmd += ["-map", "1:a:0", "-c:a", "aac"]
    elif not _has_audio_stream(video):
        cmd += ["-an"]
    else:
        af = (
            _source_audio_filter("music")
            if mode == "no_vocals"
            else _source_audio_filter(mode)
        )
        if abs(vol_mul - 1.0) > 0.01:
            af = f"{af},volume={vol_mul:.3f}"
        cmd += ["-map", "0:a:0", "-af", af, "-c:a", "aac"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-shortest", str(out)]
    run_cmd(project_id, cmd)
    return out

