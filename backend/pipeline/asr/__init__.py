"""ASR package — Whisper + OCR hardsub re-export."""
from .whisper import (  # noqa: F401
    _tighten_bounds,
    asr_whisper,
    get_whisper,
    warm_whisper,
)
from pipeline.ocr.extract import (  # noqa: F401
    _rapidocr_gpu_kwargs,
    _rapidocr_labels,
    asr_paddleocr,
)

__all__ = [
    "asr_whisper",
    "asr_paddleocr",
    "get_whisper",
    "warm_whisper",
    "_tighten_bounds",
    "_rapidocr_gpu_kwargs",
    "_rapidocr_labels",
]
