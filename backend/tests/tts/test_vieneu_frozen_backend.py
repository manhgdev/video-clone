"""Frozen VieNeu picks backend from runtime venv, not PyInstaller parent."""


def test_frozen_resolve_backend_cuda(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu_frozen as vf

    monkeypatch.delenv("VIENEU_BACKEND", raising=False)
    monkeypatch.setattr(vf, "runtime_torch_cuda_ready", lambda **_: True)
    assert vf.resolve_backend() == ("pytorch", "cuda")


def test_frozen_resolve_backend_cpu(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu_frozen as vf

    monkeypatch.delenv("VIENEU_BACKEND", raising=False)
    monkeypatch.setattr(vf, "runtime_torch_cuda_ready", lambda **_: False)
    assert vf.resolve_backend() == ("onnx", "cpu")


def test_frozen_status_uses_runtime_backend(monkeypatch) -> None:
    from pipeline.tts.engines import vieneu as v

    monkeypatch.setattr(v, "available", lambda: True)
    monkeypatch.setattr(v, "list_preset_from_assets", lambda: ["A"])
    monkeypatch.setattr(v, "package_version", lambda: "3.2.3")
    monkeypatch.setattr(v.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "pipeline.tts.engines.vieneu_frozen.probe",
        lambda: (True, "pytorch"),
    )
    monkeypatch.setattr(
        "pipeline.tts.engines.vieneu_frozen.resolve_backend",
        lambda: ("pytorch", "cuda"),
    )
    st = v.status()
    assert st["device"] == "CUDA (runtime)"
    assert "CUDA" in st["message"]


def test_frozen_synthesize_reference_passes_ref_path(monkeypatch, tmp_path) -> None:
    """zmAI reference voices must work in desktop app — not require npm run server."""
    from pipeline.tts.engines import vieneu as v
    from pipeline.tts import voice_store

    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    out = tmp_path / "out.wav"
    seen: dict = {}

    monkeypatch.setattr(v.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "pipeline.tts.engines.vieneu_frozen.resolve_backend",
        lambda: ("onnx", "cpu"),
    )

    def fake_synth(**kw):
        seen.update(kw)
        kw["out_wav"].write_bytes(b"ok")

    monkeypatch.setattr("pipeline.tts.engines.vieneu_frozen.synthesize", fake_synth)
    monkeypatch.setattr(
        voice_store,
        "get_reference_voice",
        lambda vid: {"id": vid, "ref": "ref.wav"},
    )
    monkeypatch.setattr(voice_store, "reference_path", lambda item: ref)
    monkeypatch.setattr(v, "parse_voice", lambda voice: ("reference", "zmai1"))

    v.synthesize("xin chào", "zmai1", out)

    assert seen.get("clone_ref") == str(ref)
    assert seen.get("voice") == "zmai1"
    assert "npm run server" not in str(seen)
