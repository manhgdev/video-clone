"""TTS-fit videoSpeed assignment."""
from __future__ import annotations

from typing import Any

def assign_tts_fit_speeds(
    segments: list[dict[str, Any]],
    *,
    match: str,
) -> int:
    """TTS dài hơn khe timeline → videoSpeed < 1: kéo dài span câu, đẩy trước/sau.

    retime: out_span = (end-start)/speed; gap sau end giữ 1×; câu sau map_time muộn hơn.
    stretch mode: không gán (khớp bằng atempo TTS).
    """
    if match == "stretch":
        for seg in segments:
            seg.pop("videoSpeed", None)
        return 0

    soft = 1.03
    min_speed = 0.35  # chậm tối đa ~2.86×
    ordered = sorted(segments, key=lambda s: float(s.get("start") or 0))
    n = 0
    for i, seg in enumerate(ordered):
        # audioDuration = wav 1×; khi phát, FE (dubMath.dubAudioAbsEnd) và
        # export (mux_audio) đều chia cho ttsSpeed thủ công — fit phải dùng
        # cùng độ dài hiệu dụng, không thì câu chỉnh nhanh vẫn bị giãn thừa.
        manual = float(seg.get("ttsSpeed") or 1) or 1.0
        manual = max(0.75, min(1.5, manual))
        ad = float(seg.get("audioDuration") or 0) / manual
        if ad <= 0.08:
            seg.pop("videoSpeed", None)
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        window = max(0.12, end - start)
        next_start = None
        for j in range(i + 1, len(ordered)):
            ns = float(ordered[j].get("start") or 0)
            if ns > start + 0.02:
                next_start = ns
                break
        # Câu cuối: không có khe sau → gap_after=0 (trước gán 1e9 → không bao giờ giãn)
        gap_after = max(0.0, next_start - end) if next_start is not None else 0.0
        need_speech = max(0.12, ad - gap_after + 0.05)
        if need_speech <= window * soft:
            seg.pop("videoSpeed", None)
            continue
        speed = max(min_speed, min(1.0, window / need_speech))
        speed = round(speed, 3)
        if speed >= 0.995:
            seg.pop("videoSpeed", None)
            continue
        if next_start is not None and window / speed + gap_after < ad * 0.98:
            extra = min(gap_after * 0.85, max(0.0, ad - window / min_speed))
            if extra > 0.05:
                new_end = min(next_start - 0.02, end + extra)
                if new_end > end + 0.04:
                    seg["end"] = round(new_end, 3)
                    window = max(0.12, new_end - start)
                    gap_after = max(0.0, next_start - new_end)
                    need_speech = max(0.12, ad - gap_after + 0.05)
                    speed = max(min_speed, min(1.0, window / need_speech))
                    speed = round(speed, 3)
        if speed >= 0.995:
            seg.pop("videoSpeed", None)
            continue
        seg["videoSpeed"] = speed
        n += 1
    return n

