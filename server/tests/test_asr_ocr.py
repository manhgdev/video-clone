import numpy as np

from pipeline.ocr.extract import (
    _merge_horizontal_vertical,
    _ocr_edge_stamps,
    _ocr_labels_from_frame,
)
from pipeline.export.burn import _auto_subtitle_font_size, _blur_tint_alpha, _cover_box_fit


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


def test_label_extends_matching_mid_window() -> None:
    """Label mảnh cùng chữ mid phải nới end khi kề cửa sổ mid."""
    mid = [{"source": "挖果", "start": 7.9, "end": 8.9, "layout": "mid"}]
    labels = [
        {"source": "挖果", "start": 9.0, "end": 9.5, "layout": "label"},
        {"source": "挖果", "start": 10.0, "end": 12.2, "layout": "label"},
    ]
    out = _merge_horizontal_vertical(mid, labels)
    assert len(out) == 1
    assert out[0]["layout"] == "mid"
    assert out[0]["start"] == 7.9
    assert out[0]["end"] == 12.2


def test_distant_mid_flashes_do_not_merge() -> None:
    """Hai flash mid cùng chữ cách xa không nối xuyên clip."""
    mid = [
        {"source": "尔", "start": 5.4, "end": 5.6, "layout": "mid"},
        {"source": "尔", "start": 19.3, "end": 19.5, "layout": "mid"},
        {"source": "挖果", "start": 7.9, "end": 12.7, "layout": "mid"},
    ]
    out = _merge_horizontal_vertical([], mid)
    er = [s for s in out if s["source"] == "尔"]
    dig = [s for s in out if s["source"] == "挖果"]
    assert len(er) == 2
    assert er[0]["end"] < 6
    assert er[1]["start"] > 19
    assert len(dig) == 1 and dig[0]["end"] >= 12.7


def test_fold_watermark_label_into_vertical() -> None:
    from pipeline.ocr.extract import _fold_duplicate_watermark_labels

    segs = [
        {
            "source": "花木紫",
            "start": 4.2,
            "end": 20.0,
            "layout": "vertical",
            "bbox": {"x": 166, "y": 228, "w": 73, "h": 198},
        },
        {
            "source": "花木紫",
            "start": 5.9,
            "end": 6.2,
            "layout": "label",
            "bbox": {"x": 165, "y": 230, "w": 77, "h": 198},
        },
    ]
    out = _fold_duplicate_watermark_labels(segs)
    assert len(out) == 1
    assert out[0]["layout"] == "vertical"
    assert out[0]["start"] == 4.2 and out[0]["end"] == 20.0


def test_drop_mid_glyph_inside_vertical_watermark() -> None:
    from pipeline.ocr.extract import _drop_mid_in_watermark_column

    segs = [
        {
            "source": "花木紫",
            "start": 4.2,
            "end": 20.0,
            "layout": "vertical",
            "bbox": {"x": 165, "y": 228, "w": 77, "h": 200},
        },
        {
            "source": "尔",
            "start": 5.4,
            "end": 5.6,
            "layout": "mid",
            "bbox": {"x": 184, "y": 385, "w": 38, "h": 29},
        },
        {
            "source": "挖果",
            "start": 7.9,
            "end": 12.8,
            "layout": "mid",
            "bbox": {"x": 194, "y": 910, "w": 138, "h": 74},
        },
    ]
    out = _drop_mid_in_watermark_column(segs)
    assert len(out) == 2
    assert out[0]["layout"] == "vertical"
    assert out[1]["source"] == "挖果"


def test_overlay_classify_keeps_mid_out_of_watermark_column() -> None:
    """1 detect: cột dọc + glyph nhiễu + mid thật → không mid trong cột."""
    from pipeline.ocr.extract import _classify_overlay_detections

    vw, vh = 1080, 1920
    dets = [
        ("花", 0.95, 170.0, 230.0, 210.0, 280.0),
        ("木", 0.95, 168.0, 285.0, 212.0, 340.0),
        ("紫", 0.95, 169.0, 345.0, 211.0, 400.0),
        ("尔", 0.92, 184.0, 385.0, 222.0, 414.0),
        ("挖果", 0.96, 470.0, 900.0, 620.0, 970.0),
    ]
    out = _classify_overlay_detections(dets, vw, vh)
    verts = out["vertical"]
    mids = out["mid"]
    assert len(verts) == 1
    assert "花" in verts[0][0] and "木" in verts[0][0]
    assert all(m[0] != "尔" for m in mids)
    assert any(m[0] == "挖果" for m in mids)


