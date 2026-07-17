"""Whisper ASR. RapidOCR sống ở pipeline.ocr.extract — re-export để tương thích."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from .core.project import set_status
from .core.resources import adaptive_workers
from .ocr.extract import (  # noqa: F401 — public re-exports for run.py / burn / tests
    asr_paddleocr,
    _ocr_join_lines,
    _rapidocr_gpu_kwargs,
    _rapidocr_labels,
    prepare_cuda_dlls as _prepare_cuda_dlls,
)

# 1 model / process; reload khi đổi cpu_threads (Luồng).
_whisper = None
_whisper_threads: int | None = None
_whisper_lock = threading.Lock()

# Siết biên segment theo word timestamps — KHÔNG tách text (tránh 1 câu → nhiều mảnh).
_WORD_PAD_START = 0.08
_WORD_PAD_END = 0.18
_WORD_MIN_PROB = 0.12
_MAX_SEG_DUR = 14.0  # trần nếu không có words / words lỗi
_MIN_SEG_DUR = 0.12


def _resolve_asr_workers(workers: int | None) -> int:
    return adaptive_workers(workers, kind="cpu", cap=16)


def get_whisper(workers: int = 2):
    """CPU: cpu_threads = Luồng (CTranslate2). CUDA: threads ít ảnh hưởng."""
    global _whisper, _whisper_threads
    from faster_whisper import WhisperModel

    thr = _resolve_asr_workers(workers)
    with _whisper_lock:
        if _whisper is not None and _whisper_threads == thr:
            return _whisper

        device = "cpu"
        compute = "int8"
        try:
            _prepare_cuda_dlls()
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                device, compute = "cuda", "float16"
        except (ImportError, RuntimeError):
            pass

        # CPU: nhiều thread = nhanh hơn rõ. CUDA: 1–4 đủ (kernel GPU).
        cpu_threads = thr if device == "cpu" else min(4, thr)
        _whisper = WhisperModel(
            "base",
            device=device,
            compute_type=compute,
            cpu_threads=cpu_threads,
            num_workers=1,  # 1 file/job — không fan-out nhiều transcribe
        )
        _whisper_threads = thr
        return _whisper


def _word_parts(seg: Any) -> list[tuple[float, float, str, float]]:
    """[(start, end, word, prob), ...] — bỏ token rỗng / xác suất quá thấp."""
    raw = getattr(seg, "words", None) or []
    out: list[tuple[float, float, str, float]] = []
    for w in raw:
        text = (getattr(w, "word", None) or "").strip()
        if not text:
            continue
        try:
            s = float(getattr(w, "start", 0) or 0)
            e = float(getattr(w, "end", s) or s)
            p = float(getattr(w, "probability", 1.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if e < s:
            s, e = e, s
        if p < _WORD_MIN_PROB and len(text) <= 1:
            continue
        out.append((s, e, text, p))
    return out


def _tighten_bounds(
    seg_start: float,
    seg_end: float,
    parts: list[tuple[float, float, str, float]],
) -> tuple[float, float]:
    """Chỉ siết start/end theo từ đầu–cuối; giữ nguyên 1 câu (không tách text)."""
    s0 = max(0.0, float(seg_start))
    e0 = max(s0 + _MIN_SEG_DUR, float(seg_end))
    if not parts:
        if e0 - s0 > _MAX_SEG_DUR:
            e0 = s0 + _MAX_SEG_DUR
        return s0, e0

    # Bỏ tail/head word xác suất thấp nếu kéo biên vô lý
    usable = [p for p in parts if p[3] >= _WORD_MIN_PROB or len(p[2]) > 1]
    if not usable:
        usable = parts
    ws = usable[0][0]
    we = usable[-1][1]
    start = max(0.0, ws - _WORD_PAD_START)
    end = max(start + _MIN_SEG_DUR, we + _WORD_PAD_END)
    # Không nới quá biên Whisper (trừ pad nhỏ)
    start = max(start, s0 - 0.05)
    end = min(end, e0 + 0.05)
    # Chỉ co khi words gọn hơn segment thô (cắt silence 2 đầu)
    if end - start < e0 - s0:
        return start, max(start + _MIN_SEG_DUR, end)
    if e0 - s0 > _MAX_SEG_DUR:
        return s0, s0 + _MAX_SEG_DUR
    return s0, e0


def _segments_from_whisper(seg: Any) -> list[dict[str, Any]]:
    """1 segment Whisper → đúng 1 segment (text gốc, biên siết)."""
    text = (getattr(seg, "text", None) or "").strip()
    if not text:
        return []
    seg_start = float(getattr(seg, "start", 0) or 0)
    seg_end = float(getattr(seg, "end", seg_start) or seg_start)
    parts = _word_parts(seg)
    start, end = _tighten_bounds(seg_start, seg_end, parts)
    return [
        {
            "id": str(uuid.uuid4()),
            "index": 0,
            "start": start,
            "end": end,
            "source": text,  # luôn full text Whisper — không ghép/tách theo words
            "translation": "",
            "voice": "",
        }
    ]


def asr_whisper(
    wav: Path,
    source_lang: str,
    *,
    workers: int = 2,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Whisper 1 lần cả file; siết start/end theo word timestamps."""
    import time

    thr = _resolve_asr_workers(workers)
    if project_id:
        set_status(
            project_id,
            step="asr",
            progress=18,
            message="Tải model Whisper…",
            running=True,
        )
    model = get_whisper(thr)
    if project_id:
        device = getattr(getattr(model, "model", None), "device", "cpu")
        set_status(
            project_id,
            step="asr",
            progress=22,
            message=(
                "Whisper đang nhận dạng (CUDA) — % có thể đứng lâu…"
                if device == "cuda"
                else f"Whisper đang nhận dạng ({thr} luồng CPU) — % có thể đứng lâu…"
            ),
            running=True,
        )
    lang = None if source_lang in ("", "auto") else source_lang
    # beam=1 + VAD + word_timestamps: siết biên, tránh caption ôm silence 20–40s.
    segments, _info = model.transcribe(
        str(wav),
        language=lang,
        vad_filter=True,
        # 350ms: tách câu sát hơn; vẫn giữ pause ngắn trong câu
        vad_parameters=dict(
            min_silence_duration_ms=350,
            speech_pad_ms=120,
        ),
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=False,
        word_timestamps=True,
    )
    out: list[dict[str, Any]] = []
    last_report = 0.0
    for seg in segments:
        rows = _segments_from_whisper(seg)
        if not rows:
            continue
        out.extend(rows)
        # heartbeat — Whisper hay đứng % ở 22; message đổi để UI không tưởng đơ
        if project_id:
            now = time.monotonic()
            if len(out) == 1 or now - last_report >= 1.5:
                last_report = now
                t_end = float(rows[-1]["end"])
                pct = min(48, 22 + len(out))
                set_status(
                    project_id,
                    step="asr",
                    progress=pct,
                    message=f"Whisper đã nhận {len(out)} đoạn · ~{t_end:.0f}s…",
                    running=True,
                )
    # re-index + sort (sau split gap)
    out.sort(key=lambda s: (float(s.get("start") or 0), float(s.get("end") or 0)))
    for i, row in enumerate(out, start=1):
        row["index"] = i
    # Không chồng end lên start câu sau (TTS/caption sạch)
    for i in range(len(out) - 1):
        nxt = float(out[i + 1]["start"])
        cur_end = float(out[i]["end"])
        if cur_end > nxt - 0.02:
            out[i]["end"] = max(float(out[i]["start"]) + _MIN_SEG_DUR, nxt - 0.02)
    if project_id and out:
        set_status(
            project_id,
            step="asr",
            progress=50,
            message=f"Whisper xong — {len(out)} đoạn",
            running=True,
        )
    return out


