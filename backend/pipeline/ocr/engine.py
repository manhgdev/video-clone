"""OCR engine facade — RapidOCR / CUDA helpers."""
from pipeline.ocr.extract import (  # noqa: F401
    _rapidocr_gpu_kwargs,
    _rapidocr_labels,
    prepare_cuda_dlls,
)

__all__ = ["prepare_cuda_dlls", "_rapidocr_gpu_kwargs", "_rapidocr_labels"]
