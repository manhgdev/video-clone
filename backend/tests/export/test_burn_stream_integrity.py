from pipeline.export.burn_parts.pipeline import _burn_frame_count_complete


def test_burn_frame_count_allows_decoder_rounding_only() -> None:
    fps = 30.0
    assert _burn_frame_count_complete(300, 300, fps)
    assert _burn_frame_count_complete(285, 300, fps)
    assert not _burn_frame_count_complete(1, 300, fps)
