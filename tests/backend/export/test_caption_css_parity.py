"""Parity render caption: export vẽ chữ đúng mô hình CSS của preview."""
import numpy as np
from PIL import ImageFont

from pipeline.export.burn_parts.layout_text import (
    _caption_overlay,
    _css_block_layout,
)
from pipeline.export.fonts import _subtitle_font


def test_css_block_layout_matches_preview_flex():
    # mid: line-height 1.1, căn giữa khối trong box
    top, step = _css_block_layout("mid", 40, 2, 120)
    assert abs(step - 44.0) < 1e-9
    assert abs(top - (120 - 88) / 2.0) < 1e-9
    # horizontal/label: 1.12
    top, step = _css_block_layout("horizontal", 50, 1, 100)
    assert abs(step - 56.0) < 1e-6
    assert abs(top - (100 - 56) / 2.0) < 1e-6
    # overlay (textarea free-text): top-align, 1.25
    top, step = _css_block_layout("overlay", 40, 3, 400)
    assert top == 0.0 and abs(step - 50.0) < 1e-9
    # dọc: pitch 1em + gap 0.08em, translateY(-0.06em)
    top, step = _css_block_layout("vertical", 30, 4, 300)
    assert abs(step - 32.4) < 1e-9
    block = 30 * 4 + 2.4 * 3
    assert abs(top - ((300 - block) / 2.0 - 1.8)) < 1e-9
    # khối cao hơn box: flex center tràn đều 2 phía (top âm, không kẹp 0)
    top, _ = _css_block_layout("mid", 60, 3, 100)
    assert top < 0


def _ink_top(lines, font, mode="mid"):
    overlay = _caption_overlay({
        "box": (0, 0, 400, 120),
        "font": font,
        "fontsize": font.size,
        "lines": lines,
        "pad_y": 0,
        "text_h": font.size,
        "line_hs": [font.size],
        "gap_line": 2,
        "stroke": False,
        "css_cover_mode": mode,
    })
    assert overlay is not None
    rgba, _x, _y = overlay
    ys, _xs = np.where(rgba[:, :, 3] > 0)
    return int(ys.min())


def test_css_mode_keeps_baseline_stable_across_diacritics():
    """Dòng có/không dấu-đuôi phải giữ nguyên baseline (như CSS), không nhảy y."""
    font = ImageFont.truetype(_subtitle_font(), 48)
    # "Ho" vs "Họ": cùng cap-height đầu dòng; ink-centering cũ đẩy "Họ" lên cao
    plain = _ink_top(["Ho"], font)
    descender = _ink_top(["Họ"], font)
    assert abs(plain - descender) <= 1, (plain, descender)
