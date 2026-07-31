"""Render images and video clips to a prompt timeline with optional narration."""
from __future__ import annotations

import re
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pipeline.core.config import DATA
from pipeline.core.jobs import kill_process_tree
from pipeline.core.media import nvenc_available

ROOT = DATA / "srt_image"
ROOT.mkdir(parents=True, exist_ok=True)
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_PROCS: dict[str, subprocess.Popen] = {}
_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")
_SRT_RANGE = re.compile(
    r"(?m)^(\s*)(\d+:\d+:\d+[,.]\d+)(\s*-->\s*)(\d+:\d+:\d+[,.]\d+)(.*)$"
)
_TIMELINE_RANGE = re.compile(r"\[\s*([0-9:.,]+)\s*[-–—]\s*([0-9:.,]+)\s*\]")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _seconds(value: str) -> float:
    match = _TIME.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Mốc SRT không hợp lệ: {value}")
    h, m, s, ms = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_srt_times(path: Path) -> list[tuple[float, float]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    times: list[tuple[float, float]] = []
    for line in text.splitlines():
        if "-->" not in line:
            continue
        left, right = line.split("-->", 1)
        start, end = _seconds(left), _seconds(right.split()[0])
        if end > start:
            times.append((start, end))
    if not times:
        raise ValueError("File SRT không có mốc thời gian hợp lệ")
    return times


def _format_srt_time(seconds: float) -> str:
    milliseconds = round(max(0, seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def shift_srt(path: Path, output: Path, offset: float) -> Path:
    text = path.read_text(encoding="utf-8-sig", errors="replace")

    def replace(match: re.Match[str]) -> str:
        start = _format_srt_time(_seconds(match.group(2)) + offset)
        end = _format_srt_time(_seconds(match.group(4)) + offset)
        return f"{match.group(1)}{start}{match.group(3)}{end}{match.group(5)}"

    shifted, count = _SRT_RANGE.subn(replace, text)
    if not count:
        raise ValueError("File SRT không có mốc thời gian hợp lệ")
    blocks = re.split(r"(\r?\n[ \t]*\r?\n)", shifted)
    for index in range(0, len(blocks), 2):
        lines = blocks[index].splitlines()
        timeline_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if timeline_index >= 0 and timeline_index + 1 < len(lines):
            # ponytail: join bằng khoảng trắng để tận dụng hết chiều ngang màn hình.
            # Nếu để \N sẽ bị xuống dòng quá sớm theo file SRT gốc.
            text_line = " ".join(line.strip() for line in lines[timeline_index + 1:] if line.strip())
            blocks[index] = "\n".join([*lines[:timeline_index + 1], text_line])
    output.write_text("".join(blocks), encoding="utf-8")
    return output


def _timeline_seconds(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    dot_parts = normalized.split(".")
    if ":" not in normalized and len(dot_parts) == 4:
        hours, minutes, seconds, centiseconds = dot_parts
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(centiseconds) / 100
    parts = normalized.split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    if len(parts) != 3:
        raise ValueError(f"Mốc timeline không hợp lệ: {value}")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_timeline_times(path: Path) -> list[tuple[float, float]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    times: list[tuple[float, float]] = []
    for left, right in _TIMELINE_RANGE.findall(text):
        start, end = _timeline_seconds(left), _timeline_seconds(right)
        if end > start:
            times.append((start, end))
    if not times:
        raise ValueError("File tạo ảnh không có timeline hợp lệ")
    return times


def image_resolution(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    width, height = int(stream["width"]), int(stream["height"])
    return width - width % 2, height - height % 2


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def _encoder_args(use_gpu: bool, crf: int) -> list[str]:
    if use_gpu:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", str(crf), "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)]


def _run_stage(job_id: str, cmd: list[str]) -> None:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0,
    )
    with _LOCK:
        _PROCS[job_id] = proc
    _, stderr = proc.communicate()
    with _LOCK:
        _PROCS.pop(job_id, None)
    if (get_job(job_id) or {}).get("status") == "cancelled":
        raise InterruptedError
    if proc.returncode:
        detail = "\n".join(stderr.splitlines()[-8:])
        _log(job_id, f"FFmpeg lỗi:\n{detail}")
        raise RuntimeError(f"FFmpeg kết thúc với mã {proc.returncode}: {detail}")


def _has_logo_region(source: Path, x: int, y: int, w: int, h: int) -> bool:
    """Kiểm tra vùng (x,y,w,h) có đủ phức tạp (variance) để là logo không.
    ponytail: heuristic đơn giản — vùng trơn = không logo → skip delogo."""
    try:
        from PIL import Image
        import numpy as np
        img_exts = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp"}
        if source.suffix.lower() not in img_exts:
            return True  # video → assume có logo, safe hơn
        with Image.open(source) as im:
            iw, ih = im.size
            cx, cy = min(x, iw - 1), min(y, ih - 1)
            cw, ch = min(w, iw - cx), min(h, ih - cy)
            if cw < 4 or ch < 4:
                return False
            crop = im.crop((cx, cy, cx + cw, cy + ch)).convert("L")
            arr = np.array(crop, dtype=float)
            return float(arr.std()) > 8  # std > 8 → có nội dung (logo/text)
    except Exception:
        return True  # lỗi → safe: áp delogo


def _prepare_video_segments(
    job_id: str, media: list[Path], durations: list[float], work: Path,
    width: int, height: int, fps: int, crf: int, use_gpu: bool, zoom: str = "off",
    delogo_prefix: str = "",
) -> list[Path]:
    segments: list[Path] = []
    # Parse delogo params 1 lần để check per-file
    dl_params: tuple[int, int, int, int] | None = None
    if delogo_prefix:
        import re as _re
        m = _re.search(r"x=(\d+):y=(\d+):w=(\d+):h=(\d+)", delogo_prefix)
        if m:
            dl_params = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    base_vf_no_dl = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
    )
    base_vf_dl = f"{delogo_prefix}{base_vf_no_dl}"
    last_variant = -1
    for index, (source, duration) in enumerate(zip(media, durations)):
        variant = random.randrange(16)
        if variant == last_variant:
            variant = (variant + random.randrange(1, 16)) % 16
        last_variant = variant
        motion = _motion_filter(zoom, width, height, fps, variant, duration)
        # ponytail: chỉ delogo file thật sự có logo trong vùng đó
        use_dl = delogo_prefix and (not dl_params or _has_logo_region(source, *dl_params))
        chosen_vf = base_vf_dl if use_dl else base_vf_no_dl
        vf = f"{chosen_vf}{',' + motion if motion else ''},format=yuv420p"
        if use_dl:
            _log(job_id, f"Clip {index + 1}: delogo ✓ · {source.name}")
        _log(job_id, f"Chuẩn bị clip {index + 1}/{len(durations)}: {source.name} ({duration:.2f}s)")
        output = work / f"segment_{index:05d}.mp4"
        cmd = ["ffmpeg", "-y"]
        cmd += ["-stream_loop", "-1"] if is_video(source) else ["-loop", "1"]
        cmd += ["-i", str(source), "-t", f"{duration:.3f}", "-vf", vf, "-an"]
        cmd += _encoder_args(use_gpu, crf)
        cmd += ["-movflags", "+faststart", str(output)]
        _run_stage(job_id, cmd)
        segments.append(output)
        _update(job_id, status="processing", progress=round((index + 1) / len(durations) * 35, 1))
    return segments


def create_job(
    name: str, work: Path, images: list[Path], audio: Path | None,
    timeline: Path, srt: Path | None, options: dict, watermark: Path | None = None,
    output_target: Path | None = None,
) -> dict:
    job_id = uuid.uuid4().hex[:10]
    output = output_target or ROOT / f"{Path(name).stem or 'ghep-anh-srt'}_{job_id}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id, "name": name, "status": "queued", "progress": 0,
        "error": "", "outputSize": 0, "output": str(output), "work": str(work),
        "logs": [f"[{time.strftime('%H:%M:%S')}] Đã tạo job: {name}"],
        "images": [str(p) for p in images], "audio": str(audio) if audio else "",
        "timeline": str(timeline), "srt": str(srt) if srt else "", "watermark": str(watermark) if watermark else "",
        "options": options,
    }
    with _LOCK:
        _JOBS[job_id] = job
    return dict(job)


def list_jobs() -> list[dict]:
    with _LOCK:
        return [dict(job) for job in _JOBS.values()]


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _update(job_id: str, **values: Any) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(values)


def _log(job_id: str, message: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")
        del logs[:-200]


def output_path(job_id: str) -> Path | None:
    job = get_job(job_id)
    path = Path(job["output"]) if job else None
    return path if job and job["status"] == "done" and path and path.is_file() else None


def cancel(job_id: str) -> bool:
    with _LOCK:
        if job_id not in _JOBS:
            return False
        _JOBS[job_id]["status"] = "cancelled"
        proc = _PROCS.get(job_id)
    if proc:
        kill_process_tree(proc.pid)
    return True


def pause(job_id: str, paused: bool) -> bool:
    with _LOCK:
        proc = _PROCS.get(job_id)
        job = _JOBS.get(job_id)
    if not proc or not job or proc.poll() is not None:
        return False
    if sys.platform == "win32":
        import ctypes
        access = 0x0800
        handle = ctypes.windll.kernel32.OpenProcess(access, False, proc.pid)
        if not handle:
            return False
        try:
            fn = ctypes.windll.ntdll.NtSuspendProcess if paused else ctypes.windll.ntdll.NtResumeProcess
            if fn(handle) != 0:
                return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        os.kill(proc.pid, signal.SIGSTOP if paused else signal.SIGCONT)
    _update(job_id, status="paused" if paused else "processing")
    return True


def _srt_ts_to_ass(ts: str) -> str:
    """'01:23:45,678' hoặc '01:23:45.678' → '1:23:45.67' (ASS centiseconds)"""
    ts = ts.replace(".", ",")
    h, m, s_ms = ts.split(":")
    s, ms = s_ms.split(",")
    return f"{int(h)}:{m}:{s}.{ms[:2]}"


def _ffmpeg_subtitle(path: Path, font_name: str = "system", font_size: int = 8, margin_bottom: int = 18,
                     bg_style: str = "solid", text_color: str = "#ffffff", bg_color: str = "#000000",
                     opacity: int = 55) -> str:
    """Generate ASS từ SRT với inline \\blur tags — Gaussian blur = CSS textShadow.
    force_style trên SRT không hỗ trợ inline tags; cần ASS file thực.
    Font: fontsdir (đã xác nhận hoạt động với DirectWrite).
    """
    # ponytail: Bold flag phải khớp nameID=2 (subfamily) trong TTF.
    # libass dùng flag này để tìm variant — sai flag → pick sai file hoặc fallback.
    # sub=Bold → Bold=1; sub=Regular → Bold=0
    _FONT_INFO: dict[str, tuple[str, str, int]] = {
        "system":   ("NotoSans-Bold.ttf",        "Noto Sans",         1),  # sub=Bold
        "segoe":    ("Inter-Bold.ttf",           "Inter",             0),  # sub=Regular
        "arial":    ("Arimo-Bold.ttf",           "Arimo",             0),  # sub=Regular
        "bold":     ("ArchivoBlack-Regular.ttf", "Archivo SemiBold",  0),  # sub=Regular
        "helvetica":("Roboto-Bold.ttf",          "Roboto",            0),  # sub=Regular
        "verdana":  ("OpenSans-Bold.ttf",        "Open Sans",         0),  # sub=Regular
        "tahoma":   ("Carlito-Bold.ttf",         "Carlito",           1),  # sub=Bold
        "trebuchet":("FiraSans-Bold.ttf",        "Fira Sans",         1),  # sub=Bold
        "rounded":  ("Nunito-Bold.ttf",          "Nunito ExtraLight", 0),  # sub=Regular
        "impact":   ("Anton-Regular.ttf",        "Anton",             0),  # sub=Regular
        "georgia":  ("Merriweather-Bold.ttf",    "Merriweather Light",0),  # sub=Regular
        "times":    ("Tinos-Bold.ttf",           "Tinos",             1),  # sub=Bold
        "palatino": ("Literata-Bold.ttf",        "Literata",          0),  # sub=Regular
        "garamond": ("EBGaramond-Bold.ttf",      "EB Garamond",       0),  # sub=Regular
        "courier":  ("CourierPrime-Bold.ttf",    "Cousine",           1),  # sub=Bold
        "mono":     ("NotoSansMono-Bold.ttf",    "Noto Sans Mono",    0),  # sub=Regular
        "comic":    ("ComicNeue-Bold.ttf",       "Patrick Hand",      0),  # sub=Regular
        "cjk":      ("NotoSansSC-Bold.ttf",      "Noto Sans SC Thin", 0),  # sub=Regular
        "meiryo":   ("NotoSansJP-Bold.ttf",      "Noto Sans JP Thin", 0),  # sub=Regular
        "malgun":   ("NotoSansKR-Bold.ttf",      "Noto Sans KR Thin", 0),  # sub=Regular
    }
    _ttf, real_font, bold_flag = _FONT_INFO.get(font_name.lower(), _FONT_INFO["system"])
    fonts_dir = (Path(__file__).parent.parent.parent / "frontend" / "public" / "fonts").resolve()

    # ── Màu chữ (ASS BBGGRR little-endian) ───────────────────────────────────
    tc = text_color.lstrip("#")
    if len(tc) != 6: tc = "ffffff"
    tr, tg, tb = int(tc[0:2], 16), int(tc[2:4], 16), int(tc[4:6], 16)
    ass_primary = f"&H00{tb:02X}{tg:02X}{tr:02X}"

    # ── ASS Style + inline override tags theo bg_style ────────────────────────
    # CSS textShadow '0 2px 4px rgba(0,0,0,0.9)' = subtle dark glow phía sau chữ
    # ASS tương đương: Shadow=1 với ShadowColour tối — chữ vẫn sắc nét như CSS
    # KHÔNG dùng \blur — nó blur toàn bộ glyph (cả fill), làm chữ mờ hơn preview
    if bg_style in ("solid", "box"):
        bc = bg_color.lstrip("#")
        if len(bc) != 6: bc = "000000"
        br, bg_r, bb = int(bc[0:2], 16), int(bc[2:4], 16), int(bc[4:6], 16)
        a = 255 - max(0, min(255, int(255 * opacity / 100)))
        ass_bg = f"&H{a:02X}{bb:02X}{bg_r:02X}{br:02X}"
        pad = 2 if bg_style == "box" else 1.2
        # BorderStyle=3: nền box; Shadow=1 + dark colour = subtle drop shadow
        style_line = (
            f"Style: Default,{real_font},{font_size},"
            f"{ass_primary},&H00FFFFFF,{ass_bg},{ass_bg},"
            f"{bold_flag},0,0,0,100,100,0,0,3,{pad},1,2,20,20,{margin_bottom},0"
        )
    else:
        # none: outline mỏng tối + shadow nhẹ = CSS textShadow
        style_line = (
            f"Style: Default,{real_font},{font_size},"
            f"{ass_primary},&H00FFFFFF,&H1A000000,&H1A000000,"
            f"{bold_flag},0,0,0,100,100,0,0,1,1,1,2,20,20,{margin_bottom},0"
        )
    # Không inline override — style definition đã đủ
    inline_tag = ""

    # ── ASS header ────────────────────────────────────────────────────────────
    header = (
        "[Script Info]\r\n"
        "ScriptType: v4.00+\r\n"
        "PlayResX: 384\r\n"
        "PlayResY: 288\r\n"
        "WrapStyle: 0\r\n\r\n"
        "[V4+ Styles]\r\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\r\n"
        f"{style_line}\r\n\r\n"
        "[Events]\r\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
    )

    # ── Parse SRT → ASS Dialogue events ───────────────────────────────────────
    srt_text = path.read_text(encoding="utf-8-sig", errors="replace")
    events: list[str] = []
    for block in re.split(r"\n\s*\n", srt_text.strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            m = re.match(
                r"(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})",
                line.strip(),
            )
            if m:
                start = _srt_ts_to_ass(m.group(1))
                end   = _srt_ts_to_ass(m.group(2))
                text  = r"\N".join(l.strip() for l in lines[i + 1:] if l.strip())
                text  = re.sub(r"<[^>]+>", "", text)  # strip HTML tags
                events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{inline_tag}{text}")
                break

    ass_path = path.with_suffix(".sub.ass")
    ass_path.write_bytes((header + "\r\n".join(events) + "\r\n").encode("utf-8-sig"))

    # ── FFmpeg filter ─────────────────────────────────────────────────────────
    ass_ff   = ass_path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
    fonts_ff = str(fonts_dir).replace("\\", "/").replace(":", r"\:")
    return f"subtitles=filename='{ass_ff}':fontsdir='{fonts_ff}'"







def _logo_position(x_percent: float = 88, y_percent: float = 88, moving: bool = False) -> tuple[str, str]:
    if moving:
        return "(W-w)*(0.5+0.45*sin(t*0.37))", "(H-h)*(0.5+0.45*cos(t*0.29))"
    x = max(0, min(100, float(x_percent))) / 100
    y = max(0, min(100, float(y_percent))) / 100
    return f"min(W-w,max(0,W*{x:.4f}))", f"min(H-h,max(0,H*{y:.4f}))"


def _text_logo_position(x_percent: float = 88, y_percent: float = 88,
                        moving: bool = False, cycle: float = 6,
                        safe_margin: float = 4) -> tuple[str, str]:
    if moving:
        margin = max(0, min(20, float(safe_margin))) / 100
        step = f"floor(t/{max(0.5, cycle):.3f})"
        x = f"W*{margin:.4f}+max(0,W-tw-W*{margin * 2:.4f})*mod(abs(sin({step}*12.9898+1.37)*43758.5453),1)"
        # Phụ đề nằm dưới: logo ngẫu nhiên chỉ dùng 75% chiều cao phía trên.
        y = f"H*{margin:.4f}+max(0,H*0.75-th-H*{margin * 2:.4f})*mod(abs(sin({step}*78.233+2.71)*43758.5453),1)"
        return x, y
    x = max(0, min(100, float(x_percent))) / 100
    y = max(0, min(100, float(y_percent))) / 100
    return f"min(W-tw,max(0,W*{x:.4f}))", f"min(H-th,max(0,H*{y:.4f}))"


def _text_logo_filter(logo: dict, height: int) -> str:
    raw = str(logo.get("text") if logo.get("source") == "text" else logo.get("icon", "★"))
    text = raw.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%")
    opacity = max(5, min(100, float(logo.get("opacity", 85)))) / 100
    font_size = max(6, min(160, int(logo.get("fontSize") or 10)))
    color = str(logo.get("color") or "#ffffff")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#ffffff"
    visible = max(0.5, float(logo.get("visibleSec") or 4))
    hidden = max(0, float(logo.get("hiddenSec") or 2))
    fade = min(max(0, float(logo.get("fadeSec") or 0.5)), visible / 2)
    cycle = visible + hidden
    moving = logo.get("motion") == "random"
    x, y = _text_logo_position(
        logo.get("x", 88), logo.get("y", 88), moving, cycle, logo.get("safeMargin", 4),
    )
    enable = ""
    if logo.get("scope") == "range":
        start = max(0, float(logo.get("start", 0)))
        end = max(start, float(logo.get("end", start)))
        enable = f":enable='between(t,{start:.3f},{end:.3f})'"
    font_option = ""
    if sys.platform == "win32":
        font = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf"
        if not font.is_file():
            font = font.with_name("arial.ttf")
        if font.is_file():
            escaped_font = font.as_posix().replace(":", r"\:").replace("'", r"\'")
            font_option = f"fontfile='{escaped_font}':"
    alpha = f"{opacity:.3f}"
    if moving:
        local = f"mod(t,{cycle:.3f})"
        if fade > 0:
            alpha = (
                f"if(lt({local},{visible:.3f}),{opacity:.3f}*"
                f"min(1,min({local}/{fade:.3f},({visible:.3f}-{local})/{fade:.3f})),0)"
            )
        else:
            alpha = f"if(lt({local},{visible:.3f}),{opacity:.3f},0)"
    return (
        f"drawtext={font_option}text='{text}':fontsize={font_size}:"
        f"fontcolor={color}:shadowcolor=black@0.85:shadowx=2:shadowy=2:"
        f"alpha='{alpha}':x='{x}':y='{y}'{enable}"
    )


def _motion_filter(
    mode: str, width: int, height: int, fps: int = 30,
    variant: int = 0, duration: float = 8,
) -> str:
    """Ken Burns motion with frame-evaluated expressions for smooth movement."""
    mode = str(mode or "off")
    if mode == "off":
        return ""
    size = f"{width}x{height}"
    if mode == "zoomIn":
        return (
            "scale=iw*4:ih*4,"
            "zoompan=z='min(1.06,1+on*0.00020)':"
            "x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d=1:s={size}:fps={fps}"
        )
    if mode == "zoomOut":
        return (
            "scale=iw*4:ih*4,"
            "zoompan=z='max(1,1.06-on*0.00020)':"
            "x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d=1:s={size}:fps={fps}"
        )
    axis = {
        "left": ("(iw-iw/zoom)*min(1,on/240)", "(ih-ih/zoom)/2"),
        "right": ("(iw-iw/zoom)*(1-min(1,on/240))", "(ih-ih/zoom)/2"),
        "up": ("(iw-iw/zoom)/2", "(ih-ih/zoom)*(1-min(1,on/240))"),
        "down": ("(iw-iw/zoom)/2", "(ih-ih/zoom)*min(1,on/240)"),
    }.get(mode)
    if axis:
        return (
            "scale=iw*4:ih*4,"
            f"zoompan=z='min(1.06,1+on*0.00020)':x='{axis[0]}':y='{axis[1]}':"
            f"d=1:s={size}:fps={fps}"
        )
    # random: one continuous Ken Burns camera move per scene.
    frames = max(2, round(duration * fps) - 1)
    progress = f"min(1,on/{frames})"
    travel = f"(0.5-0.5*cos(PI*{progress}))"
    directions = (
        (f"(iw-iw/zoom)*{travel}", "(ih-ih/zoom)/2"),
        (f"(iw-iw/zoom)*(1-{travel})", "(ih-ih/zoom)/2"),
        ("(iw-iw/zoom)/2", f"(ih-ih/zoom)*{travel}"),
        ("(iw-iw/zoom)/2", f"(ih-ih/zoom)*(1-{travel})"),
        (f"(iw-iw/zoom)*{travel}", f"(ih-ih/zoom)*{travel}"),
        (f"(iw-iw/zoom)*(1-{travel})", f"(ih-ih/zoom)*{travel}"),
        (f"(iw-iw/zoom)*{travel}", f"(ih-ih/zoom)*(1-{travel})"),
        (f"(iw-iw/zoom)*(1-{travel})", f"(ih-ih/zoom)*(1-{travel})"),
    )
    x, y = directions[variant % len(directions)]
    zoom_curve = (
        f"1.12-0.12*{travel}"
        if variant % 2 == 0
        else f"1+0.09*{travel}"
    )
    return (
        "scale=iw*4:ih*4,"
        f"zoompan=z='{zoom_curve}':"
        f"x='{x}':y='{y}':"
        f"d=1:s={size}:fps={fps}"
    )


def run(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    try:
        _log(job_id, "Đang đọc timeline và kiểm tra media…")
        media = [Path(p) for p in job["images"]]
        cues = parse_timeline_times(Path(job["timeline"]))
        if len(media) < len(cues):
            raise ValueError(f"Thiếu ảnh/video: cần ít nhất {len(cues)} file, hiện có {len(media)}")
        durations = [
            max(0.04, (cues[i + 1][0] if i + 1 < len(cues) else end) - start)
            for i, (start, end) in enumerate(cues)
        ]
        opts = job["options"]
        resolution = str(opts.get("resolution", "auto"))
        width, height = image_resolution(media[0]) if resolution == "auto" else (
            int(value) for value in resolution.split("x", 1)
        )
        fps = max(1, min(60, int(opts.get("fps", 30))))
        crf = max(14, min(32, int(opts.get("crf", 20))))
        use_gpu = opts.get("encoder", "auto") != "cpu" and nvenc_available()
        _log(
            job_id,
            f"Đầu vào: {len(media)} media · {len(cues)} cảnh · {width}x{height} · {fps} FPS",
        )
        _log(job_id, f"Encoder: {'GPU NVIDIA NVENC' if use_gpu else 'CPU libx264'} · CRF {crf}")
        work = Path(job["work"])
        zoom_mode = str(opts.get("zoom", "off"))
        # ponytail: delogo — xóa watermark AI trước scale/zoom, tính trên frame gốc
        delogo_prefix = ""
        dl = opts.get("delogo") if isinstance(opts.get("delogo"), dict) else {}
        if dl.get("enabled"):
            src_w, src_h = image_resolution(media[0])
            dx = max(0, round(float(dl.get("x", 82)) / 100 * src_w))
            dy = max(0, round(float(dl.get("y", 94)) / 100 * src_h))
            dw = max(10, round(float(dl.get("w", 16)) / 100 * src_w))
            dh = max(10, round(float(dl.get("h", 4)) / 100 * src_h))
            delogo_prefix = f"delogo=x={dx}:y={dy}:w={dw}:h={dh},"
            _log(job_id, f"Delogo: {dw}×{dh} tại ({dx},{dy}) trên {src_w}×{src_h}")
        # ponytail: delogo bật → force segments để check logo per-file
        need_segments = (
            zoom_mode != "off"
            or bool(delogo_prefix)
            or any(is_video(path) for path in media[:len(durations)])
        )
        sources = (
            _prepare_video_segments(
                job_id, media, durations, work, width, height, fps, crf, use_gpu, zoom_mode,
                delogo_prefix,
            )
            if need_segments else media
        )
        concat = work / "media.ffconcat"
        lines = ["ffconcat version 1.0"]
        for source, duration in zip(sources, durations):
            escaped = source.resolve().as_posix().replace("'", r"'\''")
            lines.append(f"file '{escaped}'")
            if sources is media:
                lines.append(f"duration {duration:.3f}")
        if sources is media:
            lines.append(f"file '{media[len(durations) - 1].resolve().as_posix()}'")
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

        speed = max(25, min(400, float(opts.get("speed", 100)))) / 100
        volume = max(0, min(300, float(opts.get("volume", 100)))) / 100
        preview = max(0, min(120, float(opts.get("previewSeconds", 0))))
        subtitle_font = str(opts.get("subtitleFontFamily", "system"))
        subtitle_size = max(6, min(120, int(opts.get("subtitleSize", 8))))
        subtitle_margin = max(0, min(1000, int(opts.get("subtitleMargin", 18))))
        subtitle_offset = max(-3600, min(3600, float(opts.get("subtitleOffset", 0))))
        _raw_bg = opts.get("subtitleBackground", "solid")
        # ponytail: backward compat — old payloads sent 0/1; new sends none/solid/box
        if _raw_bg in (0, "0", False):
            subtitle_background = "none"
        elif _raw_bg in (1, "1", True):
            subtitle_background = "solid"
        else:
            subtitle_background = str(opts.get("subtitleBackground", "solid"))
        subtitle_color = str(opts.get("subtitleColor", "#ffffff"))
        subtitle_bg_color = str(opts.get("subtitleBgColor", "#000000"))
        subtitle_opacity = max(0, min(100, int(opts.get("subtitleOpacity", 55))))
        zoom_filter = ""
        if str(opts.get("zoom", "off")) != "off":
            _log(job_id, f"Zoom: {opts.get('zoom')} · chuyển động nội suy theo thời gian")
        # ponytail: khi có segments (zoom on), delogo đã chạy trong segment → không cần lại
        seg_delogo = delogo_prefix if sources is media else ""
        base_vf = (
            f"{seg_delogo}"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
            + (f",{zoom_filter}" if zoom_filter else "")
        )
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
        audio_index = None
        if job["audio"]:
            audio_index = 1
            cmd += ["-i", job["audio"]]
        logo = opts.get("logo") if isinstance(opts.get("logo"), dict) else {}
        watermark_index = None
        if logo.get("enabled") and logo.get("source") == "image" and job.get("watermark"):
            watermark_index = 2 if audio_index is not None else 1
            cmd += ["-loop", "1", "-i", job["watermark"]]
        speed_filter = f",setpts=PTS/{speed:.6f}" if abs(speed - 1) > 0.001 else ""
        subtitle_filter = ""
        if job["srt"]:
            shifted_srt = shift_srt(
                Path(job["srt"]), work / "subtitles-prepared.srt", subtitle_offset,
            )
            subtitle_filter = "," + _ffmpeg_subtitle(
                shifted_srt, subtitle_font, subtitle_size, subtitle_margin, subtitle_background,
                subtitle_color, subtitle_bg_color, subtitle_opacity,
            )
            _log(
                job_id,
                f"Phụ đề: cỡ {subtitle_size} · lề dưới {subtitle_margin} · "
                f"lệch {subtitle_offset:g}s · nền {subtitle_background}",
            )
        if watermark_index is not None:
            opacity = max(5, min(100, float(logo.get("opacity", 85)))) / 100
            scale = max(2, min(30, float(logo.get("size", 8)))) / 100
            x, y = _logo_position(logo.get("x", 88), logo.get("y", 88), logo.get("motion") == "random")
            enable = ""
            if logo.get("scope") == "range":
                start = max(0, float(logo.get("start", 0)))
                end = max(start, float(logo.get("end", start)))
                enable = f":enable='between(t,{start:.3f},{end:.3f})'"
            graph = (
                f"[0:v]{base_vf}{speed_filter}[base];"
                f"[{watermark_index}:v]scale=-1:{max(12, round(height * scale))},format=rgba,"
                f"colorchannelmixer=aa={opacity:.3f}[wm];"
                f"[base][wm]overlay=x='{x}':y='{y}':shortest=1{enable}"
                f"{subtitle_filter},format=yuv420p[vout]"
            )
            cmd += ["-filter_complex", graph, "-map", "[vout]"]
        else:
            logo_filter = (
                f",{_text_logo_filter(logo, height)}"
                if logo.get("enabled") and logo.get("source") in {"text", "icon"} else ""
            )
            cmd += ["-vf", f"{base_vf}{subtitle_filter}{logo_filter}{speed_filter},format=yuv420p"]
        cmd += _encoder_args(use_gpu, crf)
        if audio_index is not None:
            if watermark_index is None:
                cmd += ["-map", "0:v:0"]
            cmd += ["-map", f"{audio_index}:a:0", "-af", f"volume={volume:.3f},atempo={speed:.6f}",
                    "-c:a", "aac", "-b:a", "192k", "-shortest"]
        if preview:
            cmd += ["-t", str(preview / speed)]
        if opts.get("removeMetadata"):
            cmd += ["-map_metadata", "-1", "-metadata", "encoder="]
        cmd += ["-movflags", "+faststart", job["output"]]
        _update(job_id, status="processing", progress=1)
        _log(job_id, f"Bắt đầu render: {Path(job['output']).name}")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0,
        )
        with _LOCK:
            _PROCS[job_id] = proc
        total = max(end for _, end in cues)
        assert proc.stderr
        stderr_tail: list[str] = []
        last_logged_percent = -10
        for line in proc.stderr:
            clean = line.strip()
            if clean:
                stderr_tail.append(clean)
                del stderr_tail[:-30]
            match = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
            if match:
                current = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
                base = 35 if sources is not media else 1
                progress = min(99, round(base + current / total * (99 - base), 1))
                _update(job_id, progress=progress)
                bucket = int(progress // 10) * 10
                if bucket > last_logged_percent:
                    last_logged_percent = bucket
                    _log(job_id, f"Đang render: {progress:.1f}% · {current:.1f}/{total:.1f}s")
        code = proc.wait()
        with _LOCK:
            _PROCS.pop(job_id, None)
        if get_job(job_id).get("status") == "cancelled":
            return
        if code or not Path(job["output"]).is_file():
            detail = "\n".join(stderr_tail[-12:])
            raise RuntimeError(f"FFmpeg kết thúc với mã {code}\n{detail}")
        path = Path(job["output"])
        _update(job_id, status="done", progress=100, outputSize=path.stat().st_size)
        _log(job_id, f"Hoàn thành: {path} ({path.stat().st_size / 1_048_576:.1f} MB)")
    except Exception as exc:
        with _LOCK:
            _PROCS.pop(job_id, None)
        if (get_job(job_id) or {}).get("status") != "cancelled":
            _update(job_id, status="error", error=str(exc))
            _log(job_id, f"LỖI: {exc}")


def start(job_id: str) -> None:
    threading.Thread(target=run, args=(job_id,), name=f"srt-image-{job_id}", daemon=True).start()
