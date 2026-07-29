"""TTS fit: nén AUDIO cho vừa thước — KHÔNG bao giờ giãn video.

Contract 2026-07-27 (yêu cầu user): thước timeline là bất khả xâm phạm —
preview 8:39 thì file xuất đúng 8:39. TTS câu nào dài hơn khe (tới câu sau)
thì atempo nén ≤2×; videoSpeed < 1 (miền auto-fit cũ) bị khai tử ở mọi nơi.
videoSpeed ≥ 1 do user đặt qua menu «Tốc độ video» vẫn giữ nguyên.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _drop_auto_speed(seg: dict[str, Any]) -> None:
    """Xoá videoSpeed thuộc miền auto-fit (<1); tôn trọng speed user (≥1)."""
    try:
        if float(seg.get("videoSpeed") or 1) < 0.995:
            seg.pop("videoSpeed", None)
    except (TypeError, ValueError):
        seg.pop("videoSpeed", None)


def strip_auto_video_speeds(segments: list[dict[str, Any]]) -> None:
    for seg in segments:
        _drop_auto_speed(seg)


def fit_tts_audio_to_slots(
    segments: list[dict[str, Any]], root: Path, *, match: str, bake: float = 1.0
) -> int:
    """Nén wav TTS (atempo) cho vừa khe tới câu sau. Trả số wav đã nén.

    Wav dùng chung nhiều câu (cùng text+voice) → fit theo khe HẸP NHẤT.
    Câu cuối không có câu sau → để tự nhiên. match="none": user tắt khớp —
    không đụng audio.

    bake: đồng hồ lúc dub (ttsBake). Dub ở 0.8 rồi nâng 1× thì giọng sẽ
    phát nhanh thêm ×(1/bake)=1.25 — trần nén hạ còn 2.0×bake để TỔNG tốc
    độ giọng nghe được không vượt ~2×.
    """
    from pipeline.core.media import ffprobe_duration
    from pipeline.tts.audio_utils import fit_duration

    strip_auto_video_speeds(segments)
    if match == "none":
        return 0

    ordered = sorted(segments, key=lambda s: float(s.get("start") or 0))
    by_wav: dict[str, float] = {}
    by_wav_manual: dict[str, float] = {}
    for i, seg in enumerate(ordered):
        name = str(seg.get("audioFile") or "")
        ad = float(seg.get("audioDuration") or 0)
        if not name or ad <= 0.08:
            continue
        # Phát chia cho ttsSpeed thủ công → khe hiệu dụng nhân lại (khớp
        # dubAudioAbsEnd của FE và _tts_clip_plan của mux_audio).
        manual = max(0.75, min(1.5, float(seg.get("ttsSpeed") or 1) or 1.0))
        start = float(seg.get("start") or 0)
        next_start = None
        for j in range(i + 1, len(ordered)):
            ns = float(ordered[j].get("start") or 0)
            if ns > start + 0.02:
                next_start = ns
                break
        if next_start is None:
            continue  # câu cuối: không ai bị đè
        slot = max(0.15, next_start - start - 0.03) * manual
        by_wav[name] = min(by_wav.get(name, 1e9), slot)
        by_wav_manual[name] = max(by_wav_manual.get(name, 1.0), manual)

    n = 0
    for name, slot in by_wav.items():
        wav = root / "tts" / name
        if not wav.is_file():
            continue
        dur = float(ffprobe_duration(wav) or 0.0)
        if dur <= 0.05 or dur <= slot * 1.04:
            continue
        # Tổng tốc độ tự động + tốc độ user không vượt 1.25×; câu còn dài sẽ
        # được giữ nguyên phần dư thay vì đọc gấp khó nghe.
        max_compress = max(1.0, 1.25 / by_wav_manual.get(name, 1.0))
        target = max(slot, dur / max_compress)
        try:
            new_dur = float(fit_duration(wav, target, "stretch", force_refit=True))
        except Exception:
            continue
        for seg in segments:
            if str(seg.get("audioFile") or "") == name:
                seg["audioDuration"] = new_dur
        n += 1
    return n


def assign_tts_fit_speeds(
    segments: list[dict[str, Any]], *, match: str
) -> int:
    """Giữ tên cũ cho call site/test cũ — giờ CHỈ dọn videoSpeed auto (<1).

    Không bao giờ gán videoSpeed nữa: video không giãn theo TTS.
    """
    strip_auto_video_speeds(segments)
    return 0
