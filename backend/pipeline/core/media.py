"""ffmpeg/ffprobe helpers and hardware probe."""
from __future__ import annotations

import platform
import hashlib
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .jobs import run_cmd


def atempo_chain(ratio: float) -> str:
    """ffmpeg atempo chỉ [0.5, 100] — chain nhiều bước khi ngoài khoảng."""
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return "anull"
    if r <= 0 or abs(r - 1.0) < 0.01:
        return "anull"
    parts: list[str] = []
    while r > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        r *= 2.0
    r = min(100.0, max(0.5, r))
    if abs(r - 1.0) >= 0.01:
        parts.append(f"atempo={r:.4f}")
    return ",".join(parts) if parts else "anull"


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """Probe the encoder, not just ffmpeg's compiled encoder list."""
    try:
        return subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=size=256x256:rate=1",
                "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def h264_encoder_args(*, fast: bool = False) -> list[str]:
    if nvenc_available():
        return [
            "-c:v", "h264_nvenc", "-preset", "p3" if fast else "p5",
            "-tune", "hq", "-rc", "vbr", "-cq", "18", "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264", "-preset", "veryfast" if fast else "fast",
        "-crf", "18", "-pix_fmt", "yuv420p",
    ]

def detect_device() -> dict[str, Any]:
    """Probe OS + GPU cho Thiết lập / cài đặt đúng backend.

    Trả về đủ để UI quyết định: Windows/macOS/Linux, có GPU không, GPU gì,
    và gói nên cài (OCR CUDA / Demucs CUDA / demucs-mlx).
    """
    system = platform.system()
    machine = platform.machine()
    if system == "Darwin":
        os_id, os_label = "macos", "macOS"
    elif system == "Windows":
        os_id, os_label = "windows", "Windows"
    elif system == "Linux":
        os_id, os_label = "linux", "Linux"
    else:
        os_id, os_label = "unknown", system or "Unknown"

    arch = machine or "?"
    apple_silicon = system == "Darwin" and machine.lower() in ("arm64", "aarch64")

    gpu_kind = "none"
    gpu_name = ""
    vram_mb: int | None = None
    driver = ""
    accel = "cpu"

    if apple_silicon:
        gpu_kind = "apple"
        accel = "metal"
        chip = ""
        try:
            chip = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass
        gpu_name = chip or f"Apple Silicon ({machine})"
    else:
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).strip()
            if out:
                line = out.splitlines()[0]
                parts = [p.strip() for p in line.split(",")]
                gpu_kind = "nvidia"
                accel = "cuda"
                gpu_name = parts[0] if parts else "NVIDIA GPU"
                if len(parts) > 1:
                    try:
                        vram_mb = int(float(parts[1]))
                    except ValueError:
                        vram_mb = None
                if len(parts) > 2:
                    driver = parts[2]
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass

    if gpu_kind == "nvidia":
        demucs_label = "Cài Demucs CUDA"
        demucs_backend = "cuda"
        ocr_action = "ocr_cuda"
        ocr_label = "Cài OCR CUDA"
        install_summary = f"{os_label} + {gpu_name} -> OCR CUDA + Demucs CUDA"
        install_hint = "Máy có NVIDIA — nên cài GPU tăng tốc OCR và Demucs CUDA."
    elif gpu_kind == "apple":
        demucs_label = "Cài Demucs (Apple Metal)"
        demucs_backend = "mlx"
        ocr_action = ""
        ocr_label = ""
        install_summary = f"{os_label} Apple Silicon ({gpu_name}) -> Demucs-MLX (Metal)"
        install_hint = "Mac Apple Silicon — OCR chạy CPU/ANE; tách lời dùng demucs-mlx (Metal)."
    else:
        demucs_label = "Cài Demucs (CPU)"
        demucs_backend = "cpu"
        ocr_action = ""
        ocr_label = ""
        install_summary = f"{os_label} · CPU ({arch}) — không phát hiện GPU tăng tốc"
        install_hint = "Không thấy NVIDIA/Apple GPU — Demucs chạy CPU (chậm hơn)."

    # ── Kế hoạch cài TẤT CẢ mục Thiết lập theo OS/GPU ──
    if os_id == "macos":
        py_link = "https://www.python.org/downloads/macos/"
        ff_cmd = "brew install ffmpeg"
        ff_link = ""
        ff_label = "Cài ffmpeg (brew)"
        ollama_link = "https://ollama.com/download/mac"
        node_link = "https://nodejs.org/en/download"
        tts_id, tts_name, tts_hint = "say", "macOS say", "TTS hệ thống macOS (có sẵn)."
        tts_install, tts_label = "", ""
    elif os_id == "windows":
        py_link = "https://www.python.org/downloads/windows/"
        ff_cmd = ""
        ff_link = "https://www.gyan.dev/ffmpeg/builds/"
        ff_label = "Tải ffmpeg (Windows)"
        ollama_link = "https://ollama.com/download/windows"
        node_link = "https://nodejs.org/en/download"
        tts_id, tts_name = "espeak", "espeak-ng"
        tts_hint = "TTS hệ thống Windows/Linux (tuỳ chọn)."
        tts_install = "https://github.com/espeak-ng/espeak-ng/releases"
        tts_label = "Tải espeak-ng"
    else:  # linux / unknown
        py_link = "https://www.python.org/downloads/"
        ff_cmd = "sudo apt install ffmpeg"
        ff_link = ""
        ff_label = "Cài ffmpeg (apt)"
        ollama_link = "https://ollama.com/download/linux"
        node_link = "https://nodejs.org/en/download"
        tts_id, tts_name = "espeak", "espeak-ng"
        tts_hint = "TTS hệ thống Linux: sudo apt install espeak-ng"
        tts_install = "sudo apt install espeak-ng"
        tts_label = "Cài espeak-ng"

    pip = f'"{sys.executable}" -m pip install' if os_id == "windows" else f"{sys.executable} -m pip install"

    items_plan: dict[str, dict[str, Any]] = {
        "python": {
            "kind": "url",
            "value": py_link,
            "label": f"Tải Python ({os_label})",
            "hint": f"Cần Python ≥ 3.10 trên {os_label}.",
        },
        "ffmpeg": {
            "kind": "cmd" if ff_cmd else "url",
            "value": ff_cmd or ff_link,
            "label": ff_label,
            "hint": "Bắt buộc cắt audio / burn / mux.",
        },
        "ffprobe": {
            "kind": "cmd" if ff_cmd else "url",
            "value": ff_cmd or ff_link,
            "label": ff_label,
            "hint": "Thường đi kèm ffmpeg.",
        },
        "faster_whisper": {
            "kind": "cmd",
            "value": f"{pip} faster-whisper",
            "label": "Cài faster-whisper",
            "hint": "ASR giọng nói (Whisper).",
        },
        "rapidocr_onnxruntime": {
            "kind": "cmd",
            "value": f"{pip} rapidocr-onnxruntime",
            "label": "Cài RapidOCR",
            "hint": "OCR hardsub / nhãn trên khung.",
        },
        "httpx": {
            "kind": "cmd",
            "value": f"{pip} httpx",
            "label": "Cài httpx",
            "hint": "Gọi API dịch / TTS cloud.",
        },
        "PIL": {
            "kind": "cmd",
            "value": f"{pip} pillow",
            "label": "Cài Pillow",
            "hint": "Vẽ caption khi burn.",
        },
        "cv2": {
            "kind": "cmd",
            "value": f"{pip} opencv-python-headless",
            "label": "Cài OpenCV",
            "hint": "Xử lý khung OCR.",
        },
        "ocr_cuda": {
            "kind": "action",
            "value": ocr_action,
            "label": ocr_label or "OCR CUDA (không cần)",
            "hint": (
                "ONNX Runtime CUDA — chỉ NVIDIA."
                if gpu_kind == "nvidia"
                else "Máy này không dùng OCR CUDA."
            ),
            "relevant": gpu_kind == "nvidia",
        },
        "demucs": {
            "kind": "action",
            "value": "demucs_cuda",
            "label": demucs_label,
            "hint": install_hint,
            "relevant": True,
            "backend": demucs_backend,
        },
        tts_id: {
            "kind": "url" if (tts_install or "").startswith("http") else ("cmd" if tts_install else "none"),
            "value": tts_install,
            "label": tts_label or tts_name,
            "hint": tts_hint,
            "relevant": True,
            "name": tts_name,
        },
        "ollama": {
            "kind": "url",
            "value": ollama_link,
            "label": f"Tải Ollama ({os_label})",
            "hint": "Dịch local (tuỳ chọn).",
        },
        "node": {
            "kind": "url",
            "value": node_link,
            "label": f"Tải Node.js ({os_label})",
            "hint": "Chỉ cần khi dev UI (npm run dev).",
        },
        "data": {
            "kind": "none",
            "value": "",
            "label": "",
            "hint": "Thư mục lưu project / cache / xuất.",
        },
    }

    actions = []
    if ocr_action:
        actions.append({"id": ocr_action, "label": ocr_label})
    actions.append({"id": "demucs_cuda", "label": demucs_label})

    vram_txt = f"{vram_mb} MB" if vram_mb else ""
    label_bits = [os_label, arch]
    if gpu_name:
        label_bits.append(gpu_name)
    if vram_txt:
        label_bits.append(vram_txt)
    label = " · ".join(label_bits)

    return {
        "os": os_id,
        "osLabel": os_label,
        "arch": arch,
        "appleSilicon": apple_silicon,
        "gpuKind": gpu_kind,
        "gpuName": gpu_name,
        "vramMb": vram_mb,
        "driver": driver,
        "accel": accel,
        "label": label,
        "hasGpu": gpu_kind in ("nvidia", "apple"),
        "install": {
            "ocr": ocr_action,
            "ocrLabel": ocr_label,
            "demucs": "demucs_cuda",
            "demucsLabel": demucs_label,
            "demucsBackend": demucs_backend,
            "summary": install_summary,
            "hint": install_hint,
            "actions": actions,
            "items": items_plan,
        },
    }


