from __future__ import annotations

import numpy as np
from PIL import ImageFont

from pipeline.export.burn import (
    _apply_cover_mask,
    _blur_region,
    _cover_box_fit,
    _cover_box_over,
    _is_corner_ui_box,
    _layout_caption_over,
    _merge_ocr_samples,
    _resolve_segment_font_size,
    _segment_bbox_override,
)
from pipeline.ocr.labels import clamp_label_box, cover_fit_label, layout_label_caption
from pipeline.ocr.locate import _source_matches


def test_cover_box_uses_resolution_aware_horizontal_padding() -> None:
    box = _cover_box_fit([(400, 800, 680, 850)], None, 1080, 1920)

    assert box == (394, 796, 686, 854)


def test_segment_bbox_override_clamps_to_video_bounds() -> None:
    box = _segment_bbox_override(
        {"bbox": {"x": 900, "y": 1000, "w": 300, "h": 200}}, 1080, 1920
    )

    assert box == (900, 1000, 1080, 1200)


def test_segment_bbox_override_preserves_ocr_height() -> None:
    box = _segment_bbox_override(
        {"bbox": {"x": 100, "y": 1500, "w": 800, "h": 120}}, 1080, 1920
    )

    assert box is not None
    assert box == (100, 1500, 900, 1620)


def test_resolve_segment_font_size_prefers_segment_then_project() -> None:
    seg = {"fontSize": 48}
    assert _resolve_segment_font_size(
        seg, 1080, 1920, project_font_size=0, default_font_size=42, auto_fontsize=True,
    ) == 48
    assert _resolve_segment_font_size(
        {}, 1080, 1920, project_font_size=36, default_font_size=42, auto_fontsize=True,
    ) == 36
    assert _resolve_segment_font_size(
        {}, 1080, 1920, project_font_size=0, default_font_size=42, auto_fontsize=True,
    ) == 36
    assert _resolve_segment_font_size(
        {}, 1080, 1920, project_font_size=0, default_font_size=42, auto_fontsize=False,
    ) == 42


def test_translation_box_expands_width_not_height() -> None:
    # Caption chỉ nới ngang; Y bám OCR (không dính pad layout).
    box = _cover_box_fit(
        [(400, 800, 680, 850)], (250, 760, 830, 900), 1080, 1920
    )

    assert box == (244, 796, 836, 854)


def test_cover_tight_mode_ignores_caption_size() -> None:
    box = _cover_box_fit(
        [(400, 800, 680, 850)], (250, 760, 830, 900), 1080, 1920, tight=True
    )

    assert box == (396, 798, 684, 852)


def test_layout_caption_over_single_line() -> None:
    ocr = (400, 800, 620, 860)
    lay, cover = _layout_caption_over(
        "Biển ở nơi trồng cây này nhuộm màu trắng sữa",
        36,
        ocr,
        1080,
        1920,
        "海边",
    )
    assert len(lay["lines"]) == 1
    assert lay["fontsize"] == 36
    assert cover[2] - cover[0] >= lay["box"][2] - lay["box"][0]


def test_cover_over_unions_ocr_and_caption_with_pad() -> None:
    ocr = (400, 800, 720, 860)
    caption = (450, 810, 620, 848)
    box = _cover_box_over(ocr, caption, 36, 1080, 1920)

    pad_x = max(8, int(round(1080 * 0.012)))
    content = max(720 - 400, 620 - 450)
    bleed = max(2, int(round(content * 0.006)))
    assert box[2] - box[0] <= content + bleed * 2 + 2
    assert box[1] <= 800 - 3
    assert box[3] >= 860 + max(8, int(round(36 * 0.22)))


def test_cover_over_grows_when_translation_wider() -> None:
    ocr = (480, 800, 580, 860)
    caption = (400, 810, 680, 848)
    box = _cover_box_over(ocr, caption, 36, 1080, 1920)

    assert box[2] - box[0] >= 680 - 400


def test_cover_over_uses_source_width() -> None:
    ocr = (480, 800, 580, 860)
    caption = (450, 810, 620, 848)
    src = "用精液填满整个海岸"
    box = _cover_box_over(ocr, caption, 36, 1080, 1920, src)

    assert box[2] - box[0] >= 620 - 450


def test_far_caption_does_not_shift_cover_y() -> None:
    box = _cover_box_fit(
        [(400, 1600, 680, 1680)], (200, 900, 880, 980), 1080, 1920
    )

    assert box[1] >= 1590
    assert box[3] <= 1690
    assert box[0] <= 200
    assert box[2] >= 880


def test_cover_box_clamps_excessive_height() -> None:
    box = _cover_box_fit([(300, 400, 780, 1200)], None, 1080, 1920)

    assert box == (294, 714, 786, 886)


def test_blur_region_fully_replaces_horizontal_edges() -> None:
    frame = np.zeros((60, 100, 3), dtype=np.uint8)
    frame[30:40, 20:80] = 255

    result = _blur_region(frame.copy(), (20, 25, 80, 45))

    # Horizontal boundary pixels are covered, not partially blended with the
    # original white subtitle pixels.
    assert np.all(result[32:38, 20] < 100)
    assert np.all(result[32:38, 79] < 100)


