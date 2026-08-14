import pytest

from pipeline.asr import whisper
from pipeline.core.jobs import Cancelled


def test_whisper_closes_generator_and_unloads_model_on_cancel(monkeypatch, tmp_path):
    closed = False
    reset = False

    def rows():
        nonlocal closed
        try:
            yield object()
        finally:
            closed = True

    class Model:
        def transcribe(self, *_args, **_kwargs):
            return rows(), object()

    def unload():
        nonlocal reset
        reset = True

    monkeypatch.setattr(whisper, "get_whisper", lambda _workers: Model())
    monkeypatch.setattr(whisper, "check_cancel", lambda _project_id: (_ for _ in ()).throw(Cancelled()))
    monkeypatch.setattr(whisper, "reset_whisper", unload)

    with pytest.raises(Cancelled):
        whisper.asr_whisper(tmp_path / "audio.wav", "auto")

    assert closed
    assert reset
