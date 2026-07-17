from types import SimpleNamespace

from pipeline.asr import _tighten_bounds, asr_whisper
from pipeline.core.project import asr_cache_key


class _FakeWhisper:
    def __init__(self) -> None:
        self.kwargs = {}

    def transcribe(self, _path: str, **kwargs):
        self.kwargs = kwargs
        # Một câu + silence dài sau — siết end, KHÔNG tách chữ
        segments = iter(
            [
                SimpleNamespace(
                    start=0.0,
                    end=25.0,
                    text="第一句完整",
                    words=[
                        SimpleNamespace(start=0.1, end=0.4, word="第", probability=0.9),
                        SimpleNamespace(start=0.4, end=0.7, word="一", probability=0.9),
                        # gap lớn giữa ký tự — trước đây tách sai
                        SimpleNamespace(start=2.0, end=2.3, word="句", probability=0.9),
                        SimpleNamespace(start=2.3, end=2.6, word="完", probability=0.9),
                        SimpleNamespace(start=2.6, end=3.0, word="整", probability=0.9),
                    ],
                ),
                SimpleNamespace(
                    start=5.0,
                    end=6.0,
                    text="第二句",
                    words=[
                        SimpleNamespace(start=5.1, end=5.5, word="第", probability=0.9),
                        SimpleNamespace(start=5.5, end=5.9, word="二句", probability=0.9),
                    ],
                ),
            ]
        )
        return segments, SimpleNamespace()


def test_whisper_keeps_full_sentence_text(tmp_path, monkeypatch) -> None:
    model = _FakeWhisper()
    monkeypatch.setattr("pipeline.asr.get_whisper", lambda _workers: model)

    result = asr_whisper(tmp_path / "audio.wav", "auto", workers=1)

    assert model.kwargs.get("word_timestamps") is True
    # Đúng 2 segment Whisper → 2 row, không tách gap
    assert len(result) == 2
    assert result[0]["source"] == "第一句完整"
    assert result[1]["source"] == "第二句"
    # Biên siết: không còn end=25
    assert result[0]["end"] < 4.0
    assert result[0]["start"] < 0.3


def test_tighten_bounds_single_window() -> None:
    parts = [
        (1.0, 1.3, "A", 0.9),
        (1.3, 1.6, "B", 0.9),
        (4.0, 4.4, "C", 0.9),  # gap lớn — vẫn 1 cửa sổ
    ]
    s, e = _tighten_bounds(0.0, 20.0, parts)
    assert s < 1.2
    assert e > 4.3
    assert e - s < 5.0


def test_whisper_cache_a15() -> None:
    key = asr_cache_key(
        {
            "engine": "whisper",
            "sourceLang": "auto",
            "previewSec": 10,
            "matchDuration": "preferVideo",
        },
        "source-fingerprint",
    )
    assert "|a15|" in key
