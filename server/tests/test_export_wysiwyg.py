from pipeline.export.burn import (
    _editor_layout_locked,
    _preview_caption_layout,
    _resolve_segment_font_size,
    _stored_cover_should_relocate,
    _text_fill_rgba,
)
from pipeline.core.media import resolve_export_crop


def test_editor_layout_locked_trusts_bbox_and_caption() -> None:
    seg = {
        "layout": "mid",
        "source": "带在头上",
        "bbox": {"x": 100, "y": 800, "w": 800, "h": 120},
        "captionLayout": {
            "x": 120,
            "y": 820,
            "w": 760,
            "h": 80,
            "lines": ["Xin chào"],
            "fontSize": 36,
        },
    }
    assert _editor_layout_locked(seg) is True
    # Đáy khung + CJK — trước đây luôn relocate; editor locked thì không
    bottom = (100, 1600, 900, 1800)
    assert _stored_cover_should_relocate(seg, bottom, 1920) is False


def test_mid_editor_layouts_inside_bbox_not_cl_xy() -> None:
    """Mid locked: chữ căn theo bbox, không dán captionLayout x/y lệch."""
    from pipeline.export.burn import _layout_mid_caption
    from PIL import ImageFont

    def font_getter(fs: int):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", fs)
        except OSError:
            return ImageFont.load_default()

    paint = (328, 1159, 751, 1265)
    lay = _layout_mid_caption(
        "Ngoài ra còn có tre",
        font_getter,
        paint,
        1080,
        1920,
        preferred_fs=35,
    )
    assert lay is not None
    x0, y0, x1, y1 = lay["box"]
    # box bám OCR cover, không nhảy sang captionLayout giả (vd. y=100)
    assert abs(y0 - 1159) <= 2
    assert abs(x0 - 328) <= 2
    assert x1 - x0 == 751 - 328
    assert y1 - y0 == 1265 - 1159


def test_unlocked_bottom_cjk_still_relocates() -> None:
    seg = {
        "layout": "horizontal",
        "source": "带在头上的帽子",
        "bbox": {"x": 100, "y": 1600, "w": 800, "h": 120},
    }
    assert _editor_layout_locked(seg) is False
    assert _stored_cover_should_relocate(seg, (100, 1600, 900, 1720), 1920) is True


def test_resolve_export_crop_9x16_on_landscape() -> None:
    """Preview 9:16 trên video ngang → crop center."""
    crop = resolve_export_crop(1920, 1080, "9:16")
    assert crop is not None
    x, y, w, h = crop
    assert abs(w / h - 9 / 16) < 0.02
    assert y == 0 or abs(y) <= 2
    assert abs(x + w / 2 - 960) < 4


def test_resolve_export_crop_original_is_noop() -> None:
    assert resolve_export_crop(1080, 1920, "original") is None
    assert resolve_export_crop(1080, 1920, "custom") is None


def test_caption_layout_font_preferred_over_auto() -> None:
    seg = {
        "captionLayout": {
            "x": 10,
            "y": 10,
            "w": 200,
            "h": 40,
            "lines": ["Hi"],
            "fontSize": 28,
        }
    }
    assert (
        _resolve_segment_font_size(
            seg, 1080, 1920, project_font_size=0, default_font_size=42, auto_fontsize=True
        )
        == 28
    )


def test_preview_layout_keeps_overlay_color() -> None:
    from PIL import ImageFont

    def font_getter(fs: int):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", fs)
        except OSError:
            return ImageFont.load_default()

    seg = {
        "textColor": "#ff6600",
        "captionLayout": {
            "x": 50,
            "y": 100,
            "w": 300,
            "h": 60,
            "lines": ["Hello"],
            "fontSize": 36,
        },
    }
    assert _text_fill_rgba(seg) == (255, 102, 0, 255)
    lay = _preview_caption_layout(seg, 36, font_getter)
    assert lay is not None
    assert lay["fill"] == (255, 102, 0, 255)
    assert lay["box"] == (50, 100, 350, 160)


if __name__ == "__main__":
    test_editor_layout_locked_trusts_bbox_and_caption()
    test_unlocked_bottom_cjk_still_relocates()
    test_resolve_export_crop_9x16_on_landscape()
    test_resolve_export_crop_original_is_noop()
    test_caption_layout_font_preferred_over_auto()
    test_preview_layout_keeps_overlay_color()
    print("ok")
