from pathlib import Path
import unittest

from PIL import ImageFont
from pydantic import ValidationError

from api.deps import SegmentIn
from pipeline.export import fonts
from pipeline.export.burn_parts.layout_text import (
    _layout_caption,
    _layout_caption_over,
    _layout_caption_vertical,
)


EXPECTED = {
    "system": "NotoSans-Bold.ttf",
    "segoe": "Inter-Bold.ttf",
    "arial": "Arimo-Bold.ttf",
    "bold": "ArchivoBlack-Regular.ttf",
    "helvetica": "Roboto-Bold.ttf",
    "verdana": "OpenSans-Bold.ttf",
    "tahoma": "Carlito-Bold.ttf",
    "trebuchet": "FiraSans-Bold.ttf",
    "rounded": "Nunito-Bold.ttf",
    "impact": "Anton-Regular.ttf",
    "georgia": "Merriweather-Bold.ttf",
    "times": "Tinos-Bold.ttf",
    "palatino": "Literata-Bold.ttf",
    "garamond": "EBGaramond-Bold.ttf",
    "courier": "CourierPrime-Bold.ttf",
    "mono": "NotoSansMono-Bold.ttf",
    "comic": "ComicNeue-Bold.ttf",
    "cjk": "NotoSansSC-Bold.ttf",
    "meiryo": "NotoSansJP-Bold.ttf",
    "malgun": "NotoSansKR-Bold.ttf",
}


class BundledCaptionFontTest(unittest.TestCase):
    def test_every_caption_preset_resolves_to_its_bundled_font(self) -> None:
        fonts._font_file_index = None
        fonts._font_cache.clear()
        bundled = (Path(__file__).parents[3] / "frontend" / "public" / "fonts").resolve()

        for preset, filename in EXPECTED.items():
            resolved = Path(fonts._font_for_preset(preset))
            self.assertEqual(resolved.name, filename)
            self.assertEqual(resolved.parent, bundled)
            self.assertGreater(
                ImageFont.truetype(resolved, 32).getbbox("Phụ đề 日本 한국 中文")[2],
                0,
            )
            self.assertTrue(
                fonts._font_covers_text(
                    str(resolved),
                    "Hãy đến và đập nát tất cả túi ngô nhỏ này",
                ),
                f"{preset} maps Vietnamese glyphs to a missing/question glyph",
            )

    def test_layout_fallbacks_keep_the_selected_font(self) -> None:
        selected = fonts._font_for_preset("comic")
        font = ImageFont.truetype(selected, 48)
        text = "Hãy đến và đập nát tất cả túi ngô nhỏ này"

        regular = _layout_caption(
            text, font, 48, (80, 900, 1000, 1010), 1080, 1920,
        )
        over, _cover = _layout_caption_over(
            text,
            48,
            (80, 900, 1000, 1010),
            1080,
            1920,
            font_path=selected,
        )
        vertical = _layout_caption_vertical(
            "Hãy đến", font, 48, (20, 200, 180, 800), 1080, 1920,
        )

        for layout in (regular, over, vertical):
            self.assertEqual(Path(layout["font"].path).resolve(), Path(selected).resolve())

    def test_missing_preset_falls_back_to_bundled_noto_not_host_font(self) -> None:
        fonts._font_file_index = None
        fonts._font_cache.clear()
        fallback = Path(fonts._font_for_preset("missing-preset"))

        self.assertEqual(fallback.name, "NotoSans-Bold.ttf")
        self.assertEqual(fallback.parent.name, "fonts")
        self.assertNotEqual(str(fallback), "Arial")

    def test_segment_caption_style_fields_are_validated(self) -> None:
        base = dict(id="s1", index=0, start=0, end=1, source="", translation="", voice="")
        segment = SegmentIn(**base, fontFamily="rounded", textColor="#12aBcF")
        self.assertEqual(segment.model_dump()["fontFamily"], "rounded")
        self.assertEqual(segment.model_dump()["textColor"], "#12aBcF")

        with self.assertRaises(ValidationError):
            SegmentIn(**base, fontFamily="../font.ttf", textColor="red")
