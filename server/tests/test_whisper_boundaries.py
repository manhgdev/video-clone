from types import SimpleNamespace

from pipeline.asr import asr_whisper
from pipeline.core.project import asr_cache_key


class _FakeWhisper:
    def __init__(self) -> None:
        self.kwargs = {}

    def transcribe(self, _path: str, **kwargs):
        self.kwargs = kwargs
        segments = iter(
            [
                SimpleNamespace(start=0.0, end=1.0, text="第一句"),
                SimpleNamespace(start=1.0, end=2.0, text="第二句"),
            ]
        )
        return segments, SimpleNamespace()


def test_whisper_keeps_model_segment_boundaries(tmp_path, monkeypatch) -> None:
    model = _FakeWhisper()
    monkeypatch.setattr("pipeline.asr.get_whisper", lambda _workers: model)

    result = asr_whisper(tmp_path / "audio.wav", "auto", workers=1)

    assert [segment["source"] for segment in result] == ["第一句", "第二句"]
    assert [(segment["start"], segment["end"]) for segment in result] == [
        (0.0, 1.0),
        (1.0, 2.0),
    ]
    assert model.kwargs["vad_parameters"]["min_silence_duration_ms"] == 400


def test_whisper_cache_version_invalidates_old_merged_results() -> None:
    key = asr_cache_key(
        {
            "engine": "whisper",
            "sourceLang": "auto",
            "previewSec": 10,
            "matchDuration": "preferVideo",
        },
        "source-fingerprint",
    )

    assert "|a2|" in key
