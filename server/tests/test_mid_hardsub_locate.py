"""Self-check: mid OCR prefers full CJK line, rejects partial L/R chunks."""
from __future__ import annotations

from pipeline.ocr.locate import _bbox_fits_source, _cjk_len


def test_bbox_fits_source_rejects_narrow() -> None:
    assert _bbox_fits_source({"w": 600, "h": 100}, "咱們拿回家做一點", 1080)
    assert not _bbox_fits_source({"w": 120, "h": 100}, "咱們拿回家做一點", 1080)
    assert not _bbox_fits_source({"w": 542, "h": 100}, "最奢侈", 1080)
    assert _bbox_fits_source({"w": 80, "h": 40}, "最", 1080)


def test_retag_mid_from_bbox_cy() -> None:
    from pipeline.ocr.locate import _layout_from_cy, _retag_layout_from_bbox

    assert _layout_from_cy(1210, 1920) == "mid"
    assert _layout_from_cy(1700, 1920) == "horizontal"
    seg: dict = {"bbox": {"x": 266, "y": 1152, "w": 582, "h": 125}, "layout": None}
    _retag_layout_from_bbox(seg, 1920)
    assert seg["layout"] == "mid"
    seg2 = {"bbox": {"x": 266, "y": 1152, "w": 582, "h": 125}, "layout": "horizontal"}
    _retag_layout_from_bbox(seg2, 1920)
    assert seg2["layout"] == "mid"


def test_cjk_len() -> None:
    assert _cjk_len("咱們拿回家做一點") == 8
    assert _cjk_len("hello") == 0


if __name__ == "__main__":
    test_bbox_fits_source_rejects_narrow()
    test_retag_mid_from_bbox_cy()
    test_cjk_len()
    print("ok")
