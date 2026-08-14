from pipeline.core.media import preview_clip_matches


def test_preview_clip_matches_exact_window_only():
    assert preview_clip_matches("preview_5.mp4", 5)
    assert preview_clip_matches("preview_5_s080.mp4", 5)
    assert preview_clip_matches("Preview_20_S115.MP4", 20)
    # substring cũ match nhầm cửa sổ khác — phải từ chối
    assert not preview_clip_matches("preview_50.mp4", 5)
    assert not preview_clip_matches("preview_55_s080.mp4", 5)
    assert not preview_clip_matches("preview_5.tmp.mp4", 5)
    assert not preview_clip_matches("source_s080.mp4", 5)
