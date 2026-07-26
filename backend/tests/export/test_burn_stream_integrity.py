from pipeline.export.burn_parts.pipeline import (
    _burn_frame_count_complete,
    _burn_output_complete,
)


def test_burn_frame_count_allows_decoder_rounding_only() -> None:
    fps = 30.0
    assert _burn_frame_count_complete(300, 300, fps)
    assert _burn_frame_count_complete(285, 300, fps)
    assert not _burn_frame_count_complete(1, 300, fps)


def test_burn_frame_count_allows_long_video_opencv_overcount() -> None:
    # Real fail: 15120/15216 @ ~22.45fps but duration full — OpenCV overcount.
    fps = 22.45
    expected = 15216
    written = 15120
    # ~0.75% / 0.75s tolerance should cover 96 frames on long clips
    assert _burn_frame_count_complete(written, expected, fps)


def test_burn_output_accepts_full_duration_despite_frame_gap() -> None:
    assert _burn_output_complete(
        written=15120,
        expected_frames=15216,
        fps=22.45,
        output_duration=677.883,
        expected_duration=677.867,
    )


def test_burn_output_rejects_short_duration() -> None:
    assert not _burn_output_complete(
        written=10000,
        expected_frames=15216,
        fps=22.45,
        output_duration=400.0,
        expected_duration=677.867,
    )
