"""OCR package — tách khỏi Whisper ASR và caption burn layout."""
from .extract import asr_paddleocr, _rapidocr_labels, _rapidocr_gpu_kwargs, _ocr_join_lines
from .locate import ocr_mid_labels, ocr_mid_vertical, ocr_mid_hardsub_boxes, rapidocr_labels

__all__ = [
    "asr_paddleocr",
    "_rapidocr_labels",
    "_rapidocr_gpu_kwargs",
    "_ocr_join_lines",
    "ocr_mid_labels",
    "ocr_mid_vertical",
    "ocr_mid_hardsub_boxes",
    "rapidocr_labels",
]