def test_fold_vertical_column_flickers() -> None:
    from pipeline.ocr.extract import _fold_vertical_column_flickers

    segs = [
        {
            "source": "花水年",
            "start": 4.1,
            "end": 19.0,
            "layout": "vertical",
            "bbox": {"x": 166, "y": 229, "w": 78, "h": 192},
        },
        {
            "source": "花木紫",
            "start": 4.2,
            "end": 20.0,
            "layout": "vertical",
            "bbox": {"x": 167, "y": 232, "w": 74, "h": 193},
        },
        {
            "source": "工",
            "start": 16.0,
            "end": 19.5,
            "layout": "vertical",
            "bbox": {"x": 142, "y": 233, "w": 96, "h": 190},
        },
        {
            "source": "挖果",
            "start": 7.9,
            "end": 12.7,
            "layout": "mid",
            "bbox": {"x": 196, "y": 912, "w": 137, "h": 72},
        },
    ]
    out = _fold_vertical_column_flickers(segs)
    verts = [s for s in out if s["layout"] == "vertical"]
    assert len(verts) == 1
    assert verts[0]["start"] <= 4.2 and verts[0]["end"] >= 19.0
    assert "花" in verts[0]["source"]
    assert not any(s["source"] == "工" for s in out)


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


def test_hardsub_rejects_ascii_noise() -> None:
    from pipeline.ocr.extract import _hardsub_line_keep

    for junk in ("CC", "888 88", "388", "8", "AB", "意力", "洗葛"):
        assert not _hardsub_line_keep(junk, "auto"), junk
        assert not _hardsub_line_keep(junk, "zh"), junk
    assert _hardsub_line_keep("灰尘照得很清楚", "auto")
    assert _hardsub_line_keep("填充绿豆让橘壳晒干缩水不会变形", "auto")
    # 1 glyph CJK đáy = nhiễu
    assert not _hardsub_line_keep("一", "auto")


def test_mid_absorbs_duplicate_horizontal() -> None:
    """Mid flash bị crop đáy nuốt thành Caption → gộp về mid."""
    horiz = [
        {
            "source": "灰尘照得很清楚",
            "start": 184.0,
            "end": 188.0,
            "layout": "horizontal",
        }
    ]
    mid = [
        {
            "source": "灰尘照得很清楚",
            "start": 184.5,
            "end": 187.0,
            "layout": "mid",
            "bbox": {"x": 100, "y": 900, "w": 400, "h": 80},
        }
    ]
    out = _merge_horizontal_vertical(horiz, mid)
    assert len(out) == 1
    assert out[0]["layout"] == "mid"
    assert out[0]["source"] == "灰尘照得很清楚"


def test_overlay_stamp_budget_scales_for_long_video() -> None:
    from pipeline.ocr.overlay_scan import _budget_stamps, _refine_stamps_gone

    short = _budget_stamps(30.0)
    long = _budget_stamps(400.0)
    assert len(short) >= 10
    assert len(long) >= 80
    assert len(long) <= 150  # không nổ coarse → không còn nghìn refine
    assert len(long) > len(short)
    assert _refine_stamps_gone()


def test_binary_edge_search_eps() -> None:
    """Biên binary vẫn đúng khi gọi trực tiếp (fallback)."""
    from pipeline.ocr.overlay_scan import _binary_edge_end, _binary_edge_start

    def has(t: float) -> bool:
        return 1.0 <= t < 3.0

    start = _binary_edge_start(has, 0.0, 2.0, eps=0.1)
    end = _binary_edge_end(has, 2.0, 4.0, eps=0.1)
    assert abs(start - 1.0) <= 0.12
    assert abs(end - 3.0) <= 0.12


