"""Check attach_speech_hardsub_boxes finds mid covers on whisper preview."""
from pathlib import Path

from pipeline.ocr.locate import attach_speech_hardsub_boxes


def test_attach_speech_boxes_on_preview_mid() -> None:
    root = Path(__file__).resolve().parents[1]
    video = root / "data" / "b5080a04dacc" / "cache" / "preview_10.mp4"
    if not video.is_file():
        return  # skip if fixture absent
    segs = [
        {"id": "1", "start": 0.0, "end": 2.0, "source": "我從山上帶下來一棵樹析", "translation": "x"},
        {"id": "2", "start": 3.0, "end": 4.5, "source": "咱們拿回家做一點", "translation": "y"},
        {"id": "5", "start": 6.0, "end": 7.0, "source": "帶在頭上", "translation": "z"},
    ]
    n = attach_speech_hardsub_boxes(video, segs, only_missing=True)
    assert n >= 2
    for s in segs:
        bb = s.get("bbox")
        assert isinstance(bb, dict), s
        cy = (bb["y"] + bb["h"] / 2) / 1920
        assert 0.18 < cy < 0.70, (s["source"], cy, bb)
        assert s.get("layout") == "mid"
