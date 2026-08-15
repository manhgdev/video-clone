from pipeline.ocr.extract_parts.merge import _drop_non_caption_branding


def test_branding_marks_are_not_subtitle_segments():
    segments = [
        {"index": 1, "source": "AI生成+", "start": 0.0, "end": 1.0},
        {"index": 2, "source": "@抖音岚白动漫", "start": 1.0, "end": 2.0},
        {"index": 3, "source": "这么大排场 @抖音岚白动漫", "start": 2.0, "end": 3.0},
    ]

    filtered = _drop_non_caption_branding(segments)

    assert len(filtered) == 1
    assert filtered[0]["source"] == "这么大排场"
    assert filtered[0]["index"] == 1
