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
        whisper.asr_whisper_inprocess(tmp_path / "audio.wav", "auto")

    assert closed
    assert reset


def test_win32_whisper_uses_subprocess(monkeypatch, tmp_path):
    called = {}

    monkeypatch.setattr(whisper.sys, "platform", "win32")
    monkeypatch.setattr(whisper.sys, "frozen", False, raising=False)
    monkeypatch.delenv("VIDEO_CLONE_WHISPER_INPROCESS", raising=False)

    def fake_sub(wav, source_lang, *, workers, project_id):
        called["ok"] = True
        return [{"id": "1"}]

    monkeypatch.setattr(whisper, "_asr_via_runtime_subprocess", fake_sub)
    assert whisper.asr_whisper(tmp_path / "a.wav", "vi") == [{"id": "1"}]
    assert called["ok"] is True


def test_win_ntstatus_heap():
    assert "HEAP" in whisper._win_ntstatus(3221226356)


def test_frozen_whisper_uses_runtime_subprocess(monkeypatch, tmp_path):
    called = {}

    monkeypatch.setattr(whisper.sys, "frozen", True, raising=False)

    def fake_sub(wav, source_lang, *, workers, project_id):
        called["ok"] = (str(wav), source_lang, workers, project_id)
        return [{"id": "1", "source": "hi"}]

    monkeypatch.setattr(whisper, "_asr_via_runtime_subprocess", fake_sub)
    out = whisper.asr_whisper(tmp_path / "a.wav", "vi", workers=3, project_id="p1")
    assert out == [{"id": "1", "source": "hi"}]
    assert called["ok"] == (str(tmp_path / "a.wav"), "vi", 3, "p1")
