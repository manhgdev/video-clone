import unittest

from pipeline.ocr.translate_track import _align_cues_to_caption_track


class OcrCaptionTrackTests(unittest.TestCase):
    def test_uses_main_caption_timing_and_removes_overlap(self):
        captions = [
            {"start": 3.32, "end": 5.47, "source": "觉醒SS级天赋断界线"},
            {"start": 5.50, "end": 7.47, "source": "我靠！SS级"},
        ]
        ocr = [
            {"start": 3.0, "end": 7.0, "source": "觉醒SS级天赋断界线"},
            {"start": 4.82, "end": 7.47, "source": "渝州市宽师一年学学華动大学"},
            {"start": 5.50, "end": 7.47, "source": "我靠！SS级"},
        ]

        result = _align_cues_to_caption_track(ocr, captions)

        self.assertEqual([(item["start"], item["end"]) for item in result], [(3.32, 5.47), (5.50, 7.47)])
        self.assertEqual([item["source"] for item in result], ["觉醒SS级天赋断界线", "我靠！SS级"])
        self.assertLessEqual(result[0]["end"], result[1]["start"])


if __name__ == "__main__":
    unittest.main()
