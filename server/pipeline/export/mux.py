"""Mix TTS onto video with BGM ducking."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core.jobs import run_cmd
from ..core.media import _has_audio_stream, ffprobe_duration, h264_encoder_args
from ..core.project import ensure_layout, out_final, set_status


def _wav_rms(path: Path) -> float:
    """RMS thô pcm_s16le (0..1) — chẩn đoán stem Demucs gần im."""
    import struct

    try:
        raw = subprocess.check_output(
            [
                "ffmpeg", "-v", "error", "-i", str(path),
                "-ac", "1", "-ar", "8000", "-f", "s16le", "-t", "120", "-",
            ],
            timeout=60,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return 0.0
    if len(raw) < 4:
        return 0.0
    n = len(raw) // 2
    # lấy mẫu thưa để nhanh
    step = max(1, n // 40000)
    acc = 0.0
    count = 0
    for i in range(0, n, step):
        (sample,) = struct.unpack_from("<h", raw, i * 2)
        acc += float(sample) * float(sample)
        count += 1
    if count <= 0:
        return 0.0
    return (acc / count) ** 0.5 / 32768.0


def _demucs_python(project_id: str | None = None, *, report: bool = True) -> str:
    """Python có demucs: ưu tiên server/.venv-demucs, cài lần đầu nếu thiếu."""
    server_root = Path(__file__).resolve().parents[2]
    venv = server_root / ".venv-demucs"
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def _has_demucs(exe: Path) -> bool:
        if not exe.is_file():
            return False
        try:
            r = subprocess.run(
                [str(exe), "-c", "import demucs, soundfile"],
                capture_output=True,
                timeout=60,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    if _has_demucs(py):
        return str(py)

    # Fallback: API venv nếu đã có demucs (hiếm)
    cur = Path(sys.executable)
    if _has_demucs(cur):
        return str(cur)

    if report and project_id:
        set_status(
            project_id,
            step="export",
            progress=62,
            message="Đang cài Demucs (xóa lời AI) — lần đầu có thể mất vài phút…",
            running=True,
        )
    if not py.is_file():
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            check=True,
            capture_output=True,
            timeout=180,
        )
    pip = [str(py), "-m", "pip"]
    subprocess.run(pip + ["install", "-U", "pip", "wheel"], capture_output=True, timeout=300)
    # torch CPU — nhẹ hơn CUDA, đủ cho demucs
    r_torch = subprocess.run(
        pip
        + [
            "install",
            "torch",
            "torchaudio",
            "--index-url",
            "https://download.pytorch.org/whl/cpu",
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if r_torch.returncode != 0:
        raise RuntimeError(
            "Không cài được PyTorch cho Demucs.\n"
            + ((r_torch.stderr or r_torch.stdout or "")[-800:])
        )
    r_dem = subprocess.run(
        pip + ["install", "demucs", "soundfile"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if r_dem.returncode != 0:
        raise RuntimeError(
            "Không cài được Demucs.\n" + ((r_dem.stderr or r_dem.stdout or "")[-800:])
        )
    if not _has_demucs(py):
        raise RuntimeError("Đã pip demucs nhưng import vẫn lỗi — kiểm tra server/.venv-demucs")
    return str(py)


def separate_no_vocals(
    project_id: str, video: Path, *, report: bool = True
) -> Path:
    """Demucs: bỏ stem vocals, giữ nhạc/SFX.

    Không dùng stereotools làm «xóa lời» — filter đó vẫn để lại lời, cache sai.
    Demucs lỗi → nền im (đúng hơn còn lời).
    report=False: gọi từ preview, không ghi đè status job xuất.
    """
    root = ensure_layout(project_id)
    stat = video.stat()
    # v5: bắt buộc Demucs thật; invalidate cache stereotools (v4 trở xuống)
    key = hashlib.sha1(
        f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|v5".encode()
    ).hexdigest()[:12]
    cache = root / "cache" / f"no_vocals_{key}.wav"
    if cache.exists() and cache.stat().st_size > 1024:
        return cache

    python = _demucs_python(project_id, report=report)
    work = root / "cache" / f"demucs_{key}"
    work.mkdir(parents=True, exist_ok=True)
    source_wav = work / "source.wav"
    run_cmd(
        project_id,
        [
            "ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100",
            str(source_wav),
        ],
    )

    demucs_ok = False
    result: Path | None = None
    separated = work / "separated"
    demucs_err = ""
    try:
        if report:
            set_status(
                project_id,
                step="export",
                progress=66,
                message="Demucs đang xóa lời (giữ nhạc/SFX)…",
                running=True,
            )
        proc = subprocess.run(
            [
                python, "-m", "demucs", "--two-stems", "vocals",
                "--shifts", "1", "--overlap", "0.25", "-j", "1",
                "-o", str(separated), str(source_wav),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if proc.returncode != 0:
            demucs_err = (proc.stderr or proc.stdout or f"exit {proc.returncode}")[-600:]
        result = separated / "htdemucs" / source_wav.stem / "no_vocals.wav"
        demucs_ok = result.exists() and result.stat().st_size > 1024
        if not demucs_ok and not demucs_err:
            demucs_err = "không thấy file no_vocals.wav sau Demucs"
    except Exception as e:
        demucs_ok = False
        demucs_err = str(e)[:600]

    if demucs_ok and result is not None:
        # Video thoại mono: stem gần im — boost nhẹ, KHÔNG trộn lại gốc.
        src_rms = max(_wav_rms(source_wav), 1e-6)
        stem_rms = _wav_rms(result)
        ratio = stem_rms / src_rms
        if ratio >= 0.12:
            gain = min(2.6, max(1.25, 0.72 / max(ratio, 0.12)))
        elif ratio >= 0.02:
            gain = min(3.5, max(1.5, 0.15 / max(ratio, 0.001)))
        else:
            # Gần như không còn nhạc/SFX — giữ gần im (đúng với clip chỉ lời)
            gain = 1.0
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y", "-i", str(result),
                "-af",
                f"volume={gain:.3f},alimiter=limit=0.95:level=disabled",
                "-c:a", "pcm_s16le", str(cache),
            ],
        )
    else:
        # Demucs thất bại: nền im — stereotools cũ để lại lời → lệch setting «Xóa lời».
        if report and project_id:
            set_status(
                project_id,
                step="export",
                progress=68,
                message=(
                    "Demucs lỗi — tạm tắt âm gốc (tránh còn lời). "
                    f"Chi tiết: {demucs_err[:180]}"
                    if demucs_err
                    else "Demucs lỗi — tạm tắt âm gốc (tránh còn lời)."
                ),
                running=True,
            )
        dur = max(0.1, ffprobe_duration(source_wav) or ffprobe_duration(video) or 1.0)
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", f"{dur:.3f}",
                "-c:a", "pcm_s16le", str(cache),
            ],
        )

    shutil.rmtree(work, ignore_errors=True)
    if not cache.exists():
        raise RuntimeError(
            "Không tạo được track xóa lời (Demucs)."
            + (f" {demucs_err}" if demucs_err else "")
        )
    return cache

def _bg_duck_expr(
    segments: list[dict[str, Any]], keep: float = 0.35, duck: float = 0.12
) -> str:
    """ffmpeg volume= expr: duck during speech windows, else keep BGM."""
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
        if merged and s <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    windows = [f"between(t\\,{s:.3f}\\,{e:.3f})" for s, e in merged]
    if not windows:
        return str(keep)
    return f"if({'+'.join(windows)}\\,{duck}\\,{keep})"


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
    """atempo chain for speed ratio (output_dur = input_dur / ratio)."""
    parts: list[str] = []
    r = max(0.05, float(ratio))
    while r > 2.0 + 1e-6:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5 - 1e-6:
        parts.append("atempo=0.5")
        r /= 0.5
    parts.append(f"atempo={r:.4f}")
    return ",".join(parts)


def plan_video_slowdown_factor(
    segments: list[dict[str, Any]],
    root: Path,
    *,
    match: str = "preferVideo",
) -> float:
    """Chỉ tính video_factor (>1 = chậm toàn video). Dùng lúc dub + xuất."""
    _clips, vf = _tts_clip_plan(segments, root, allow_video_slowdown=True, match=match)
    return float(vf)


# preferVideo: chậm cố định 0.80× (setpts 1/0.8) — đủ chỗ TTS dịch dài, không ép đọc
PREFER_VIDEO_SPEED = 0.80
PREFER_VIDEO_FACTOR = 1.0 / PREFER_VIDEO_SPEED  # 1.25


def _tts_clip_plan(
    segments: list[dict[str, Any]],
    root: Path,
    *,
    allow_video_slowdown: bool = True,
    match: str = "preferVideo",
) -> tuple[list[tuple[Path, float, float, float, float]], float]:
    """Trả (clips, video_factor).

    video_factor > 1 = chậm **toàn bộ** video để TTS gần tốc độ tự nhiên (áp dụng tất cả).
    preferVideo: cố định 0.80× (factor 1.25), gần như không ép TTS.
    clips: (wav, start_sec_scaled, slot_sec, tts_speed, volume)
    """
    ordered = sorted(
        [s for s in segments if s],
        key=lambda s: float(s.get("start") or 0),
    )
    gap = 0.04
    if match == "preferVideo":
        max_video_factor = PREFER_VIDEO_FACTOR
        soft_tts_speed = 1.02
        hard_tts_cap = 1.12
        fixed_factor = PREFER_VIDEO_FACTOR if allow_video_slowdown else 1.0
    elif match == "none":
        max_video_factor = 1.45
        soft_tts_speed = 1.08
        hard_tts_cap = 1.30
        fixed_factor = None
    else:
        max_video_factor = 1.35
        soft_tts_speed = 1.12
        hard_tts_cap = 1.60
        fixed_factor = None
    raw: list[tuple[Path, float, float, float, float, float]] = []  # wav, start, slot0, ad, volume, manual speed
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
            slot0 = max(0.15, next_start - start - gap)
        else:
            slot0 = max(0.15, (ad if ad > 0.05 else end - start) + 0.12)
        raw.append((wav, start, slot0, ad, max(0.0, min(2.0, float(seg.get("ttsVolume", 100)) / 100)), max(0.75, min(1.5, float(seg.get("ttsSpeed", 1))))))

    if not raw:
        return [], (fixed_factor if fixed_factor is not None else 1.0)

    video_factor = 1.0
    if fixed_factor is not None:
        video_factor = float(fixed_factor)
    elif allow_video_slowdown:
        needs: list[float] = []
        for _wav, _start, slot0, ad, _volume, manual_speed in raw:
            ad /= manual_speed
            if ad > 0.08 and slot0 > 0.05 and ad > slot0 * soft_tts_speed:
                needs.append(ad / (slot0 * soft_tts_speed))
        if needs:
            needs.sort()
            idx = min(len(needs) - 1, max(0, int(len(needs) * 0.90) - 1))
            video_factor = needs[idx]
            mid = needs[len(needs) // 2]
            video_factor = max(video_factor, min(mid, max_video_factor))
        video_factor = min(max_video_factor, max(1.0, video_factor))

    clips: list[tuple[Path, float, float, float, float]] = []
    for wav, start, slot0, ad, volume, manual_speed in raw:
        slot = slot0 * video_factor
        speed = 1.0
        if ad > 0.08 and ad > slot * 1.005:
            speed = min(hard_tts_cap, ad / max(slot, 0.05))
            speed = max(1.0, speed)
        eff = ad / max(speed * manual_speed, 0.05) if ad > 0.05 else slot
        trim = max(0.08, min(slot, eff + 0.02))
        clips.append((wav, start * video_factor, trim, speed * manual_speed, volume))
    clips.sort(key=lambda c: c[1])
    out: list[tuple[Path, float, float, float, float]] = []
    for i, (wav, start, trim, sp, volume) in enumerate(clips):
        next_start = clips[i + 1][1] if i + 1 < len(clips) else None
        if next_start is not None:
            trim = min(trim, max(0.08, next_start - start - 0.03))
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
) -> Path:
    """Trộn TTS theo timeline đã scale. TTS speed nhẹ; video chậm bù."""
    ordered_plan, plan_vf = _tts_clip_plan(
        segments, root, allow_video_slowdown=allow_video_slowdown, match=match
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
        (f"v8|vf{plan_vf:.3f}|" + "|".join(signature)).encode()
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
            if speed > 1.03:
                parts.append(_atempo_chain(speed))
            elif speed < 0.97:
                parts.append(_atempo_chain(speed))
            parts.append(f"volume={volume:.3f}")
            fade = min(0.08, max(0.025, max_sec * 0.08))
            st_fade = max(0.0, max_sec - fade)
            parts.append(f"atrim=0:{max_sec:.3f}")
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
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
                "-map", "[aout]", "-c:a", "pcm_s16le", str(batch_out),
            ],
        )
        batches.append(batch_out)

    if len(batches) == 1:
        batches[0].replace(out)
    else:
        inputs = [arg for wav in batches for arg in ("-i", str(wav))]
        labels = "".join(f"[{i}:a]" for i in range(len(batches)))
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y", *inputs, "-filter_complex",
                labels
                + f"amix=inputs={len(batches)}:duration=longest:normalize=0,"
                f"alimiter=limit=0.95[aout]",
                "-map", "[aout]", "-c:a", "pcm_s16le", str(out),
            ],
        )
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
) -> Path:
    """Đặt TTS theo timeline; chậm **toàn video** nếu TTS dài hơn slot."""
    root = ensure_layout(project_id)
    duration = ffprobe_duration(video)
    _clips, video_factor = _tts_clip_plan(
        segments, root, allow_video_slowdown=allow_video_slowdown, match=match
    )
    voice_track = _mix_tts_track(
        project_id,
        segments,
        root,
        video_factor=video_factor,
        allow_video_slowdown=allow_video_slowdown,
        match=match,
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
    inputs += ["-i", str(voice_track)]
    filters.append(f"[{next_input_index}:a]anull[voice]")

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

    if has_bg:
        vol = _bg_duck_expr(duck_segs, keep=keep, duck=duck)
        if use_preseparated:
            source_filter = "anull"
        elif original_audio_mode in ("vocals", "music", "no_vocals"):
            mode = "music" if original_audio_mode == "no_vocals" else original_audio_mode
            source_filter = _source_audio_filter(mode)
        else:
            source_filter = "anull"
        # Chậm BGM cùng nhịp video (atempo < 1)
        bg_tempo = _atempo_chain(1.0 / video_factor) if video_factor > 1.001 else "anull"
        filters.append(
            f"[{source_audio_index}:a]{source_filter},{bg_tempo},"
            f"apad=whole_dur={out_dur:.3f},volume={vol}:eval=frame[bg]"
        )
        filters.append(
            "[bg][voice]amix=inputs=2:duration=longest:dropout_transition=0:"
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
    fc = ";".join(filters)
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        fc,
        "-map",
        map_video,
        "-map",
        map_audio,
        *vcodec,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-shortest",
        "-t",
        str(out_dur),
        str(out),
    ]
    run_cmd(project_id, cmd)
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


if __name__ == "__main__":
    # ponytail: self-check — Windows phải dùng Scripts/python.exe, không bin/python
    sr = Path(__file__).resolve().parents[2]
    venv_py = sr / ".venv-demucs" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    # Chưa cài vẫn ok — _demucs_python sẽ tạo; chỗ này chỉ check path shape
    if sys.platform == "win32":
        assert "Scripts" in str(venv_py)
    ratio = 0.05
    gain = min(3.5, max(1.5, 0.15 / max(ratio, 0.001)))
    assert gain > 1.5
    print("mux self-check ok:", venv_py)
