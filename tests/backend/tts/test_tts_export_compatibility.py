import shutil
import wave

import pytest

from pipeline.tts import studio


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for audio compatibility export")
def test_tts_exports_are_quicktime_safe_pcm_wav_and_mp3(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(studio, "TTS_OUTPUT", tmp_path)
    job = tmp_path / "quicktime"
    job.mkdir()
    source = job / "audio.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24_000)
        out.writeframes(b"\0\0" * 24_000)

    wav = studio.ensure_wav("quicktime")
    mp3 = studio.ensure_mp3("quicktime")

    with wave.open(str(wav), "rb") as out:
        assert (out.getnchannels(), out.getsampwidth(), out.getframerate()) == (1, 2, 48_000)
    assert mp3.read_bytes()[:3] == b"ID3"
