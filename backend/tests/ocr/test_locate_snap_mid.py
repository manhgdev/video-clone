"""CAP-MID không bị snap Y xuống dải hardsub đáy."""
from __future__ import annotations

from pipeline.ocr.locate import _apply_caption_box, _snap_inherited_y


def test_snap_skips_mid_and_non_bottom_anchor():
    fh = 1920
    segs = [
        {
            "layout": "mid",
            "bboxInherited": True,
            "bbox": {"x": 300, "y": 1100, "w": 400, "h": 80},
            "source": "还有竹子",
        },
        {
            "layout": "horizontal",
            "bboxInherited": True,
            "bbox": {"x": 100, "y": 1600, "w": 800, "h": 60},
            "source": "底部字幕",
        },
    ]
    # anchor mid-ish — không snap gì
    _snap_inherited_y(segs, fh * 0.55, fh)
    assert segs[0]["bbox"]["y"] == 1100
    assert segs[1]["bbox"]["y"] == 1600

    # anchor đáy — mid giữ, horizontal inherit có thể neo
    segs[0]["bbox"]["y"] = 1100
    segs[1]["bbox"]["y"] = 1500
    _snap_inherited_y(segs, fh * 0.88, fh)
    assert segs[0]["bbox"]["y"] == 1100
    assert segs[0]["layout"] == "mid"


def test_apply_caption_box_mid_not_tall():
    fw, fh = 1080, 1920
    seg: dict = {"source": "还有竹子"}
    # OCR poly cao bất thường
    _apply_caption_box(seg, (300, 1000, 700, 1200), fw, fh)
    assert seg["layout"] == "mid"
    assert seg["bboxInherited"] is True
    assert seg["bbox"]["h"] <= 130
    assert seg["bbox"]["y"] > 900