def test_corner_ui_box_detected() -> None:
    # "12" góc trái — hẹp, sát mép.
    assert _is_corner_ui_box((20, 1500, 120, 1650), 1080, 1920)
    # hardsub giữa không bị coi là UI.
    assert not _is_corner_ui_box((280, 1500, 800, 1580), 1080, 1920)


def test_merge_ocr_samples_ignores_left_corner_logo() -> None:
    # Logo góc + hardsub giữa cùng dải Y → chỉ giữ hardsub.
    logo = (10, 1500, 100, 1640)
    sub = (300, 1520, 780, 1580)
    boxes = _merge_ocr_samples([logo, sub], 1080, 1920)
    assert len(boxes) == 1
    x0, _y0, x1, _y1 = boxes[0]
    assert x0 >= 200  # không kéo sang logo "12"
    assert x1 >= 780


def test_wide_horizontal_label_is_not_cropped() -> None:
    detected = (122, 264, 884, 403)

    clamped = clamp_label_box(detected, 1080, 1920)
    covered = cover_fit_label(clamped, None, 1080, 1920)

    assert clamped[0] <= detected[0] and clamped[2] >= detected[2]
    assert covered is not None
    assert covered[0] <= detected[0] and covered[2] >= detected[2]


def test_pure_cjk_uses_horizontal_ocr_box_shape() -> None:
    layout = layout_label_caption(
        "Lâu rồi chưa dọn dẹp",
        ImageFont.load_default(),
        48,
        (122, 264, 884, 403),
        1080,
        1920,
        font_path="",
        source="\u597d\u4e45\u6ca1\u6253\u626b\u536b\u751f\u4e86",
    )

    assert layout["vertical"] is False


def test_long_source_rejects_single_shared_noise_glyph() -> None:
    source = "\u597d\u4e45\u6ca1\u6253\u626b\u536b\u751f\u4e86\u8d81\u7740\u9759\u7f6e\u65f6\u95f4\u6253\u626b\u4e00\u4e0b\u536b\u751f"

    assert not _source_matches("II \u5e02", source)
    assert _source_matches("\u82b1\u6728\u696d", "\u82b1\u6728\u7d2b")


def test_preview_caption_layout_from_client() -> None:
    from pipeline.export.burn import _preview_caption_layout

    seg = {
        "captionLayout": {
            "x": 100,
            "y": 800,
            "w": 400,
            "h": 48,
            "lines": ["Xin chào"],
            "fontSize": 36,
        },
    }
    fonts: dict[int, object] = {}

    def _font(fs: int):
        from PIL import ImageFont

        if fs not in fonts:
            fonts[fs] = ImageFont.load_default()
        return fonts[fs]

    lay = _preview_caption_layout(seg, 36, _font)
    assert lay is not None
    assert lay["box"] == (100, 800, 500, 848)
    assert lay["lines"] == ["Xin chào"]
    assert lay["fontsize"] == 36


def test_cover_to_anchor_roundtrip() -> None:
    from pipeline.export.burn import _cover_to_anchor, _layout_caption_over

    ocr = (400, 800, 620, 860)
    lay, cover = _layout_caption_over("Xin chào", 36, ocr, 1080, 1920, source_text="你好")
    anchor = _cover_to_anchor(cover, 36, 1080, 1920)
    assert cover[0] <= anchor[0] and cover[1] <= anchor[1]
    assert cover[2] >= anchor[2] and cover[3] >= anchor[3]
    assert lay["box"][1] >= ocr[1]


def test_apply_cover_mask_solid_fills_region() -> None:
    frame = np.full((80, 80, 3), 40, dtype=np.uint8)
    out = _apply_cover_mask(
        frame.copy(), (10, 10, 50, 30), style="solid", color_hex="#ff0000", opacity_pct=100
    )
    assert out[20, 30, 2] > 200
    assert out[5, 5, 0] == 40


def test_segment_bbox_override_keeps_user_size() -> None:
    from pipeline.export.burn import _segment_bbox_override

    seg = {"bbox": {"x": 80, "y": 900, "w": 920, "h": 180}}
    assert _segment_bbox_override(seg, 1080, 1920) == (80, 900, 1000, 1080)


def test_apply_cover_mask_blur_hides_bright_text() -> None:
    frame = np.zeros((60, 120, 3), dtype=np.uint8)
    frame[25:35, 20:100] = 255
    out = _apply_cover_mask(
        frame.copy(), (15, 20, 105, 40), style="blur", color_hex="#4c1d95", opacity_pct=40
    )
    assert out[30, 50].max() < 240
    assert out[5, 5, 0] == 0


def test_parse_hex_color_default_violet() -> None:
    from pipeline.export.burn import _parse_hex_color

    assert _parse_hex_color("#4c1d95") == (76, 29, 149)
    assert _parse_hex_color("bad") == (76, 29, 149)
