from pathlib import Path

from pipeline.subtitles import split_portrait_caption_segments, subtitle_segments


def test_subtitle_segments_keeps_srt_timestamps_and_preview(tmp_path: Path):
    source = tmp_path / "captions.vi.srt"
    source.write_text(
        "1\n00:00:00,120 --> 00:00:03,480\nHello\n\n2\n00:00:04,000 --> 00:00:06,000\nWorld\n",
        encoding="utf-8",
    )

    segments = subtitle_segments(source, preview_sec=4)
    assert len(segments) == 1
    assert {key: segments[0][key] for key in ("index", "start", "end", "source", "translation", "voice")} == {
        "index": 0, "start": 0.12, "end": 3.48, "source": "Hello", "translation": "", "voice": ""
    }


def test_subtitle_segments_preserves_supplied_cues_verbatim(tmp_path: Path):
    source = tmp_path / "youtube.vi.srt"
    source.write_text(
        "1\n00:00:00,120 --> 00:00:01,750\nKhi anh chàng này nhận ra rằng hai nam\n\n"
        "2\n00:00:01,760 --> 00:00:03,470\nKhi anh chàng này nhận ra rằng hai nam châm có thể đẩy nhau\n\n"
        "3\n00:00:03,480 --> 00:00:05,920\nchâm có thể đẩy nhau không tận dụng chính lực đẩy này\n",
        encoding="utf-8",
    )
    assert [x["source"] for x in subtitle_segments(source)] == [
        "Khi anh chàng này nhận ra rằng hai nam",
        "Khi anh chàng này nhận ra rằng hai nam châm có thể đẩy nhau",
        "châm có thể đẩy nhau không tận dụng chính lực đẩy này",
    ]


def test_portrait_caption_split_keeps_text_and_cue_timeline():
    source = "Khi anh chàng này nhận ra rằng hai nam châm có thể đẩy nhau."
    rows = split_portrait_caption_segments([
        {"id": "one", "index": 0, "start": 1.0, "end": 4.0, "source": source, "voice": "v"}
    ])

    assert len(rows) >= 2
    # A final one-word fragment is merged into its neighbour (up to 8 words)
    # instead of creating an unreadable one-word card.
    assert all(len(row["source"].split()) <= 8 for row in rows)
    assert " ".join(row["source"] for row in rows) == source
    assert rows[0]["start"] == 1.0
    assert rows[-1]["end"] == 4.0
    assert all(rows[index]["end"] == rows[index + 1]["start"] for index in range(len(rows) - 1))


def test_portrait_caption_split_never_leaves_a_one_word_cue():
    rows = split_portrait_caption_segments([
        {
            "id": "one", "index": 0, "start": 0, "end": 3,
            "source": "Một hai ba bốn năm sáu bảy tám chín", "voice": "",
        }
    ])

    assert all(len(row["source"].split()) > 1 for row in rows)
