"""P1: ffmpeg vẽ mask+chữ phải cho kết quả tương đương đường Python cũ.

Parity gate của PLAN: cùng bộ cue đã chuẩn bị, hai đường render ra video
có (1) chữ đặt đúng chỗ, (2) vùng mask thật sự bị che, (3) thời lượng đủ.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pipeline.export.burn import cover_and_burn

pytestmark = pytest.mark.skipif(
    subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode != 0,
    reason="ffmpeg không có sẵn",
)

W, H = 640, 360


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ffg") / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=25:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", str(out)],
        check=True, capture_output=True,
    )
    return out


def _segments() -> list[dict]:
    return [
        {
            "id": "cap1", "start": 1.0, "end": 3.0,
            "source": "来这一小包玉米", "translation": "Xin chào thế giới",
            "layout": "horizontal",
            "bbox": {"x": 60, "y": 280, "w": 520, "h": 50},
        },
        {
            "id": "fx1", "start": 4.0, "end": 6.0,
            "source": "", "translation": "", "layout": "horizontal",
            "maskOnly": True, "coverMaskStyle": "solid",
            "coverMaskColor": "#102030", "coverMaskOpacity": 90,
            "bbox": {"x": 100, "y": 60, "w": 200, "h": 80},
        },
    ]


def _grab(video: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw[: W * H * 3], np.uint8).reshape((H, W, 3)).astype(np.int16)


def _render(clip: Path, out: Path, legacy: bool) -> Path:
    env_key = "VIDEO_CLONE_LEGACY_BURN"
    old = os.environ.get(env_key)
    os.environ[env_key] = "1" if legacy else "0"
    try:
        cover_and_burn(clip, _segments(), out, cover=True, burn=True,
                       project_id=None, workers=2)
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old
    return out


@pytest.fixture(scope="module")
def rendered(clip, tmp_path_factory):
    d = tmp_path_factory.mktemp("out")
    ff = _render(clip, d / "ff.mp4", legacy=False)
    legacy = _render(clip, d / "legacy.mp4", legacy=True)
    return clip, ff, legacy


def test_duration_full(rendered):
    from pipeline.core.media import ffprobe_duration

    clip, ff, legacy = rendered
    src = ffprobe_duration(clip)
    assert abs(ffprobe_duration(ff) - src) < 0.5
    assert abs(ffprobe_duration(legacy) - src) < 0.5


def test_caption_drawn_same_place(rendered):
    """Giữa cue chữ: cả hai đường phải vẽ chữ trong bbox, khác hẳn nguồn."""
    clip, ff, legacy = rendered
    t = 2.0
    src = _grab(clip, t)
    a = _grab(ff, t)
    b = _grab(legacy, t)
    y0, y1, x0, x1 = 280, 330, 60, 580
    src_r, a_r, b_r = (im[y0:y1, x0:x1] for im in (src, a, b))
    # cả hai khác nguồn rõ rệt (mask+chữ đã đè)
    assert np.abs(a_r - src_r).mean() > 8, "ffgraph không vẽ gì lên vùng caption"
    assert np.abs(b_r - src_r).mean() > 8, "legacy không vẽ gì lên vùng caption"
    # hai đường gần nhau (codec + khác biệt blur cho phép lệch trung bình nhỏ)
    assert np.abs(a_r - b_r).mean() < 40, f"lệch {np.abs(a_r - b_r).mean():.1f}"


def test_solid_mask_region_covered(rendered):
    clip, ff, legacy = rendered
    t = 5.0
    src = _grab(clip, t)
    a = _grab(ff, t)
    b = _grab(legacy, t)
    y0, y1, x0, x1 = 60, 140, 100, 300
    # solid #102030 @90% — vùng phải tối và đồng màu ở CẢ hai đường
    for name, im in (("ffgraph", a), ("legacy", b)):
        region = im[y0:y1, x0:x1]
        assert np.abs(region - src[y0:y1, x0:x1]).mean() > 15, f"{name} không che"
        assert region.std() < src[y0:y1, x0:x1].std(), f"{name} vùng che còn texture gốc"
    assert np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean() < 25


def test_outside_regions_untouched(rendered):
    """Ngoài cửa sổ cue: khung phải giống video nguồn (không vẽ nhầm)."""
    clip, ff, _legacy = rendered
    t = 7.5  # sau mọi cue
    src = _grab(clip, t)
    a = _grab(ff, t)
    assert np.abs(a - src).mean() < 6, "ffgraph làm biến dạng khung ngoài cue"


@pytest.fixture(scope="module")
def gop_clip(tmp_path_factory) -> Path:
    """Nguồn keyframe mỗi 1s — để đường segmented (P2) thực sự kích hoạt."""
    out = tmp_path_factory.mktemp("ffg2") / "gop.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=25:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "25", "-keyint_min", "25", "-sc_threshold", "0",
         "-c:a", "aac", str(out)],
        check=True, capture_output=True,
    )
    return out


def _frames_of(video: Path) -> int:
    return int(subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True).stdout.strip())


def test_segmented_path_exact_frames_and_untouched_gaps(gop_clip, tmp_path):
    """P2: cue chỉ ở [1..3] của video 20s → encode 1 đoạn, copy phần còn lại.

    Yêu cầu: đủ TỪNG khung (không rơi/lặp ở mối nối), đoạn trống giữ nguyên
    bit-identical với nguồn (copy thật, không re-encode), chữ vẫn được vẽ.
    """
    from pipeline.core.media import ffprobe_duration

    out = tmp_path / "seg.mp4"
    segs = [{
        "id": "cap", "start": 1.0, "end": 3.0,
        "source": "来这一小包玉米", "translation": "Xin chào",
        "layout": "horizontal", "bbox": {"x": 60, "y": 280, "w": 520, "h": 50},
    }]
    cover_and_burn(gop_clip, segs, out, cover=True, burn=True,
                   project_id=None, workers=2)
    assert _frames_of(out) == _frames_of(gop_clip), "rơi/lặp khung ở mối nối"
    assert abs(ffprobe_duration(out) - ffprobe_duration(gop_clip)) < 0.2
    # decode sạch toàn bộ
    chk = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"],
                         capture_output=True, text=True)
    assert chk.returncode == 0 and not chk.stderr.strip(), chk.stderr[:200]
    # giữa cue: chữ được vẽ; xa cue (t=15): giống nguồn gần như tuyệt đối (copy)
    src_mid, out_mid = _grab(gop_clip, 2.0), _grab(out, 2.0)
    assert np.abs(out_mid[280:330, 60:580] - src_mid[280:330, 60:580]).mean() > 8
    src_far, out_far = _grab(gop_clip, 15.0), _grab(out, 15.0)
    assert np.abs(out_far - src_far).mean() < 1.5, "đoạn trống bị re-encode?"
