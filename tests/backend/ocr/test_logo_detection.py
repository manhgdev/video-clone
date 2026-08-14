import numpy as np

from pipeline.ocr.logo import _logo_candidates, pick_logo_detection


def _item(box, sample, confidence=0.9, text="LOGO"):
    return {
        "box": box,
        "sample": sample,
        "confidence": confidence,
        "text": text,
    }


def test_picks_one_persistent_corner_logo():
    samples = [
        [_item((20, 20, 120, 70), 0), _item((400, 300, 700, 380), 0)],
        [_item((22, 21, 122, 71), 1)],
        [_item((19, 20, 119, 70), 2)],
    ]
    detection = pick_logo_detection(samples, 1080, 1920)

    assert detection is not None
    assert detection["samples"] == 3
    assert detection["bbox"]["x"] < 0.03
    assert detection["bbox"]["y"] < 0.02


def test_rejects_transient_logo():
    samples = [[_item((20, 20, 120, 70), 0)], [], []]
    assert pick_logo_detection(samples, 1080, 1920) is None


def test_candidate_scan_rejects_center_text():
    frame = np.zeros((1000, 1000, 3), np.uint8)

    def fake_ocr(_frame):
        return [
            ([[20, 20], [120, 20], [120, 70], [20, 70]], "LOGO", 0.95),
            ([[350, 450], [650, 450], [650, 520], [350, 520]], "SUBTITLE", 0.99),
        ], None

    found = _logo_candidates(frame, fake_ocr, 0)
    assert [item["text"] for item in found] == ["LOGO"]


def test_candidate_scan_rejects_spoken_source():
    frame = np.zeros((1000, 1000, 3), np.uint8)

    def fake_ocr(_frame):
        return [
            ([[20, 20], [120, 20], [120, 70], [20, 70]], "字幕内容", 0.95),
        ], None

    assert _logo_candidates(frame, fake_ocr, 0, {"字幕内容"}) == []
