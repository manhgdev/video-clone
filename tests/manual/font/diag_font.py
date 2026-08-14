"""Compare FE-baked captionLayout vs BE rendered text.

Run: PYTHONPATH=backend python tests/manual/font/diag_font.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from PIL import Image, ImageDraw, ImageFont
from pipeline.export.fonts import _font_for_preset

SAMPLE_TEXT = "Xin chào Việt Nam đẹp lắm"
FONT_KEY = "system"
FONT_SIZE = 48

font_path = _font_for_preset(FONT_KEY)
font = ImageFont.truetype(font_path, FONT_SIZE)
print(f"Font: {font_path}")
print(f"Size: {FONT_SIZE}px")

# Measure
draw_probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
bb = draw_probe.textbbox((0, 0), SAMPLE_TEXT, font=font)
print(f"PIL textbbox: {bb}")
print(f"PIL width: {bb[2] - bb[0]}")
print(f"PIL height: {bb[3] - bb[1]}")
length = font.getlength(SAMPLE_TEXT)
print(f"PIL getlength: {length}")

ascent, descent = font.getmetrics()
print(f"Font metrics: ascent={ascent}, descent={descent}")

# CSS line-height
line_h = FONT_SIZE * 1.12
print(f"CSS line_h (fs * 1.12): {line_h}")
print(f"CSS text block center: (box_h - line_h) / 2")

# Render sample
img = Image.new("RGBA", (800, 120), (30, 30, 30, 255))
draw = ImageDraw.Draw(img)
# center horizontally
tw = bb[2] - bb[0]
tx = (800 - tw) // 2 - bb[0]
# center vertically using CSS line-height method
gy = int(round((120 - line_h) / 2.0 + (line_h - (ascent + descent)) / 2.0))
draw.text((tx, gy), SAMPLE_TEXT, font=font, fill=(255, 255, 255, 255))
img.save(Path(__file__).with_name("diag_font_pil.png"))
print(f"Saved diag_font_pil.png")
print()
print("Compare this with browser rendering of the same text at 48px bold VC Noto Sans")
