"""GPU inventory + VRAM-aware assignment. Reuses detect_device / accel."""

from .manager import assign_device, diagnostics, list_gpus, model_residency, vram_free_mb

__all__ = [
    "assign_device",
    "diagnostics",
    "list_gpus",
    "model_residency",
    "vram_free_mb",
]
