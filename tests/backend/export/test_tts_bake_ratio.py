from pipeline.export.mux_audio import _tts_bake_ratio


def test_tts_natural_on_fit_clock():
    # Dub sau khi đã bake 0.8 → phát tự nhiên (không nhân 0.8 lần nữa)
    assert _tts_bake_ratio(0.8, 0.8) == 1.0
    # Dub ở 0.8 rồi nâng timeline 1× → TTS nhanh 1.25 khớp cửa sổ co lại
    assert abs(_tts_bake_ratio(1.0, 0.8) - 1.25) < 1e-9


def test_legacy_segments_keep_old_behavior():
    # Segment cũ không có ttsBake (dub thời 1×) → ratio = bake như trước
    assert _tts_bake_ratio(0.8, None) == 0.8
    assert _tts_bake_ratio(1.15, 0) == 1.15
    assert _tts_bake_ratio(1.0, "x") == 1.0