def test_edge_refine_uses_coarse_pad_without_ocr() -> None:
    """Neighbor coarse trống → pad midpoint, không gọi OCR."""
    from pipeline.ocr.overlay_scan import _edge_refine_cluster

    cluster = [(4.0, "定型", {"x": 1, "y": 1, "w": 50, "h": 20})]
    coarse = [2.0, 4.0, 6.0]
    stamp_layouts = {
        2.0: set(),
        4.0: {"mid"},
        6.0: set(),
    }

    class _Boom:
        def ocr_at(self, t: float):
            raise AssertionError(f"should not OCR at {t}")

    out, hint = _edge_refine_cluster(
        _Boom(),  # type: ignore[arg-type]
        cluster,
        "mid",
        coarse=coarse,
        video_end=20.0,
        stamp_layouts=stamp_layouts,
    )
    assert len(out) == 3
    assert abs(out[0][0] - 3.0) < 1e-6  # pad (2+4)/2
    assert abs(out[-1][0] - 4.95) < 0.1 or abs(out[-1][0] - 5.0) < 0.1
    assert hint["empty_before"] is True and hint["empty_after"] is True
    assert hint["t_after"] == 6.0


def test_pad_edge_helpers() -> None:
    from pipeline.ocr.overlay_scan import _pad_edge_end, _pad_edge_start

    # Bias sớm (0.2) / muộn (0.8) — tránh midpoint để chữ lộ trước bbox
    assert abs(_pad_edge_start(2.0, 4.0) - 2.4) < 1e-6
    assert abs(_pad_edge_end(4.0, 6.0) - 5.6) < 1e-6


def test_cluster_hits_splits_flashes() -> None:
    from pipeline.ocr.overlay_scan import _cluster_hits

    hits = [
        (10.0, "挖果", None),
        (11.2, "挖果", None),
        (40.0, "定型", None),
        (41.0, "定型", None),
    ]
    groups = _cluster_hits(hits, gap=1.5)
    assert len(groups) == 2
    assert groups[0][0][1] == "挖果"
    assert groups[1][0][1] == "定型"


def test_vertical_long_does_not_need_dense_grid() -> None:
    """Vertical xuyên clip: sticky → không đưa vào vòng biên flash."""
    from pipeline.ocr.overlay_scan import (
        _cluster_hits,
        _is_sticky_vertical,
        _partition_vert_clusters,
        _VERT_LONG_SEC,
    )

    # ~200 coarse hits dọc suốt 400s
    hits = [(float(i) * 2.0, "花木紫", {"x": 40, "y": 100, "w": 40, "h": 200}) for i in range(200)]
    clusters = _cluster_hits(hits, gap=2.5)
    assert len(clusters) == 1
    assert _is_sticky_vertical(clusters[0])
    span = float(clusters[0][-1][0]) - float(clusters[0][0][0])
    assert span >= _VERT_LONG_SEC
    sticky, flash = _partition_vert_clusters(clusters)
    assert len(sticky) == 1 and flash == []


def test_sticky_vertical_returns_three_anchors_not_all_coarse() -> None:
    """Sticky không dump hàng trăm hit coarse — chỉ 3 neo đầu/giữa/cuối."""
    from pipeline.ocr.overlay_scan import _sticky_vertical_edges

    cluster = [
        (float(i) * 2.0, "花木紫", {"x": 40, "y": 100, "w": 40, "h": 200}) for i in range(50)
    ]
    coarse = [float(h[0]) for h in cluster]

    class _FakeProbe:
        def ocr_at(self, t: float):
            # chữ dọc suốt [0, 100]
            if 0.0 <= t <= 100.0:
                return {"vertical": [("花木紫", cluster[0][2])], "mid": [], "label": []}
            return {"vertical": [], "mid": [], "label": []}

    out, _hint = _sticky_vertical_edges(_FakeProbe(), cluster, coarse=coarse, video_end=100.0)  # type: ignore[arg-type]
    assert len(out) == 3
    assert out[0][0] <= out[1][0] <= out[2][0]
    assert out[0][1] == "花木紫"


def test_short_vertical_flash_not_sticky() -> None:
    """Flash dọc ngắn vẫn edge-search (không sticky)."""
    from pipeline.ocr.overlay_scan import _is_sticky_vertical, _partition_vert_clusters

    flash = [(10.0, "竖", None), (11.0, "竖", None)]
    assert not _is_sticky_vertical(flash)
    sticky, short = _partition_vert_clusters([flash])
    assert sticky == [] and len(short) == 1


