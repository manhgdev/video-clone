import json
from pathlib import Path

from api.routes.download import _copy_job_subtitles
from pipeline.download import ytdlp_jobs
from pipeline.download.ytdlp_jobs import _json3_to_srt, _normalize_urls


def test_normalize_urls_splits_accidentally_concatenated_links() -> None:
    url = "https://www.youtube.com/shorts/7JjuXYxTY8Y"
    assert _normalize_urls(url + url) == [url]


def test_subtitle_probe_prefers_one_creator_caption_then_one_automatic_caption(monkeypatch) -> None:
    payload = {"subtitles": {"vi-orig": [{}], "en-US": [{}]}, "automatic_captions": {"fr": [{}]}}

    class Result:
        returncode = 0

        @property
        def stdout(self):
            return json.dumps(payload)

    monkeypatch.setattr(ytdlp_jobs.subprocess, "run", lambda *_args, **_kwargs: Result())
    assert ytdlp_jobs._platform_subtitle_selection("yt-dlp", "https://example.test") == (["vi-orig"], False)

    payload["subtitles"] = {}
    payload["automatic_captions"] = {"vi": [{}], "en": [{}]}
    assert ytdlp_jobs._platform_subtitle_selection("yt-dlp", "https://example.test") == (["vi"], True)


def test_json3_track_is_written_as_non_overlapping_srt(tmp_path) -> None:
    source = tmp_path / "video.vi.json3"
    source.write_text(json.dumps({"events": [
        {"tStartMs": 120, "dDurationMs": 3360, "segs": [{"utf8": "Line one"}]},
        {"tStartMs": 1760, "dDurationMs": 2824, "segs": [{"utf8": "[âm nhạc] Line two"}]},
    ]}), encoding="utf-8")
    target = _json3_to_srt(source)
    assert target is not None
    assert target.read_text(encoding="utf-8") == (
        "1\n00:00:00,120 --> 00:00:01,760\nLine one\n\n"
        "2\n00:00:01,760 --> 00:00:04,584\nLine two\n"
    )


def test_copy_job_subtitles_keeps_download_source_label(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    project = tmp_path / "project"
    job_dir.mkdir()
    (job_dir / "captions.vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    copied = _copy_job_subtitles(project, job_dir)
    assert copied == [{"name": "captions.vi.srt", "label": "Từ Download · captions.vi.srt", "origin": "download"}]
    assert (project / "subtitles" / "captions.vi.srt").is_file()
