from pipeline.srt_export import _caption_cues, _styled


def test_caption_input_keeps_timecodes_and_styles(tmp_path):
    source = tmp_path / "caption.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:05,000\nMột câu phụ đề đủ dài để được tách hợp lý.\n", encoding="utf-8")

    cues = _caption_cues(source)
    styled = _styled(cues, "v916")

    assert cues[0]["start"] == 1.0
    assert cues[0]["end"] == 5.0
    assert styled
    assert styled[0]["start"] == 1.0
    assert styled[-1]["end"] <= 5.0
