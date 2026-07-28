from pipeline.orchestrate.dub import (
    _PREFER_VIDEO_DEFAULT_TTS_SPEED,
    _segment_playback,
)


def test_segment_playback_uses_editor_speed_and_percent_volume() -> None:
    assert _segment_playback({"ttsSpeed": 1.3, "ttsVolume": 75}) == (1.3, 0.75)


def test_segment_playback_defaults_and_clamps_bad_values() -> None:
    assert _segment_playback({}) == (1.0, 1.0)
    assert _segment_playback({"ttsSpeed": 9, "ttsVolume": -20}) == (2.0, 0.0)
    assert _segment_playback({"ttsSpeed": "bad", "ttsVolume": "bad"}) == (1.0, 1.0)


def test_prefer_video_default_tts_speed_is_not_too_fast() -> None:
    assert _PREFER_VIDEO_DEFAULT_TTS_SPEED == 1.1
