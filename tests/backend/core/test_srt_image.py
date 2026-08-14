from pathlib import Path

from pipeline.srt_image import (
    _ffmpeg_subtitle,
    _log,
    _logo_position,
    _text_logo_filter,
    _text_logo_position,
    create_job,
    image_resolution,
    is_video,
    parse_srt_times,
    parse_timeline_times,
    shift_srt,
)


def test_parse_srt_times(tmp_path):
    srt = tmp_path / "sample.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\nMột\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\nHai\n",
        encoding="utf-8",
    )
    assert parse_srt_times(srt) == [(0.0, 1.5), (1.5, 3.0)]


def test_shift_srt_and_subtitle_style(tmp_path):
    source = tmp_path / "sample.srt"
    output = tmp_path / "shifted.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,500\nMột câu bị\nxuống dòng sớm\n",
        encoding="utf-8",
    )

    shift_srt(source, output, -1.5)

    assert "00:00:00,000 --> 00:00:01,000" in output.read_text(encoding="utf-8")
    assert "Một câu bị xuống dòng sớm" in output.read_text(encoding="utf-8")
    subtitle_filter = _ffmpeg_subtitle(output, 12, 24, True)
    assert "FontSize=12" in subtitle_filter
    assert "MarginV=24" in subtitle_filter
    assert "BorderStyle=3" in subtitle_filter
    assert "OutlineColour=&H60000000" in subtitle_filter
    assert "MarginL=20,MarginR=20" in subtitle_filter


def test_job_log_keeps_recent_lines(tmp_path):
    job = create_job(
        "test.mp4", tmp_path, [], None, tmp_path / "timeline.txt",
        tmp_path / "subtitles.srt", {},
    )
    for index in range(205):
        _log(job["id"], f"line {index}")
    from pipeline.srt_image import get_job
    logs = get_job(job["id"])["logs"]
    assert len(logs) == 200
    assert logs[-1].endswith("line 204")


def test_text_logo_uses_explicit_windows_font(monkeypatch, tmp_path):
    import pipeline.srt_image as module
    windows = tmp_path / "Windows"
    font = windows / "Fonts" / "arial.ttf"
    font.parent.mkdir(parents=True)
    font.touch()
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", str(windows))

    result = _text_logo_filter({"source": "text", "text": "ZMTOOL"}, 1080)

    assert "fontfile=" in result
    assert "arial" in result


def test_text_logo_position_uses_text_dimensions():
    fixed = _text_logo_position(88, 88)
    moving = _text_logo_position(88, 88, True)
    assert fixed == (
        "min(W-tw,max(0,W*0.8800))",
        "min(H-th,max(0,H*0.8800))",
    )
    assert "W-tw" in moving[0]
    assert "-th" in moving[1]


def test_random_text_logo_uses_standard_show_hide_cycle():
    result = _text_logo_filter({
        "source": "text", "text": "ZMTOOL", "motion": "random",
        "visibleSec": 4, "hiddenSec": 2, "fadeSec": 0.5,
        "safeMargin": 4, "fontSize": 42, "color": "#ffd166",
    }, 1080)
    assert "floor(t/6.000)" in result
    assert "mod(t,6.000)" in result
    assert "fontcolor=#ffd166" in result
    assert "shadowcolor=black@0.85" in result


def test_parse_timeline_times(tmp_path):
    timeline = tmp_path / "prompts.txt"
    timeline.write_text(
        "001_[00:00.000-00:01.500] cảnh một\n"
        "002_[00:01.500-00:03.000] cảnh hai\n",
        encoding="utf-8",
    )
    assert parse_timeline_times(timeline) == [(0.0, 1.5), (1.5, 3.0)]


def test_parse_dot_timecode_timeline(tmp_path):
    timeline = tmp_path / "video-prompts.txt"
    timeline.write_text(
        "001_[00.00.00.00-00.00.04.00] clip one\n\n"
        "002_[00.00.04.00-00.00.10.00] clip two\n",
        encoding="utf-8",
    )
    assert parse_timeline_times(timeline) == [(0.0, 4.0), (4.0, 10.0)]


def test_image_resolution_is_even(monkeypatch, tmp_path):
    class Result:
        stdout = '{"streams":[{"width":1921,"height":1081}]}'

    monkeypatch.setattr("pipeline.srt_image.subprocess.run", lambda *args, **kwargs: Result())
    assert image_resolution(tmp_path / "image.jpg") == (1920, 1080)


def test_logo_position():
    assert "W*0.0000" in _logo_position(0, 0)[0]
    assert "sin(t" in _logo_position(moving=True)[0]


def test_video_extensions():
    assert is_video(Path("001.MP4"))
    assert is_video(Path("002.webm"))
    assert not is_video(Path("003.png"))
    assert not is_video(Path("004.jfif"))


def test_create_job_uses_selected_output(tmp_path):
    selected = tmp_path / "my-video.mp4"
    job = create_job(
        selected.name, tmp_path, [], None, tmp_path / "timeline.txt",
        tmp_path / "subtitles.srt", {}, output_target=selected,
    )
    assert job["output"] == str(selected)
