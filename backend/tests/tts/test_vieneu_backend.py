"""VieNeu backend prefers CUDA when torch.cuda is available."""


def test_resolve_backend_auto_cpu_without_torch(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu as v

    monkeypatch.delenv("VIENEU_BACKEND", raising=False)
    monkeypatch.setattr(v, "_torch_cuda_ready", lambda: False)
    assert v._resolve_backend() == ("onnx", "cpu")


def test_resolve_backend_auto_cuda(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu as v

    monkeypatch.delenv("VIENEU_BACKEND", raising=False)
    monkeypatch.setattr(v, "_torch_cuda_ready", lambda: True)
    assert v._resolve_backend() == ("pytorch", "cuda")


def test_resolve_backend_force_onnx(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu as v

    monkeypatch.setenv("VIENEU_BACKEND", "onnx")
    monkeypatch.setattr(v, "_torch_cuda_ready", lambda: True)
    assert v._resolve_backend() == ("onnx", "cpu")
