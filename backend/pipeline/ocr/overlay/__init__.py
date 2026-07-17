"""Overlay OCR subpackage — re-exports."""
from pipeline.ocr import overlay_scan as scan  # noqa: F401
from pipeline.ocr import overlay_cover as cover  # noqa: F401

__all__ = ["scan", "cover"]
