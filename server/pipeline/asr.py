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


def asr_whisper(
    wav: Path,
    source_lang: str,
    *,
    workers: int = 2,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Whisper 1 lần cả file; Luồng → cpu_threads CTranslate2."""
    thr = _resolve_asr_workers(workers)
    model = get_whisper(thr)
    if project_id:
        device = getattr(getattr(model, "model", None), "device", "cpu")
        set_status(
            project_id,
            step="asr",
            progress=22,
            message=(
                "Whisper ASR (CUDA)…"
                if device == "cuda"
                else f"Whisper ASR ({thr} luồng CPU)…"
            ),
            running=True,
        )
    lang = None if source_lang in ("", "auto") else source_lang
    # beam=1 + VAD + condition_on_previous=False: nhanh hơn default ~2–3× trên CPU.
    segments, _info = model.transcribe(
        str(wav),
        language=lang,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=False,
    )
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(segments, start=1):
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append(
            {
                "id": str(uuid.uuid4()),
                "index": i,
                "start": float(seg.start),
                "end": float(seg.end),
                "source": text,
                "translation": "",
                "voice": "",
            }
        )
    return out


