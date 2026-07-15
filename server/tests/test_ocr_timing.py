"""OCR locate: cover defaults after 3-point bbox probe."""


def test_report_throttle_every_four() -> None:
    prev = -1
    reported = []
    total = 67
    for done in range(1, total + 1):
        if done == prev:
            continue
        if done not in (1, total) and done % 4 != 0:
            continue
        prev = done
        reported.append(done)
    assert reported[0] == 1
    assert reported[-1] == total
    assert 4 in reported and 8 in reported
    assert 2 not in reported and 3 not in reported


def test_ensure_cover_times_skips_existing() -> None:
    from pipeline.ocr.locate import _ensure_cover_times

    segs = [
        {
            "start": 1.0,
            "end": 2.0,
            "source": "你好",
            "layout": "mid",
            "coverStart": 0.5,
            "coverEnd": 3.0,
        },
        {"start": 3.0, "end": 4.0, "source": "世界", "layout": "mid"},
    ]
    _ensure_cover_times(segs, video_end=120.0)
    assert float(segs[0]["coverStart"]) == 0.5
    assert segs[1].get("coverStart") is not None


def test_ensure_cover_times_sets_fields() -> None:
    from pipeline.ocr.locate import _ensure_cover_times

    segs = [
        {"start": 1.0, "end": 2.0, "source": "你好", "layout": "mid"},
        {"start": 3.0, "end": 4.0, "source": "世界", "layout": "mid"},
    ]
    _ensure_cover_times(segs, video_end=120.0)
    for seg in segs:
        assert seg.get("coverStart") is not None
        assert seg.get("coverEnd") is not None
        assert float(seg["coverEnd"]) > float(seg["coverStart"])


def test_three_point_probe_times() -> None:
    """Mỗi mốc đại diện chỉ OCR ở giữa cue."""
    s0, e0 = 10.0, 12.0
    assert s0 + (e0 - s0) * 0.5 == 11.0


def test_whisper_locate_uses_only_video_start_middle_end() -> None:
    from pipeline.ocr.locate import _three_point_segments

    segments = [{"id": i} for i in range(54)]
    assert [segment["id"] for segment in _three_point_segments(segments)] == [
        0,
        27,
        53,
    ]


def test_whisper_locate_keeps_all_when_three_or_fewer() -> None:
    from pipeline.ocr.locate import _three_point_segments

    segments = [{"id": i} for i in range(3)]
    assert _three_point_segments(segments) == segments
