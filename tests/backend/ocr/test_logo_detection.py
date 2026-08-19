import numpy as np

from pipeline.ocr.logo import _logo_candidates, pick_logo_detection


def _item(box, sample, confidence=0.9, text="LOGO"):
    return {
        "box": box,
        "sample": sample,
        "confidence": confidence,
        "text": text,
    }


def test_logo_probe_times_short_clip_is_sparse():
    from pipeline.ocr.logo import _logo_probe_times

    times = _logo_probe_times(20.0)
    assert 3 <= len(times) <= 8
    assert times[0] < 1.0
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


def test_bilibili_corner_watermark_is_branding():
    from pipeline.ocr.logo import _branding_text, _moving_branding_tracks

    assert _branding_text("哔哩哔哩2313")
    assert _branding_text("叽咕叽咕2313")
    assert not _branding_text("哥哥的许诺而承忘不了")
    samples = [[_item((820, 980, 1060, 1040), 0, text="哔哩哔哩2313")], [], []]
    tracks = _moving_branding_tracks(samples, [0.0, 0.5, 1.0], 1080, 1080)
    assert tracks
    assert "哔哩" in tracks[0]["text"] or "2313" in tracks[0]["text"]
    from pipeline.ocr.logo import _full_clip_branding_tracks

    full = _full_clip_branding_tracks(samples, 1080, 1080, 20.0)
    assert full and full[0]["start"] == 0.0 and full[0]["end"] == 20.0
    from pipeline.ocr.logo import _moving_branding_tracks

    samples = [
        [_item((900, 20, 1040, 70), 0, text="AI生成+")],
        [],
        [],
    ]
    tracks = _moving_branding_tracks(samples, [0.0, 0.5, 1.0], 1080, 1920)
    assert tracks
    assert "生成" in tracks[0]["text"]
    static = pick_logo_detection(samples, 1080, 1920)
    assert static is not None
    assert "生成" in (static.get("text") or "")


def test_rejects_transient_logo():
    samples = [[_item((20, 20, 120, 70), 0)], [], []]
    assert pick_logo_detection(samples, 1080, 1920) is None


def test_candidate_scan_keeps_ai_generated_off_edge():
    frame = np.zeros((1000, 1000, 3), np.uint8)

    def fake_ocr(_frame):
        return [
            ([[400, 40], [620, 40], [620, 90], [400, 90]], "AI生成+", 0.92),
        ], None

    found = _logo_candidates(frame, fake_ocr, 0)
    assert [item["text"] for item in found] == ["AI生成+"]


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