def test_refined_anchors_become_one_segment() -> None:
    """3 neo biên cách xa không tách thành 3 segment trùng chữ."""
    from pipeline.ocr.overlay_scan import _seg_from_refined_hits

    # mid flash 4s: neo đầu / giữa / cuối như _edge_refine_cluster
    hits = [
        (1.0, "定型", {"x": 100, "y": 400, "w": 200, "h": 80}),
        (3.0, "定型", {"x": 100, "y": 400, "w": 200, "h": 80}),
        (4.95, "定型", {"x": 100, "y": 400, "w": 200, "h": 80}),
    ]
    seg = _seg_from_refined_hits(hits, layout="mid", video_end=60.0)
    assert seg is not None
    assert seg["source"] == "定型"
    assert abs(float(seg["start"]) - 1.0) < 1e-6
    assert abs(float(seg["end"]) - 4.95) < 1e-6


def test_sticky_three_anchors_one_vertical_segment() -> None:
    from pipeline.ocr.overlay_scan import _seg_from_refined_hits

    hits = [
        (0.0, "花木紫", {"x": 40, "y": 100, "w": 40, "h": 200}),
        (50.0, "花木紫", {"x": 40, "y": 100, "w": 40, "h": 200}),
        (99.95, "花木紫", {"x": 40, "y": 100, "w": 40, "h": 200}),
    ]
    seg = _seg_from_refined_hits(hits, layout="vertical", video_end=100.0, min_hold=0.15)
    assert seg is not None
    assert float(seg["end"]) - float(seg["start"]) > 90


def test_merge_mid_segments_joins_coarse_shards() -> None:
    """Cùng chữ mỗi ~2s coarse → 1 mid dài (không 5 dòng trùng)."""
    from pipeline.ocr.extract import _merge_mid_segments

    shards = [
        {"source": "播放", "start": 4.2, "end": 5.0, "layout": "mid"},
        {"source": "播放", "start": 6.0, "end": 6.8, "layout": "mid"},
        {"source": "播放", "start": 8.0, "end": 8.8, "layout": "mid"},
    ]
    out = _merge_mid_segments(shards, max_gap=2.5)
    assert len(out) == 1
    assert out[0]["source"] == "播放"
    assert out[0]["start"] <= 4.2
    assert out[0]["end"] >= 8.8


def test_merge_mid_segments_keeps_distant_same_glyph() -> None:
    from pipeline.ocr.extract import _merge_mid_segments

    shards = [
        {"source": "尔", "start": 5.4, "end": 5.6, "layout": "mid"},
        {"source": "尔", "start": 19.3, "end": 19.5, "layout": "mid"},
    ]
    out = _merge_mid_segments(shards, max_gap=2.5)
    assert len(out) == 2


def test_cluster_hits_overlay_same_text_wide_gap() -> None:
    from pipeline.ocr.overlay_scan import _cluster_hits_overlay

    hits = [
        (4.0, "列表", None),
        (6.0, "列表", None),
        (8.0, "列表", None),
        (40.0, "定型", None),
    ]
    groups = _cluster_hits_overlay(hits, gap=1.5, coarse_step=2.0, layout="mid")
    assert len(groups) == 2
    assert len(groups[0]) == 3
    assert groups[0][0][1] == "列表"


def test_attach_cover_reaches_empty_coarse_after() -> None:
    """Mid hit 8s, mốc trống 10s → coverEnd >= 10; coverStart không kéo về mốc trống trước."""
    from pipeline.ocr.cover_timing import attach_cover_times, resolve_cover_window

    seg = {
        "source": "播放",
        "start": 8.0,
        "end": 9.0,
        "layout": "mid",
    }
    attach_cover_times(
        seg,
        t_before=6.0,
        t_after=10.0,
        video_end=60.0,
        neighbor_empty_before=True,
        neighbor_empty_after=True,
    )
    assert float(seg["coverEnd"]) >= 10.0
    # có thể kéo coverStart sớm về gần mốc trống trước (chữ đã hiện trong khe)
    assert float(seg["coverStart"]) <= 8.0
    assert float(seg["coverStart"]) >= 6.0
    cs, ce = resolve_cover_window(seg)
    assert ce >= 10.0
    assert cs <= 8.0


def test_resolve_cover_clamps_early_saved_start() -> None:
    """coverStart đã lưu quá sớm (OCR cũ) — resolve kẹp gần start."""
    from pipeline.ocr.cover_timing import resolve_cover_window

    seg = {
        "source": "列表",
        "start": 8.0,
        "end": 9.0,
        "layout": "mid",
        "coverStart": 6.0,
        "coverEnd": 10.0,
    }
    cs, ce = resolve_cover_window(seg)
    assert ce >= 10.0
    # kẹp lead ~0.35 khi coverStart lưu quá sớm
    assert cs >= 8.0 - 0.4
    assert cs > 6.5


