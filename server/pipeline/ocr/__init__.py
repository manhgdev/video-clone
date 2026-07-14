"""OCR package — tách khỏi Whisper ASR và caption burn layout.

Modules:
  extract.py       — RapidOCR hardsub đáy (+ gọi overlay_scan)
  overlay_scan.py  — mid / dọc / nhãn (quét thưa theo độ dài video)
  overlay_cover.py — che+chữ dịch classic (pre-preview) cho overlay
  locate.py        — định vị box lúc xuất
  labels.py        — layout nhãn / cột
"""
from .extract import asr_paddleocr, _rapidocr_labels, _rapidocr_gpu_kwargs, _ocr_join_lines
from .locate import ocr_mid_labels, ocr_mid_vertical, ocr_mid_hardsub_boxes, rapidocr_labels
from .overlay_cover import classic_cover_fit, is_mid_flash_source, use_classic_overlay_cover
from .overlay_scan import adaptive_bottom_fps, adaptive_overlay_step, run_overlay_ocr

__all__ = [
    "asr_paddleocr",
    "_rapidocr_labels",
    "_rapidocr_gpu_kwargs",
    "_ocr_join_lines",
    "ocr_mid_labels",
    "ocr_mid_vertical",
    "ocr_mid_hardsub_boxes",
    "rapidocr_labels",
    "classic_cover_fit",
    "is_mid_flash_source",
    "use_classic_overlay_cover",
    "adaptive_bottom_fps",
    "adaptive_overlay_step",
    "run_overlay_ocr",
]
