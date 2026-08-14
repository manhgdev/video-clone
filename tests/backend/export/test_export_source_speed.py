"""Xuất phải dùng file ĐÚNG tốc độ đang hiển thị ở preview.

Bug thật: «ưu tiên chậm 0.8» → phân tích → nâng timeline về 1× → preview 1×
nhưng xuất vẫn ra 0.8, vì export_source_video có fallback cứng
`preview_{N}_s080.mp4` chạy cả khi bakedSpeed = 1.0.
"""
import json
import shutil

import pytest

from pipeline.core.config import PUBLIC_DATA
from pipeline.core.project import ensure_layout
from pipeline.export.source_video import export_source_video


@pytest.fixture
def project(tmp_path_factory):
    pid = "zz_export_speed_test"
    root = ensure_layout(pid)
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    src = root / "source.mp4"
    for p in (src, cache / "preview_5.mp4", cache / "preview_5_s080.mp4",
              cache / "source_s080.mp4"):
        p.write_bytes(b"x" * 64)
    (root / "meta.json").write_text(json.dumps({"videoPath": str(src)}), encoding="utf-8")
    yield pid, root, src
    shutil.rmtree(root, ignore_errors=True)


def test_after_raising_back_to_1x_export_uses_1x_clip(project):
    """workVideo trỏ source (sau nâng 1×) + cache còn bản 0.8 → phải lấy bản 1×."""
    pid, root, src = project
    meta = {
        "videoPath": str(src),
        "previewSec": 5,
        "bakedSpeed": 1.0,
        "workVideo": str(src),
        "timelineClock": "display",
    }
    got, preview_sec = export_source_video(pid, meta)
    assert preview_sec == 5
    assert got.name == "preview_5.mp4", f"lấy nhầm {got.name}"


def test_work_video_matching_window_wins(project):
    pid, root, src = project
    meta = {
        "videoPath": str(src),
        "previewSec": 5,
        "bakedSpeed": 1.0,
        "workVideo": str(root / "cache" / "preview_5.mp4"),
    }
    got, _ = export_source_video(pid, meta)
    assert got.name == "preview_5.mp4"


def test_still_uses_baked_clip_when_really_slowed(project):
    """Đang thật sự bake 0.8 → phải dùng bản 0.8 (không phá hành vi cũ)."""
    pid, root, src = project
    meta = {
        "videoPath": str(src),
        "previewSec": 5,
        "bakedSpeed": 0.8,
        "bakedPreferVideo": True,
        "workVideo": str(src),
    }
    got, _ = export_source_video(pid, meta)
    assert got.name == "preview_5_s080.mp4"


def test_full_window_1x_ignores_stale_slow_source(project):
    """previewSec=0, đã nâng 1× → không được lấy source_s080.mp4 còn sót."""
    pid, root, src = project
    meta = {
        "videoPath": str(src),
        "previewSec": 0,
        "bakedSpeed": 1.0,
        "workVideo": str(src),
    }
    got, preview_sec = export_source_video(pid, meta)
    assert preview_sec == 0
    assert got.name == "source.mp4", f"lấy nhầm {got.name}"


def test_full_window_uses_baked_source_when_slowed(project):
    """Sau bake thật, workVideo trỏ thẳng file s080 — export phải theo nó."""
    pid, root, src = project
    meta = {
        "videoPath": str(src),
        "previewSec": 0,
        "bakedSpeed": 0.8,
        "bakedPreferVideo": True,
        "workVideo": str(root / "cache" / "source_s080.mp4"),
    }
    got, _ = export_source_video(pid, meta)
    assert got.name == "source_s080.mp4"