def test_resolve_cover_extends_short_saved_end() -> None:
    """coverEnd lưu ngắn hơn clip mid — resolve phải kéo tới end+tail."""
    from pipeline.ocr.cover_timing import resolve_cover_window

    seg = {
        "source": "挖果",
        "start": 120.0,
        "end": 125.0,
        "layout": "mid",
        "coverStart": 119.92,
        "coverEnd": 122.0,
    }
    cs, ce = resolve_cover_window(seg)
    assert cs <= 120.0
    assert ce >= 125.0 + 0.22 - 0.01


def test_resolve_cover_fallback_without_fields() -> None:
    from pipeline.ocr.cover_timing import resolve_cover_window

    seg = {"source": "定型", "start": 4.0, "end": 5.0, "layout": "mid"}
    cs, ce = resolve_cover_window(seg)
    assert cs <= 4.0 and ce >= 5.0
    assert ce - cs >= (5.0 - 4.0)


def test_seg_from_refined_attaches_cover() -> None:
    from pipeline.ocr.overlay_scan import _seg_from_refined_hits

    hits = [
        (8.0, "播放", {"x": 1, "y": 1, "w": 40, "h": 20}),
        (8.5, "播放", {"x": 1, "y": 1, "w": 40, "h": 20}),
        (9.0, "播放", {"x": 1, "y": 1, "w": 40, "h": 20}),
    ]
    seg = _seg_from_refined_hits(
        hits,
        layout="mid",
        video_end=60.0,
        cover_hint={
            "t_before": 6.0,
            "t_after": 10.0,
            "empty_before": True,
            "empty_after": True,
        },
    )
    assert seg is not None
    assert float(seg["coverEnd"]) >= 10.0


def test_blur_tint_alpha_matches_preview() -> None:
    # Đồng bộ coverMaskPreviewStyle: tintA = clamp(0.38 + a*0.5, 0.55, 0.90)
    assert abs(_blur_tint_alpha(40) - 0.58) < 1e-9  # default UI
    assert _blur_tint_alpha(0) == 0.55
    assert abs(_blur_tint_alpha(100) - 0.88) < 1e-9
    assert abs(_blur_tint_alpha(80) - 0.78) < 1e-9


if __name__ == "__main__":
    test_refine_only_scans_cluster_edges()
    test_longer_matching_label_completes_horizontal_text()
    test_label_stabilizes_vertical_title_and_extends_timing()
    test_label_extends_matching_mid_window()
    test_distant_mid_flashes_do_not_merge()
    test_fold_watermark_label_into_vertical()
    test_drop_mid_glyph_inside_vertical_watermark()
    test_overlay_classify_keeps_mid_out_of_watermark_column()
    test_fold_vertical_column_flickers()
    test_cover_box_contains_ocr_and_translation()
    test_auto_font_scales_with_resolution_and_stays_readable()
    test_overlay_label_scan_keeps_top_two_line_caption()
    test_hardsub_rejects_ascii_noise()
    test_mid_absorbs_duplicate_horizontal()
    test_overlay_stamp_budget_scales_for_long_video()
    test_binary_edge_search_eps()
    test_edge_refine_uses_coarse_pad_without_ocr()
    test_pad_edge_helpers()
    test_cluster_hits_splits_flashes()
    test_vertical_long_does_not_need_dense_grid()
    test_sticky_vertical_returns_three_anchors_not_all_coarse()
    test_short_vertical_flash_not_sticky()
    test_refined_anchors_become_one_segment()
    test_sticky_three_anchors_one_vertical_segment()
    test_merge_mid_segments_joins_coarse_shards()
    test_merge_mid_segments_keeps_distant_same_glyph()
    test_cluster_hits_overlay_same_text_wide_gap()
    test_attach_cover_reaches_empty_coarse_after()
    test_resolve_cover_clamps_early_saved_start()
    test_resolve_cover_fallback_without_fields()
    test_seg_from_refined_attaches_cover()
    test_blur_tint_alpha_matches_preview()
    print("ok")
