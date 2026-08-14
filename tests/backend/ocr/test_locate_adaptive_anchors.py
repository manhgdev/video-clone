from pipeline.ocr.locate import (
    _QUICK_PROBE_LIMIT,
    _initial_anchor_indices,
    _lane_region_from_box,
    _refinement_indices,
)


def _segments(count: int, duration: float) -> list[dict]:
    step = duration / count
    return [
        {"start": i * step, "end": (i + 1) * step, "source": "字幕"}
        for i in range(count)
    ]


def test_short_video_starts_with_three_anchors() -> None:
    assert _initial_anchor_indices(_segments(21, 300), 300) == [0, 10, 20]


def test_five_hour_video_starts_with_five_anchors() -> None:
    assert _initial_anchor_indices(_segments(21, 5 * 3600), 5 * 3600) == [
        0,
        5,
        10,
        15,
        20,
    ]


def test_stable_lane_needs_no_more_ocr() -> None:
    boxes = {
        0: (300, 900, 700, 980),
        10: (280, 905, 720, 985),
        20: (310, 895, 690, 975),
    }
    assert _refinement_indices(boxes, 21, 1080, 1920) == []


def test_changed_lane_is_bisected_and_budget_is_bounded() -> None:
    boxes = {
        0: (300, 900, 700, 980),
        20: (300, 1450, 700, 1530),
    }
    assert _refinement_indices(boxes, 21, 1080, 1920) == [10]
    assert _QUICK_PROBE_LIMIT == 16


def test_missed_anchor_retries_nearest_cue() -> None:
    boxes = {0: (300, 900, 700, 980), 10: None, 20: (300, 900, 700, 980)}
    assert _refinement_indices(boxes, 21, 1080, 1920)[0] == 9


def test_decode_failure_does_not_spawn_more_anchors() -> None:
    boxes = {0: (300, 900, 700, 980), 10: None, 20: (300, 900, 700, 980)}
    assert _refinement_indices(
        boxes, 21, 1080, 1920, decode_failed={10}
    ) == []


def test_lane_region_is_narrow_and_respects_user_limit() -> None:
    region = _lane_region_from_box(
        (300, 1400, 700, 1480),
        1080,
        1920,
        (0.1, 0.5, 0.8, 0.4),
    )
    assert region["x"] == 0.1
    assert region["w"] == 0.8
    assert 0.1 < region["h"] < 0.2
    assert region["y"] >= 0.5
