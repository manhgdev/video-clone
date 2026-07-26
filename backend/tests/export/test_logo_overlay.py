import numpy as np
from PIL import Image

import pytest

pytest.importorskip("fastapi", reason="Python hệ thống thiếu fastapi — test API chạy trong venv backend")

from api.deps import TextOverlayIn
from api.routes.overlays import _validate_logo
from pipeline.orchestrate.export_overlays import _logo_schedule
from pipeline.export.burn_parts.layout_text import _blit_overlay, _image_overlay


def _logo(**patch):
    data = dict(id="logo", start=0, end=10, text="LOGO", x=10, y=10, w=100, h=50,
                fontSize=42, color="#fff", kind="logo", logoSource="text",
                scope="full", motion="random", visibleSec=4, hiddenSec=2, fadeSec=.5)
    data.update(patch)
    return TextOverlayIn(**data)


def test_logo_schema_accepts_text_and_keyframes():
    logo = _logo(fontFamily="impact", positionKeyframes=[{"at": 0, "x": 10, "y": 10}])
    _validate_logo(logo)
    assert logo.fontFamily == "impact"
    assert logo.positionKeyframes[0]["x"] == 10


def test_fixed_logo_exports_for_whole_scope_without_random_fades():
    assert _logo_schedule({"motion": "fixed", "visibleSec": 1, "hiddenSec": 9}, 0, 10, 20, 30) == [
        (0, 10, 20, 30, 0.0)
    ]


def test_image_logo_keeps_alpha_and_global_opacity(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(path)
    overlay = _image_overlay(str(path), (5, 5, 45, 25))
    assert overlay is not None
    frame = np.zeros((30, 50, 3), dtype=np.uint8)
    _blit_overlay(frame, overlay, .5)
    assert 120 <= int(frame[15, 25, 2]) <= 130
    assert int(frame[15, 25, 0]) == 0
