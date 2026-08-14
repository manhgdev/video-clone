import subprocess

import pytest

from pipeline.core.media import ffprobe_duration
from pipeline.tts.audio_utils import fit_duration


pytestmark = pytest.mark.skipif(
    subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode != 0,
    reason="ffmpeg không có sẵn",
)


def _tone(path, seconds: float = 2.0) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            str(path),
        ],
        check=True,
    )


def test_prefer_video_speeds_up_audio_that_still_overflows(tmp_path) -> None:
    wav = tmp_path / "voice.wav"
    _tone(wav)

    fitted = fit_duration(wav, 1.0, "preferVideo")

    assert fitted == pytest.approx(1.0, abs=0.08)
    assert ffprobe_duration(wav) == pytest.approx(1.0, abs=0.08)


def test_none_keeps_original_audio_duration(tmp_path) -> None:
    wav = tmp_path / "voice.wav"
    _tone(wav)

    fitted = fit_duration(wav, 1.0, "none")

    assert fitted == pytest.approx(2.0, abs=0.08)
