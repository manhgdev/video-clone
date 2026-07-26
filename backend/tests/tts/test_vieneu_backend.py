"""VieNeu backend prefers CUDA when torch.cuda is available.

Seam thật là ``pipeline.core.accel.preferred_torch_device`` — _resolve_backend
uỷ quyền cho accel.preferred_vieneu_backend từ khi refactor.
"""


def test_resolve_backend_auto_cpu_without_torch(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu as v

    monkeypatch.delenv("VIENEU_BACKEND", raising=False)
    monkeypatch.setattr("pipeline.core.accel.preferred_torch_device", lambda **_: "cpu")
    assert v._resolve_backend() == ("onnx", "cpu")


def test_resolve_backend_auto_cuda(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu as v

    monkeypatch.delenv("VIENEU_BACKEND", raising=False)
    monkeypatch.setattr("pipeline.core.accel.preferred_torch_device", lambda **_: "cuda")
    assert v._resolve_backend() == ("pytorch", "cuda")


def test_resolve_backend_force_onnx(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu as v

    monkeypatch.setenv("VIENEU_BACKEND", "onnx")
    monkeypatch.setattr("pipeline.core.accel.preferred_torch_device", lambda **_: "cuda")
    assert v._resolve_backend() == ("onnx", "cpu")
