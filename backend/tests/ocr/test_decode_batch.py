"""Giải mã frame cho Định vị OCR: phải chia lô, không rơi về OpenCV seek.

Bug thật: select expr với >~100 nhánh eq() làm ffmpeg lỗi parse → trả 0 frame →
chế độ «ổn định đầu•giữa•cuối» (3 mốc/câu) rơi hết về OpenCV seek, ăn ~8/12 core.
"""
import subprocess
import time
from pathlib import Path

import pytest

from pipeline.ocr.locate import _decode_frames_batch, _group_frame_indices

pytestmark = pytest.mark.skipif(
    subprocess.run(
        ["ffmpeg", "-version"], capture_output=True
    ).returncode != 0,
    reason="ffmpeg không có sẵn",
)


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dec") / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=25:duration=8",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_decodes_all_marks_even_when_many(clip: Path):
    """160 mốc > giới hạn parser của ffmpeg — phải chia lô, không mất frame."""
    times = [i * (8.0 / 160) for i in range(160)]
    got = list(_decode_frames_batch(clip, times, 25.0, 320, 240, use_cuda=False))
    wanted = sorted({max(0, round(t * 25.0)) for t in times})
    assert len(got) == len(wanted), f"{len(got)}/{len(wanted)} frame"
    # Decoder song song trả frame nào xong trước để GPU OCR ngay.
    assert sorted(i for i, _f in got) == wanted
    for _i, frame in got:
        assert frame.shape == (240, 320, 3)


def test_small_set_still_works(clip: Path):
    times = [0.5, 1.5, 2.5]
    got = list(_decode_frames_batch(clip, times, 25.0, 320, 240, use_cuda=False))
    assert len(got) == 3


def test_bad_input_is_silent(tmp_path: Path):
    missing = tmp_path / "khong-co.mp4"
    assert list(_decode_frames_batch(missing, [1.0], 25.0, 320, 240, use_cuda=False)) == []
    # tham số vô lý → không nổ, trả rỗng để caller fallback
    assert list(_decode_frames_batch(missing, [], 25.0, 320, 240, use_cuda=False)) == []
    assert list(_decode_frames_batch(missing, [1.0], 0, 320, 240, use_cuda=False)) == []


def test_sparse_long_video_marks_seek_independently():
    fps = 25.0
    marks = [0, round(3600 * fps), round(5 * 3600 * fps)]
    assert _group_frame_indices(marks, fps) == [[marks[0]], [marks[1]], [marks[2]]]


def test_dense_marks_stay_batched():
    assert _group_frame_indices(list(range(100)), 25.0) == [
        list(range(48)),
        list(range(48, 96)),
        list(range(96, 100)),
    ]


def test_one_hung_seek_does_not_block_other_frames(monkeypatch, clip: Path):
    real_popen = subprocess.Popen
    class HungOnce:
        def __init__(self, *args, **kwargs):
            self._real = real_popen(*args, **kwargs)
            self.pid = self._real.pid
            self.returncode = None
            self._hang = "0.480" in args[0]

        def communicate(self, timeout=None):
            if self._hang:
                self._hang = False
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            result = self._real.communicate(timeout=timeout)
            self.returncode = self._real.returncode
            return result

        def poll(self):
            return self._real.poll()

        def kill(self):
            return self._real.kill()

    monkeypatch.setattr("pipeline.ocr.locate.subprocess.Popen", HungOnce)
    started = time.monotonic()
    got = list(
        _decode_frames_batch(
            clip, [0.5, 1.5, 2.5], 25.0, 320, 240,
            use_cuda=False, timeout=0.1,
        )
    )
    assert time.monotonic() - started < 5
    assert len(got) == 2


def test_default_seek_timeout_is_short_enough_to_avoid_a_stalled_job():
    from pipeline.ocr.locate import _FRAME_SEEK_TIMEOUT_SECONDS

    assert _FRAME_SEEK_TIMEOUT_SECONDS <= 5
