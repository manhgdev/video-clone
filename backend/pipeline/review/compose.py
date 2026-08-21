"""Compile EditPlan clips with existing FFmpeg encoder (NVENC/VideoToolbox/libx264)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pipeline.core.jobs import check_cancel, run_cmd
from pipeline.core.media import _ff_bin, atempo_chain, h264_encoder_args


def crop_filter(ratio: str, width: int, height: int) -> str:
    if ratio == "9:16" and width > 0 and height > 0:
        target_w = int(height * 9 / 16)
        if target_w < width:
            x = max(0, (width - target_w) // 2)
            return f"crop={target_w}:{height}:{x}:0,scale=1080:1920"
        return "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    if ratio == "1:1" and width > 0 and height > 0:
        side = min(width, height)
        x = max(0, (width - side) // 2)
        y = max(0, (height - side) // 2)
        return f"crop={side}:{side}:{x}:{y},scale=1080:1080"
    return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"


def compose_video(
    source: Path,
    plan: dict[str, Any],
    dest: Path,
    *,
    ratio: str,
    width: int,
    height: int,
    job_id: str | None = None,
    original_pct: float = 0,
    clip_workers: int | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    clips: list[dict[str, Any]] = []
    for seg in plan.get("segments") or []:
        for clip in seg.get("clips") or []:
            clips.append(clip)
    if not clips:
        raise RuntimeError("RENDER_ERROR: EditPlan không có clip")
    vf0 = crop_filter(ratio, width, height)
    # Each Review window can compose concurrently; keep its transient clips isolated.
    cache = dest.parent / "compose_parts" / dest.stem
    cache.mkdir(parents=True, exist_ok=True)
    want_audio = float(original_pct or 0) > 0.5
    hw = h264_encoder_args(fast=True)
    sw = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709",
    ]

    def clip_cmd(clip: dict[str, Any], video_args: list[str], part: Path) -> list[str]:
        start = float(clip["source_start"])
        end = float(clip["source_end"])
        src_dur = max(0.12, end - start)
        target = max(0.12, float(clip.get("target_duration") or src_dur))
        factor = target / src_dur
        vf = vf0 if abs(factor - 1.0) < 0.02 else f"{vf0},setpts={factor:.6f}*PTS"
        cmd = [
            _ff_bin("ffmpeg"), "-y",
            "-ss", f"{start:.3f}", "-t", f"{src_dur:.3f}", "-i", str(source),
        ]
        if want_audio:
            tempo = atempo_chain(src_dur / target)
            cmd += [
                "-vf", vf, *video_args,
                "-af", f"{tempo},volume={max(0.0, min(1.0, float(original_pct) / 100.0)):.3f}",
                "-c:a", "aac", "-ac", "2", "-ar", "44100",
            ]
        else:
            cmd += [
                "-f", "lavfi", "-t", f"{target:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map", "0:v", "-map", "1:a",
                "-vf", vf, *video_args,
                "-c:a", "aac", "-ac", "2", "-ar", "44100", "-shortest",
            ]
        cmd.append(str(part))
        return cmd

    def encode_one(item: tuple[int, dict[str, Any]]) -> tuple[int, Path]:
        i, clip = item
        if job_id:
            check_cancel(job_id)
        part = cache / f"p{i:04d}.mp4"
        cmd = clip_cmd(clip, hw, part)
        try:
            run_cmd(job_id, cmd) if job_id else subprocess.run(cmd, check=True, timeout=180)
        except (subprocess.CalledProcessError, RuntimeError):
            if hw[1] == "libx264":
                raise
            cmd = clip_cmd(clip, sw, part)
            run_cmd(job_id, cmd) if job_id else subprocess.run(cmd, check=True, timeout=180)
        return i, part

    if len(clips) == 1:
        parts = [encode_one((0, clips[0]))[1]]
    else:
        import os
        from pipeline.core.resources import run_with_adaptive_workers

        cores = max(1, os.cpu_count() or 4)
        hard = max(8, min(16, int(cores * 0.75)))
        if clip_workers is not None and int(clip_workers) > 0:
            hard = min(hard, int(clip_workers))
        hard = max(1, min(hard, len(clips)))
        rows = run_with_adaptive_workers(
            list(enumerate(clips)),
            encode_one,
            kind="cpu",
            # Always elastic under the ceiling — fixed 8 was leaving M-series idle.
            requested=0,
            cap=hard,
            thread_name_prefix="rv-ff",
            cancel_check=(lambda: check_cancel(job_id)) if job_id else None,
        )
        parts = [p for _, p in sorted(rows, key=lambda r: r[0])]
    return concat_parts(parts, dest, job_id=job_id)


def concat_parts(
    parts: list[Path],
    dest: Path,
    job_id: str | None = None,
    *,
    reencode_fallback: bool = True,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Concurrent Review windows share a run directory, so their concat lists
    # must not overwrite one another.
    listing = dest.parent / f"concat_{dest.stem}.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    cmd = [
        _ff_bin("ffmpeg"), "-y", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy", "-fflags", "+genpts", str(dest),
    ]
    try:
        run_cmd(job_id, cmd) if job_id else subprocess.run(cmd, check=True, timeout=600)
    except (subprocess.CalledProcessError, RuntimeError):
        if not reencode_fallback:
            raise
        cmd = [
            _ff_bin("ffmpeg"), "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ac", "2", str(dest),
        ]
        run_cmd(job_id, cmd) if job_id else subprocess.run(cmd, check=True, timeout=600)
    return dest
