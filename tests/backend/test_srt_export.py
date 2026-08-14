import zipfile

from pipeline.srt_export import _caption_cues, _pick_platform_language, _styled, _translated_cues, _write_outputs, _zip_outputs


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


def test_platform_prefers_creator_caption_and_requested_language():
    info = {"subtitles": {"en-US": [{}]}, "automatic_captions": {"vi": [{}]}}
    assert _pick_platform_language(info, "en") == ("en-US", "phụ đề có sẵn")
    assert _pick_platform_language(info, "vi") == ("vi", "phụ đề tự động")


def test_bilingual_exports_two_separate_sets(tmp_path):
    cues = [{"start": 1.0, "end": 2.0, "text": "Hello"}]
    translated = _translated_cues(cues, ["Xin chào"])
    source_files = _write_outputs(tmp_path, cues, "subtitles-source")
    translated_files = _write_outputs(tmp_path, translated, "subtitles-vi")
    assert "subtitles-source-hard.srt" in source_files
    assert "subtitles-vi-hard.srt" in translated_files
    assert "Hello\nXin chào" not in (tmp_path / "subtitles-vi-hard.srt").read_text(encoding="utf-8-sig")
    _zip_outputs(tmp_path, source_files + translated_files, bilingual=True, target_lang="vi")
    with zipfile.ZipFile(tmp_path / "subtitles-all.zip") as archive:
        assert "phu-de-goc/subtitles-source-hard.srt" in archive.namelist()
        assert "ban-dich-vi/subtitles-vi-hard.srt" in archive.namelist()
