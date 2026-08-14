from pathlib import Path

import pytest


def test_reference_wav_loads_when_torchaudio_requires_torchcodec(monkeypatch) -> None:
    torchaudio = pytest.importorskip("torchaudio")
    pytest.importorskip("soundfile")
    from pipeline.tts.engines import vieneu

    def requires_torchcodec(*_args, **_kwargs):
        raise ImportError("TorchCodec is required for load_with_torchcodec")

    monkeypatch.setattr(torchaudio, "load", requires_torchcodec)
    vieneu._enable_torchaudio_soundfile_fallback()

    wav, sample_rate = torchaudio.load(
        Path("backend/resources/voice-ref/adam-low-tone.wav")
    )
    assert sample_rate == 24_000
    assert tuple(wav.shape[:1]) == (1,)
    assert wav.shape[1] > 0
