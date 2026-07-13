"""ffmpeg/ffprobe helpers and hardware probe."""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Any

from .jobs import run_cmd

def hardware() -> dict[str, str]:
    machine = platform.machine()
    system = platform.system()
    if system == "Darwin":
        return {"label": f"Metal ({machine})", "accel": "metal"}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
        if out:
            return {"label": f"CUDA ({out.splitlines()[0]})", "accel": "cuda"}
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return {"label": f"CPU ({machine})", "accel": "cpu"}


def ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _has_audio_stream(path: Path) -> bool:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            text=True,
            timeout=15,
        )
        return bool(out.strip())
    except (FileNotFoundError, subprocess.SubprocessError):
        return False

def ensure_preview_clip(
    source: Path, dest: Path, sec: float, project_id: str | None = None
) -> Path:
    """Cắt N giây đầu để thử nhanh; cache theo dest path."""
    if dest.exists() and dest.stat().st_mtime >= source.stat().st_mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: -c copy nhanh; lỗi codec thì re-encode
    try:
        run_cmd(
            project_id,
            [
                "ffmpeg",
                "-y",
                "-ss",
                "0",
                "-t",
                str(sec),
                "-i",
                str(source),
                "-c",
                "copy",
                str(dest),
            ],
        )
    except Exception:
        run_cmd(
            project_id,
            [
                "ffmpeg",
                "-y",
                "-ss",
                "0",
                "-t",
                str(sec),
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(dest),
            ],
        )
    return dest

def extract_audio(video: Path, wav: Path, project_id: str | None = None) -> None:
    run_cmd(
        project_id,
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav),
        ],
    )

def video_size(path: Path) -> tuple[int, int]:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        text=True,
    ).strip()
    w, h = out.split("x")
    return int(w), int(h)


def encode_export_1080(
    src: Path,
    dst: Path,
    project_id: str | None = None,
) -> Path:
    """Xuất 1080p: dọc 1080×?, ngang ?×1080; H.264 chất lượng cao."""
    w, h = video_size(src)
    # portrait → cạnh ngắn (width) = 1080; landscape → height = 1080
    if h >= w:
        vf = "scale=1080:-2"
    else:
        vf = "scale=-2:1080"
    tmp = dst.with_suffix(".tmp1080.mp4")
    tmp.unlink(missing_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        project_id,
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            str(tmp),
        ],
    )
    tmp.replace(dst)
    return dst