def hardware() -> dict[str, str]:
    d = detect_device()
    return {
        "label": d["label"],
        "accel": str(d["accel"]),
        "os": str(d["os"]),
        "gpuKind": str(d["gpuKind"]),
        "gpuName": str(d.get("gpuName") or ""),
    }


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


def retime_video_segments(
    video: Path,
    segments: list[dict[str, Any]],
    cache_dir: Path,
    project_id: str | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    """Retime speech spans and return segments mapped onto output timeline."""
    duration = ffprobe_duration(video)
    ordered = sorted((dict(s) for s in segments), key=lambda s: float(s.get("start") or 0))
    if duration <= 0 or not any(abs(float(s.get("videoSpeed") or 1) - 1.0) > 0.001 for s in ordered):
        return video, ordered

    stat = video.stat()
    signature = [
        (s.get("id"), round(float(s.get("start") or 0), 4), round(float(s.get("end") or 0), 4),
         round(float(s.get("videoSpeed") or 1), 3))
        for s in ordered
    ]
    key = hashlib.sha1(
        json.dumps([str(video.resolve()), stat.st_size, stat.st_mtime_ns, signature]).encode()
    ).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"retimed_{key}.mp4"

    spans: list[tuple[float, float, float, float, float]] = []
    cursor = 0.0
    out_cursor = 0.0
    for segment in ordered:
        start = max(cursor, min(duration, float(segment.get("start") or 0)))
        end = max(start, min(duration, float(segment.get("end") or start)))
        if start > cursor + 0.001:
            gap = start - cursor
            spans.append((cursor, start, 1.0, out_cursor, out_cursor + gap))
            out_cursor += gap
        # <1 = chậm (kéo dài span, đẩy timeline sau); >1 = nhanh
        speed = max(0.4, min(2.0, float(segment.get("videoSpeed") or 1)))
        if end > start + 0.001:
            out_end = out_cursor + (end - start) / speed
            spans.append((start, end, speed, out_cursor, out_end))
            out_cursor = out_end
        cursor = max(cursor, end)
    if cursor < duration - 0.001:
        spans.append((cursor, duration, 1.0, out_cursor, out_cursor + duration - cursor))

    def map_time(value: float) -> float:
        for source_start, source_end, speed, output_start, output_end in spans:
            if value <= source_end + 1e-6:
                return output_start + max(0.0, min(source_end - source_start, value - source_start)) / speed
        return spans[-1][4] if spans else value

    remapped = []
    for segment in ordered:
        mapped = dict(segment)
        mapped["start"] = map_time(float(segment.get("start") or 0))
        mapped["end"] = map_time(float(segment.get("end") or 0))
        if segment.get("coverStart") is not None:
            try:
                mapped["coverStart"] = map_time(float(segment["coverStart"]))
            except (TypeError, ValueError):
                pass
        if segment.get("coverEnd") is not None:
            try:
                mapped["coverEnd"] = map_time(float(segment["coverEnd"]))
            except (TypeError, ValueError):
                pass
        # speed đã “nướng” vào timeline — đừng retime lần 2
        mapped.pop("videoSpeed", None)
        remapped.append(mapped)

    if not out.exists():
        # Gộp span liền kề cùng speed → ít node filter (tránh WinError 206)
        merged: list[tuple[float, float, float, float, float]] = []
        for sp in spans:
            if (
                merged
                and abs(merged[-1][2] - sp[2]) < 1e-6
                and abs(merged[-1][1] - sp[0]) < 1e-4
            ):
                prev = merged[-1]
                merged[-1] = (prev[0], sp[1], prev[2], prev[3], sp[4])
            else:
                merged.append(sp)
        has_audio = _has_audio_stream(video)
        filters: list[str] = []
        labels: list[str] = []
        for i, (start, end, speed, _out_start, _out_end) in enumerate(merged):
            sp = max(0.25, min(4.0, float(speed) or 1.0))
            filters.append(
                f"[0:v]trim=start={start:.6f}:end={end:.6f},"
                f"setpts=(PTS-STARTPTS)/{sp:.6f}[v{i}]"
            )
            if has_audio:
                a_chain = atempo_chain(sp)
                filters.append(
                    f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
                    f"{a_chain}[a{i}]"
                )
                labels.append(f"[v{i}][a{i}]")
            else:
                labels.append(f"[v{i}]")
        n = len(merged)
        if has_audio:
            filters.append("".join(labels) + f"concat=n={n}:v=1:a=1[vout][aout]")
        else:
            filters.append("".join(labels) + f"concat=n={n}:v=1:a=0[vout]")
        # Script file — không nhét filter_complex vào argv (Windows MAX_PATH / 206)
        fc_path = cache_dir / f"retimed_{key}_fc.txt"
        fc_path.write_text(";\n".join(filters) + "\n", encoding="utf-8")
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-filter_complex_script",
                str(fc_path),
                "-map",
                "[vout]",
            ]
            if has_audio:
                cmd += [
                    "-map",
                    "[aout]",
                    *h264_encoder_args(fast=True),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                ]
            else:
                cmd += [*h264_encoder_args(fast=True), "-an"]
            cmd += ["-map_metadata", "-1", "-map_chapters", "-1", str(out)]
            run_cmd(project_id, cmd)
        finally:
            try:
                fc_path.unlink(missing_ok=True)
            except OSError:
                pass
    return out, remapped

