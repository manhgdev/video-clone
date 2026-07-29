from pipeline.tts.capcut import _normalize_tts_text


def test_capcut_normalizes_joined_laughter() -> None:
    assert _normalize_tts_text("Hahahaha") == "Ha-ha-ha-ha"
    assert _normalize_tts_text("HAHAHA!") == "Ha-ha-ha"
    assert _normalize_tts_text("Ha ha ha") == "Ha-ha-ha"


def test_capcut_keeps_normal_speech_and_cleans_whitespace() -> None:
    assert _normalize_tts_text("  Xin lỗi\n con.  ") == "Xin lỗi con."
