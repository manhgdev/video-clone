import numpy as np

from pipeline.asr import (
    _merge_horizontal_vertical,
    _ocr_edge_stamps,
    _ocr_labels_from_frame,
)
from pipeline.export.burn import _auto_subtitle_font_size, _cover_box_fit


def test_refine_only_scans_cluster_edges() -> None:
    hits = [(1.0 + i * 0.5, "挖果") for i in range(9)]
    stamps = _ocr_edge_stamps(hits, 10.0, 0.5, 0.1, layout="horizontal")
    assert len(stamps) <= 22
    assert stamps[0] == 0.5 and stamps[-1] == 5.5


def test_longer_matching_label_completes_horizontal_text() -> None:
    horizontal = [{"source": "填充绿豆让橘壳晒干", "start": 28.1, "layout": "horizontal"}]
    label = [{"source": "填充绿豆让橘壳晒干 缩水不会变形", "start": 28.05, "layout": "label"}]
    out = _merge_horizontal_vertical(horizontal, label)
    assert len(out) == 1
    assert out[0]["source"] == "填充绿豆让橘壳晒干 缩水不会变形"


def test_label_stabilizes_vertical_title_and_extends_timing() -> None:
    vertical = [
        {"source": "花木業", "start": 3.8, "end": 4.4, "layout": "vertical"},
        {"source": "花木紫", "start": 27.8, "end": 30.0, "layout": "vertical"},
    ]
    label = [
        {"source": "花木紫", "start": 3.9, "end": 28.05, "layout": "label"}
    ]
    out = _merge_horizontal_vertical([], vertical)
    out = _merge_horizontal_vertical(out, label)
    assert len(out) == 1
    assert out[0]["source"] == "花木紫"
    assert out[0]["layout"] == "vertical"
    assert out[0]["start"] == 3.8
    assert out[0]["end"] == 30.0


def test_cover_box_contains_ocr_and_translation() -> None:
    fitted = _cover_box_fit([(430, 900, 650, 970)], (120, 880, 960, 995), 1080, 1920)
    assert fitted is not None
    assert fitted[0] <= 120 and fitted[1] <= 880
    assert fitted[2] >= 960 and fitted[3] >= 995


def test_auto_font_scales_with_resolution_and_stays_readable() -> None:
    assert _auto_subtitle_font_size(1080, 1920) == 63
    assert _auto_subtitle_font_size(720, 1280) == 42
    assert _auto_subtitle_font_size(320, 480) >= 24
    assert _auto_subtitle_font_size(2160, 3840) <= 72


def test_overlay_label_scan_keeps_top_two_line_caption() -> None:
    class FakeOCR:
        def __call__(self, _frame):
            return ([
                ([[120, 260], [700, 260], [700, 320], [120, 320]], "\u597d\u4e45\u6ca1\u6253\u626b\u536b\u751f\u4e86", 0.99),
                ([[120, 330], [880, 330], [880, 400], [120, 400]], "\u8d81\u7740\u9759\u7f6e\u65f6\u95f4\u6253\u626b\u4e00\u4e0b\u536b\u751f", 0.98),
            ], None)

    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    text = _ocr_labels_from_frame(frame, FakeOCR(), 1080, 1920)

    assert "\u597d\u4e45\u6ca1\u6253\u626b\u536b\u751f\u4e86" in text
    assert "\u8d81\u7740\u9759\u7f6e\u65f6\u95f4\u6253\u626b\u4e00\u4e0b\u536b\u751f" in text


if __name__ == "__main__":
    test_refine_only_scans_cluster_edges()
    test_longer_matching_label_completes_horizontal_text()
