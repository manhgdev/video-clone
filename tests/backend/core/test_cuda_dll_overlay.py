"""Overlay torch cuDNN onto ctranslate2 — do not fall back to CPU."""

from pathlib import Path


def test_overlay_copies_torch_cudnn(tmp_path: Path, monkeypatch) -> None:
    from pipeline.core import cuda_dll as m

    src = tmp_path / "torch_lib"
    dst = tmp_path / "ctranslate2"
    src.mkdir()
    dst.mkdir()
    (src / "cudnn64_9.dll").write_bytes(b"torch-cudnn")
    (dst / "cudnn64_9.dll").write_bytes(b"broken-ct2")
    monkeypatch.setattr(m, "_torch_lib", lambda: src)
    monkeypatch.setattr(m, "_ctranslate2_dir", lambda: dst)
    m._overlay_onto_ctranslate2(src)
    assert (dst / "cudnn64_9.dll").read_bytes() == b"torch-cudnn"


def test_overlay_copies_cublas_onto_sherpa(tmp_path: Path, monkeypatch) -> None:
    from pipeline.core import cuda_dll as m

    src = tmp_path / "torch_lib"
    dst = tmp_path / "sherpa_onnx" / "lib"
    src.mkdir()
    dst.mkdir(parents=True)
    (src / "cublasLt64_12.dll").write_bytes(b"torch-cublas")
    monkeypatch.setattr(m, "_torch_lib", lambda: src)
    monkeypatch.setattr(m, "_sherpa_lib", lambda: dst)
    m._overlay_onto_sherpa(src)
    assert (dst / "cublasLt64_12.dll").read_bytes() == b"torch-cublas"


def test_whisper_does_not_retry_cpu() -> None:
    text = Path("backend/pipeline/asr/whisper.py").read_text(encoding="utf-8")
    assert "retry CPU" not in text
    assert "CPU in parent" not in text


def test_vieneu_does_not_switch_to_cpu_on_bad_cudnn() -> None:
    text = Path("backend/pipeline/tts/engines/vieneu.py").read_text(encoding="utf-8")
    assert "chuyển ONNX/CPU" not in text
    assert "giữ CUDA" in text
