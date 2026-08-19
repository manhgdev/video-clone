from __future__ import annotations

import sys
from unittest.mock import MagicMock


def test_nvidia_hardware_prefers_cuda_even_if_torch_probe_fails(monkeypatch) -> None:
    from pipeline.core import accel

    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = False
    fake_torch.backends.mps.is_available.return_value = False
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(accel, "_nvidia_smi", lambda: True)
    accel._cache.clear()

    assert accel.preferred_torch_device(refresh=True) == "cuda"


def test_ocr_prefers_directml_when_cuda_is_unavailable(monkeypatch) -> None:
    from pipeline.ocr.extract_parts import runtime

    fake_ort = MagicMock()
    fake_ort.get_available_providers.return_value = [
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setattr(runtime, "prepare_cuda_dlls", lambda: None)
    runtime._rapidocr_gpu_kwargs.cache_clear()

    kwargs = runtime._rapidocr_gpu_kwargs()

    assert kwargs["det_use_dml"] is True
    assert kwargs["det_use_cuda"] is False
