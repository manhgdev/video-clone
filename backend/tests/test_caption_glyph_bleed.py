import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

from pipeline.export.burn_parts.layout_text import _caption_overlay


def test_vietnamese_diacritics_and_descenders_are_not_clipped():
    font = ImageFont.truetype(str(Path("C:/Windows/Fonts/arial.ttf")), 40)
    text = "nặng gạch quý"
    box = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
    text_h = box[3] - box[1]
    overlay = _caption_overlay({
        "box": (20, 20, 420, 20 + text_h),
        "font": font,
        "lines": [text],
        "pad_y": 0,
        "text_h": text_h,
        "line_hs": [text_h],
        "stroke": False,
    })

    assert overlay is not None
    rgba, _x, _y = overlay
    ys, xs = np.where(rgba[:, :, 3] > 0)
    assert len(xs) > 0
    assert ys.min() > 0 and ys.max() < rgba.shape[0] - 1
    # Ink vẫn quanh tâm sau khi chừa bleed; không tụt xuống do font baseline.
    assert abs((ys.min() + ys.max()) / 2 - rgba.shape[0] / 2) <= 4