def ensure_preview_clip(
    source: Path, dest: Path, sec: float, project_id: str | None = None
) -> Path:
    """Cắt N giây đầu để thử nhanh; cache theo dest path.

    Ghi *.tmp.mp4 rồi rename — tránh Range vào file đang ghi (416).
    Không dùng .mp4.tmp: ffmpeg/nvenc không nhận extension → exit -22.
    """
    if dest.exists() and dest.stat().st_mtime >= source.stat().st_mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.stem}.tmp{dest.suffix}")  # preview_10.tmp.mp4
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass
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
                str(tmp),
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
                *h264_encoder_args(fast=True),
                "-c:a",
                "aac",
                str(tmp),
            ],
        )
    tmp.replace(dest)
    return dest


def ensure_playback_speed(
    source: Path,
    dest: Path,
    speed: float = 0.80,
    project_id: str | None = None,
    *,
    force: bool = False,
) -> Path:
    """Bake tốc độ phát vào file (preferVideo 0.80×) — chạy TRƯỚC ASR/OCR.

    speed < 1 = chậm hơn (dài hơn): setpts *= 1/speed, atempo = speed.

    Encode trung gian: ultrafast/p1 + CRF cao — chỉ phục vụ ASR/timeline,
    không phải file xuất cuối (xuất encode lại sau).
    """
    speed = max(0.5, min(2.0, float(speed)))
    if abs(speed - 1.0) < 0.001:
        return source
    if (
        not force
        and dest.exists()
        and dest.stat().st_mtime >= source.stat().st_mtime
        and dest.stat().st_size > 1024
    ):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.stem}.tmp{dest.suffix}")
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass
    pts = 1.0 / speed
    has_a = _has_audio_stream(source)
    # Bake pipeline: ưu tiên tốc độ, chất lượng vừa đủ cho Whisper/OCR
    if nvenc_available():
        vcodec = [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p1",
            "-tune",
            "ll",
            "-rc",
            "vbr",
            "-cq",
            "28",
            "-b:v",
            "0",
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        vcodec = [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "0",
        ]
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter:v",
        f"setpts={pts:.6f}*PTS",
        *vcodec,
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
    ]
    if has_a:
        # atempo chỉ 0.5–2.0 — speed 0.80 ok 1 bước; AAC 128k đủ cho ASR
        cmd += ["-filter:a", f"atempo={speed:.6f}", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    cmd.append(str(tmp))
    run_cmd(project_id, cmd)
    tmp.replace(dest)
    return dest


def clamp_playback_speed(speed: float) -> float:
    return max(0.5, min(2.0, float(speed)))


def meta_baked_speed(meta: dict) -> float:
    """Tốc độ đã bake vào workVideo.

    - bakedSpeed có key (kể cả 1.0 sau «Áp dụng 1×») → dùng giá trị đó
    - chỉ bakedPreferVideo (legacy) → 0.80
    - không key → 1.0 (timeline 1×; soft preferVideo chỉ ở FE playbackRate)
    """
    if meta.get("bakedSpeed") is not None:
        return clamp_playback_speed(float(meta["bakedSpeed"]))
    if meta.get("bakedPreferVideo"):
        return 0.80
    return 1.0


def meta_has_user_bake(meta: dict) -> bool:
    """User đã bấm Áp dụng tốc độ (kể cả 1×). Không lẫn soft preferVideo."""
    return meta.get("bakedSpeed") is not None


def speed_cache_tag(speed: float) -> str:
    return f"s{int(round(clamp_playback_speed(speed) * 100)):03d}"


def scale_time_fields(obj: dict, scale: float, keys: tuple[str, ...] = ("start", "end")) -> None:
    if abs(scale - 1.0) < 1e-9:
        return
    for k in keys:
        if obj.get(k) is None:
            continue
        try:
            obj[k] = float(obj[k]) * scale
        except (TypeError, ValueError):
            pass


_SEG_TIME_KEYS = ("start", "end", "coverStart", "coverEnd")


def _deepcopy_json(obj: Any) -> Any:
    import copy

    return copy.deepcopy(obj)


def _scale_segment_tree(seg: dict, scale: float) -> None:
    """Scale start/end/cover + compoundChildren (relative hoặc absolute)."""
    if not isinstance(seg, dict) or abs(scale - 1.0) < 1e-9:
        return
    scale_time_fields(seg, scale, _SEG_TIME_KEYS)
    children = seg.get("compoundChildren")
    if not isinstance(children, list):
        return
    for ch in children:
        if isinstance(ch, dict):
            scale_time_fields(ch, scale, _SEG_TIME_KEYS)


def _snapshot_timeline_1x(meta: dict, current_speed: float) -> dict[str, Any]:
    """Chụp timeline về mốc 1× (t_1x = t_display * current_speed). Tránh nhân chồng khi bake nhiều lần."""
    speed = clamp_playback_speed(current_speed)
    to_1x = speed  # display → 1×
    segs = _deepcopy_json(meta.get("segments") or [])
    ovs = _deepcopy_json(meta.get("overlays") or [])
    for seg in segs:
        if isinstance(seg, dict):
            _scale_segment_tree(seg, to_1x)
            # Giữ videoSpeed từng câu (TTS fit) — không ghi đè bake global
    for ov in ovs:
        if isinstance(ov, dict):
            scale_time_fields(ov, to_1x, ("start", "end"))
    # duration meta = nguồn 1×; workDuration = file bake (display)
    base_dur = float(meta.get("duration") or 0)
    work_dur = float(meta.get("workDuration") or 0)
    if base_dur <= 0 and work_dur > 0:
        # work đang ở display → quy về 1×
        base_dur = work_dur * speed
    if base_dur <= 0 and segs:
        try:
            # segs đã scale về 1× ở trên
            base_dur = max(float(s.get("end") or 0) for s in segs if isinstance(s, dict))
        except ValueError:
            base_dur = 0.0
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    # previewSec lưu 1× (cửa sổ dịch)
    preview_1x = float(preview_sec) * speed if preview_sec > 0 else 0.0
    return {
        "segments": segs,
        "overlays": ovs,
        "duration1x": base_dur,
        "previewSec1x": preview_1x,
        "previewSec": preview_sec,
    }


def ensure_timeline_baseline(meta: dict, current_speed: float) -> dict[str, Any]:
    """Baseline 1× chỉ tạo một lần (hoặc khi thiếu). Mọi bake sau tính từ đây."""
    bl = meta.get("timelineBaseline")
    if isinstance(bl, dict) and isinstance(bl.get("segments"), list):
        return bl
    bl = _snapshot_timeline_1x(meta, current_speed)
    meta["timelineBaseline"] = bl
    return bl


def apply_timeline_from_baseline(meta: dict, new_speed: float) -> None:
    """t_display = t_1x / new_speed — luôn từ baseline, không cascade."""
    import copy

    new_speed = clamp_playback_speed(new_speed)
    bl = ensure_timeline_baseline(meta, meta_baked_speed(meta))
    scale = 1.0 / new_speed
    segs = copy.deepcopy(bl.get("segments") or [])
    ovs = copy.deepcopy(bl.get("overlays") or [])
    for seg in segs:
        if isinstance(seg, dict):
            _scale_segment_tree(seg, scale)
    for ov in ovs:
        if isinstance(ov, dict):
            scale_time_fields(ov, scale, ("start", "end"))
    meta["segments"] = segs
    meta["overlays"] = ovs
    dur1 = float(bl.get("duration1x") or meta.get("duration") or 0)
    if dur1 > 0:
        # duration nguồn giữ 1×; workDuration = độ dài file bake
        meta["duration"] = dur1
        if abs(new_speed - 1.0) > 0.001:
            meta["workDuration"] = dur1 / new_speed
        else:
            meta.pop("workDuration", None)


def remap_timeline_for_speed_change(meta: dict, old_speed: float, new_speed: float) -> None:
    """Đổi bake speed: timeline/caption/TTS/overlay scale đồng bộ từ baseline 1×.

    Không nhân chồng (0.8→1→2 luôn đúng như apply trực tiếp từ gốc).
    Compound children, coverStart/End, overlays đều scale.
    Không ghi đè videoSpeed từng câu (TTS fit).
    """
    old_speed = clamp_playback_speed(old_speed)
    new_speed = clamp_playback_speed(new_speed)
    if abs(old_speed - new_speed) < 1e-9:
        return
    # Chụp baseline nếu chưa có (từ timeline đang ở old_speed)
    ensure_timeline_baseline(meta, old_speed)
    apply_timeline_from_baseline(meta, new_speed)

def preview_1x_path(project_id: str, meta: dict) -> Path:
    """File preview/source 1× (chưa bake tốc độ)."""
    from .project import ensure_layout

    preview_sec = max(0, int(meta.get("previewSec") or 0))
    cache = ensure_layout(project_id) / "cache"
    if preview_sec > 0:
        cached = cache / f"preview_{preview_sec}.mp4"
        if cached.is_file():
            return cached
    return Path(str(meta["videoPath"]))


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
            *h264_encoder_args(),
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


# Khớp LivePreviewEditor.ASPECT_PRESETS (w/h ratio)
_ASPECT_PRESETS: dict[str, tuple[float, float]] = {
    "16:9": (16, 9),
    "4:3": (4, 3),
    "2.35:1": (235, 100),
    "2:1": (2, 1),
    "1.85:1": (185, 100),
    "9:16": (9, 16),
    "3:4": (3, 4),
    "58inch": (108, 234),
    "1:1": (1, 1),
}


def resolve_export_crop(
    source_w: int,
    source_h: int,
    preset_id: str,
) -> tuple[int, int, int, int] | None:
    """Center-crop giống resolveCropRect — None = giữ nguyên khung."""
    if source_w <= 0 or source_h <= 0:
        return None
    key = (preset_id or "original").strip()
    if key in ("", "original", "custom"):
        return None
    dims = _ASPECT_PRESETS.get(key)
    if not dims:
        return None
    tw, th = dims
    target = tw / th
    source = source_w / source_h
    if source >= target:
        h = float(source_h)
        w = h * target
        x = (source_w - w) / 2.0
        y = 0.0
    else:
        w = float(source_w)
        h = w / target
        x = 0.0
        y = (source_h - h) / 2.0
    xi = max(0, int(round(x)))
    yi = max(0, int(round(y)))
    wi = int(round(w))
    hi = int(round(h))
    # H.264 cần chẵn
    wi -= wi % 2
    hi -= hi % 2
    xi -= xi % 2
    yi -= yi % 2
    xi = max(0, min(source_w - wi, xi))
    yi = max(0, min(source_h - hi, yi))
    if wi < 2 or hi < 2:
        return None
    if wi >= source_w - 1 and hi >= source_h - 1:
        return None
    return xi, yi, wi, hi


def crop_export_aspect(
    src: Path,
    dst: Path,
    preset_id: str,
    *,
    project_id: str | None = None,
) -> Path:
    """Cắt khung theo previewAspectRatio (sau burn, trước encode 1080)."""
    sw, sh = video_size(src)
    crop = resolve_export_crop(sw, sh, preset_id)
    if crop is None:
        if src.resolve() != dst.resolve():
            import shutil

            shutil.copy2(src, dst)
        return dst
    x, y, w, h = crop
    tmp = dst.with_suffix(".tmpcrop.mp4")
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
            f"crop={w}:{h}:{x}:{y}",
            *h264_encoder_args(),
            "-c:a",
            "copy",
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
