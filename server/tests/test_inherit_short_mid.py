"""Inherit mid bbox from three OCR anchors without copying an oversized width."""
from pipeline.ocr.locate import _inherit_caption_bboxes


def test_inherit_scales_wide_donor_for_short_cjk() -> None:
    segs = [
        {
            "id": "3",
            "start": 4.0,
            "end": 6.0,
            "source": "咱們拿回家做一點",
            "layout": "mid",
            "bbox": {"x": 269, "y": 1161, "w": 545, "h": 102},
        },
        {
            "id": "4",
            "start": 5.76,
            "end": 6.6,
            "source": "最奢侈",
            "layout": None,
            "bbox": None,
        },
        {
            "id": "5",
            "start": 6.88,
            "end": 7.98,
            "source": "最豪華的奔官",
            "layout": "mid",
            "bbox": {"x": 240, "y": 1162, "w": 600, "h": 98},
        },
    ]
    n = _inherit_caption_bboxes(segs, 1920)
    assert n == 1
    s4 = segs[1]
    assert s4["layout"] == "mid"
    bb = s4["bbox"]
    assert isinstance(bb, dict)
    assert 48 <= bb["w"] < 545, bb
    assert bb["x"] > 269
    assert s4["bboxInherited"] is True
    assert bb["y"] == 1161
