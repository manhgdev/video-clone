"""Mix TTS onto video with BGM ducking."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from ..core.jobs import run_cmd
from ..core.media import _has_audio_stream, ffprobe_duration
from ..core.project import ensure_layout, out_final


def _wav_rms(path: Path) -> float:
    """RMS thô pcm_s16le (0..1) — chẩn đoán stem Demucs gần im."""
    import struct
    import subprocess

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


def separate_no_vocals(project_id: str, video: Path) -> Path:
    """Demucs: bỏ stem vocals, giữ nhạc/SFX. Stem quá im → fallback stereotools."""
    root = ensure_layout(project_id)
    stat = video.stat()
    # v3: boost loudnorm stem no_vocals (trước đây hay bé)
    key = hashlib.sha1(
        f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|v3".encode()
    ).hexdigest()[:12]
    cache = root / "cache" / f"no_vocals_{key}.wav"
    if cache.exists() and cache.stat().st_size > 1024:
        return cache

    server_root = Path(__file__).resolve().parents[2]
    python = server_root / ".venv-demucs" / "bin" / "python"
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
    if python.exists():
        separated = work / "separated"
        try:
            run_cmd(
                project_id,
                [
                    str(python), "-m", "demucs", "--two-stems", "vocals",
                    "--shifts", "1", "--overlap", "0.25", "-j", "1",
                    "-o", str(separated), str(source_wav),
                ],
            )
            result = separated / "htdemucs" / "source" / "no_vocals.wav"
            demucs_ok = result.exists() and result.stat().st_size > 1024
        except Exception:
            demucs_ok = False

    # Video thoại mono: Demucs nhét hết vào vocals → no_vocals gần im.
    # Nếu stem < ~12% năng lượng gốc → fallback hạ mid (giữ nhạc/SFX).
    use_fallback = True
    if demucs_ok and result is not None:
        src_rms = max(_wav_rms(source_wav), 1e-6)
        stem_rms = _wav_rms(result)
        ratio = stem_rms / src_rms
        if ratio >= 0.12:
            # Boost stem về gần mức nền (Demucs hay xuất nhỏ hơn gốc)
            # ratio 0.12→0.5 → gain ~2.2→1.35; cap 2.6, sàn 1.25
            gain = min(2.6, max(1.25, 0.72 / max(ratio, 0.12)))
            run_cmd(
                project_id,
                [
                    "ffmpeg", "-y", "-i", str(result),
                    "-af",
                    f"volume={gain:.3f},alimiter=limit=0.95:level=disabled",
                    "-c:a", "pcm_s16le", str(cache),
                ],
            )
            use_fallback = False
        else:
            # Trộn stem + original hạ mid + boost
            run_cmd(
                project_id,
                [
                    "ffmpeg", "-y",
                    "-i", str(result),
                    "-i", str(source_wav),
                    "-filter_complex",
                    (
                        "[1:a]" + _source_audio_filter("music") + "[m];"
                        "[0:a]volume=1.6[s];"
                        "[s][m]amix=inputs=2:duration=first:weights=0.5 0.5:"
                        "normalize=0,volume=1.55,alimiter=limit=0.95:level=disabled[aout]"
                    ),
                    "-map", "[aout]", "-c:a", "pcm_s16le", str(cache),
                ],
            )
            use_fallback = False

    if use_fallback:
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y", "-i", str(source_wav),
                "-af",
                _source_audio_filter("music")
                + ",volume=1.25,alimiter=limit=0.95:level=disabled",
                "-c:a", "pcm_s16le", str(cache),
            ],
        )

    shutil.rmtree(work, ignore_errors=True)
    if not cache.exists():
        raise RuntimeError("Không tạo được track xóa lời (Demucs/fallback).")
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


def _tts_clip_plan(
    segments: list[dict[str, Any]], root: Path
) -> tuple[list[tuple[Path, float, float, float]], float]:
    """Trả (clips, video_factor).

    video_factor > 1 = chậm video để TTS gần tốc độ tự nhiên.
    clips: (wav, start_sec_scaled, slot_sec, tts_speed)
    """
    ordered = sorted(
        [s for s in segments if s],
        key=lambda s: float(s.get("start") or 0),
    )
    gap = 0.04
    # Ưu tiên chậm video; TTS chỉ tăng tốc nhẹ
    max_video_factor = 1.35  # chậm tối đa ~35%
    soft_tts_speed = 1.12  # mục tiêu đọc gần tự nhiên
    hard_tts_speed = 1.25  # trần sau khi đã chậm video
    raw: list[tuple[Path, float, float, float]] = []  # wav, start, slot0, ad
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
        raw.append((wav, start, slot0, ad))

    if not raw:
        return [], 1.0

    # video_factor theo p90 nhu cầu (1 outlier không kéo cả video quá chậm)
    needs: list[float] = []
    for _wav, _start, slot0, ad in raw:
        if ad > 0.08 and slot0 > 0.05 and ad > slot0 * soft_tts_speed:
            needs.append(ad / (slot0 * soft_tts_speed))
    video_factor = 1.0
    if needs:
        needs.sort()
        # p90
        idx = min(len(needs) - 1, max(0, int(len(needs) * 0.90) - 1))
        video_factor = needs[idx]
        # không thấp hơn median nếu đa số cần chậm
        mid = needs[len(needs) // 2]
        video_factor = max(video_factor, min(mid, max_video_factor))
    video_factor = min(max_video_factor, max(1.0, video_factor))

    clips: list[tuple[Path, float, float, float]] = []
    for wav, start, slot0, ad in raw:
        slot = slot0 * video_factor
        speed = 1.0
        if ad > 0.08 and ad > slot * 1.005:
            # fit đầy đủ vào slot (thường ≤1.25 nhờ chậm video; outlier tới 1.6)
            speed = min(1.60, ad / max(slot, 0.05))
            speed = max(1.0, speed)
        clips.append((wav, start * video_factor, slot, speed))
    return clips, video_factor


def _mix_tts_track(
    project_id: str,
    segments: list[dict[str, Any]],
    root: Path,
    *,
    video_factor: float = 1.0,
) -> Path:
    """Trộn TTS theo timeline đã scale. TTS speed nhẹ; video chậm bù."""
    ordered_plan, plan_vf = _tts_clip_plan(segments, root)
    # Dùng plan (đã tính factor); video_factor chỉ để cache key khớp mux_dub
    if abs(video_factor - plan_vf) > 0.02 and video_factor > 1.0:
        # re-scale starts/slots if caller forces different factor
        scale = video_factor / max(plan_vf, 1e-6)
        ordered_plan = [
            (w, s * scale, slot * scale, sp)
            for w, s, slot, sp in ordered_plan
        ]
        plan_vf = video_factor

    if not ordered_plan:
        raise RuntimeError("Chưa có audio TTS — chạy Lồng tiếng trước.")

    signature = [
        f"{w.name}@{s:.3f}@{slot:.3f}@{sp:.3f}"
        for w, s, slot, sp in ordered_plan
    ]
    key = hashlib.sha1(
        (f"v6|vf{plan_vf:.3f}|" + "|".join(signature)).encode()
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
        for i, (wav, start_sec, max_sec, speed) in enumerate(batch):
            delay_ms = max(0, int(start_sec * 1000))
            inputs += ["-i", str(wav)]
            parts: list[str] = []
            if speed > 1.03:
                parts.append(_atempo_chain(speed))
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
) -> Path:
    """Đặt TTS theo timeline; chậm video nhẹ nếu TTS dài hơn slot."""
    root = ensure_layout(project_id)
    duration = ffprobe_duration(video)
    _clips, video_factor = _tts_clip_plan(segments, root)
    voice_track = _mix_tts_track(
        project_id, segments, root, video_factor=video_factor
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
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
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
