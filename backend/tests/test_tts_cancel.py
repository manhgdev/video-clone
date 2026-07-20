import numpy as np
import pytest

from pipeline.tts.engines import vieneu


def test_vieneu_stream_stops_before_saving_when_cancelled(monkeypatch, tmp_path):
    closed = False

    def stream():
        nonlocal closed
        try:
            yield np.zeros(16, dtype=np.float32)
            yield np.zeros(16, dtype=np.float32)
        finally:
            closed = True

    class Client:
        def infer_stream(self, _text, **_kwargs):
            return stream()

        def save(self, _audio, _path):
            raise AssertionError("cancelled audio must not be saved")

    monkeypatch.setattr(vieneu, "parse_voice", lambda _voice: ("preset", "demo"))
    monkeypatch.setattr(vieneu, "get_client", lambda: Client())

    with pytest.raises(RuntimeError, match="Job đã hủy"):
        vieneu.synthesize(
            "Nội dung dài",
            "vn:demo",
            tmp_path / "cancelled.wav",
            cancel_check=lambda: True,
        )

    assert closed
    assert not (tmp_path / "cancelled.wav").exists()
