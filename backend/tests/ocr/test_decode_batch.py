"""Giải mã frame cho Định vị OCR: phải chia lô, không rơi về OpenCV seek.

Bug thật: select expr với >~100 nhánh eq() làm ffmpeg lỗi parse → trả 0 frame →
chế độ «ổn định đầu•giữa•cuối» (3 mốc/câu) rơi hết về OpenCV seek, ăn ~8/12 core.
"""
import subprocess
from pathlib import Path

import pytest

from pipeline.ocr.locate import _decode_frames_batch

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
    assert [i for i, _f in got] == wanted
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
