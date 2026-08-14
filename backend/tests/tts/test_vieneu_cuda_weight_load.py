from __future__ import annotations

import sys
from types import SimpleNamespace


def test_cuda_weights_stage_on_cpu_without_pin_allocator(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu

    moved: list[str] = []

    class Torch:
        uint8 = object()
        cuda = SimpleNamespace(init=lambda: None)

        @staticmethod
        def empty(*_args, **_kwargs):
            raise RuntimeError("Need to provide pin_memory allocator to use pin memory.")

    def original(model, _filename, strict=True, device="cpu"):
        assert strict is False
        assert device == "cpu"
        return "loaded"

    safe_torch = SimpleNamespace(load_model=original)
    monkeypatch.setitem(sys.modules, "torch", Torch)
    monkeypatch.setitem(sys.modules, "safetensors", SimpleNamespace(torch=safe_torch))
    monkeypatch.setitem(sys.modules, "safetensors.torch", safe_torch)

    assert vieneu._prepare_cuda_weight_load("pytorch", "cuda") is True
    model = SimpleNamespace(to=lambda device: moved.append(device))
    assert safe_torch.load_model(model, "weights", strict=False, device="cuda") == "loaded"
    assert moved == ["cuda"]


def test_cuda_weights_keep_direct_loader_when_pin_allocator_works(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu

    fake_torch = SimpleNamespace(
        uint8=object(),
        cuda=SimpleNamespace(init=lambda: None),
        empty=lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert vieneu._prepare_cuda_weight_load("pytorch", "cuda") is False
