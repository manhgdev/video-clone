import zipfile

import httpx
import pytest

from pipeline.srt_export import _caption_cues, _pick_platform_language, _styled, _translated_cues, _write_outputs, _zip_outputs


def test_capcut_vod_transfer_ret_2000_is_success():
    """VOD upload APIs use 2000/Success rather than the editor API's 0."""
    from pipeline.capcut_stt import _json

    class Client:
        def request(self, *_args, **_kwargs):
            return httpx.Response(200, json={"ret": 2000, "errmsg": "Success"})

    assert _json(Client(), "POST", "https://upload.invalid", headers={}) == {
        "ret": 2000,
        "errmsg": "Success",
    }


def test_capcut_poll_progress_reports_state_elapsed_and_poll_count():
    from pipeline.capcut_stt import _poll_progress_message

    assert _poll_progress_message("queueing", 6.4, 4) == "CapCut: đang xếp hàng trên CapCut · đã chờ 6s · kiểm tra #4"
    assert "đang chờ phản hồi" in _poll_progress_message("", 0, 0)


def test_capcut_task_without_status_is_not_treated_as_running():
    from pipeline.capcut_stt import _task_status

    assert _task_status({"data": {"tasks": [{}]}}) == ({}, "")
    task, status = _task_status({"data": {"tasks": [{"status": "queueing"}]}})
    assert task == {"status": "queueing"}
    assert status == "queueing"
    assert _task_status({"data": {"tasks": [{"task_status": "completed"}]}})[1] == "success"
    assert _task_status({"data": {"tasks": [{"status": "succeed"}]}})[1] == "success"


def test_capcut_text_only_translation_is_not_silently_replaced_by_google():
    from pipeline.mt.api import translate_segments

    with pytest.raises(RuntimeError, match="CapCut cloud"):
        translate_segments(["hello"], "vi", translator="capcut")


def test_capcut_subtitle_payload_keeps_timestamps_and_translation():
    from pipeline.capcut_stt import subtitle_cues

    source, translated = subtitle_cues({"utterances": [{
        "text": "你好", "translation": "Xin chào", "start_time": 1250, "end_time": 3400,
    }]})
    assert source == [{"start": 1.25, "end": 3.4, "text": "你好"}]
    assert translated == [{"start": 1.25, "end": 3.4, "text": "Xin chào"}]


def test_capcut_live_translation_text_alias_is_preserved():
    from pipeline.capcut_stt import subtitle_cues

    source, translated = subtitle_cues({"utterances": [{
        "text": "你好", "translation_text": "Xin chào", "start_time": 0, "end_time": 1000,
    }]})
    assert source[0]["text"] == "你好"
    assert translated[0]["text"] == "Xin chào"


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
