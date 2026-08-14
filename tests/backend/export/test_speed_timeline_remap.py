from pathlib import Path

from pipeline.core import media
from pipeline.core.media import remap_timeline_for_speed_change
from pipeline.export.burn_parts.layout_text import _caption_overlay, _preview_caption_layout
from pipeline.export.fonts import _font_for_preset
from pipeline.export.mux_audio import _tts_clip_plan


def test_system_caption_font_is_bundled_and_vietnamese_ready():
    from PIL import ImageFont

    path = Path(_font_for_preset("system"))
    assert path.name == "NotoSans-Bold.ttf"
    font = ImageFont.truetype(str(path), 48)
    assert font.getlength("Trong chuyến đi này, tôi mất") > 0
    assert font.getlength("trung bình 1.000 đôi loại 56") > 0


def test_rebake_uses_latest_tts_caption_and_overlay_content():
    meta = {
        "duration": 20,
        "segments": [{
            "id": "s1", "start": 2, "end": 6, "coverStart": 2.5, "coverEnd": 5.5,
            "translation": "old", "audioUrl": "/old.wav",
            "compoundChildren": [{"id": "c1", "start": 1, "end": 2}],
        }],
        "overlays": [{"id": "o1", "start": 2, "end": 5, "text": "old"}],
    }
    remap_timeline_for_speed_change(meta, 1, 2)

    # Edits made after the first bake must become the new source of truth.
    meta["segments"][0].update(
        translation="new caption", audioUrl="/new.wav", audioDuration=3.25
    )
    meta["overlays"][0]["text"] = "new text"
    remap_timeline_for_speed_change(meta, 2, 1)

    segment = meta["segments"][0]
    assert (segment["start"], segment["end"]) == (2, 6)
    assert (segment["coverStart"], segment["coverEnd"]) == (2.5, 5.5)
    assert (segment["translation"], segment["audioUrl"], segment["audioDuration"]) == (
        "new caption", "/new.wav", 3.25
    )
    assert segment["compoundChildren"][0]["start"] == 1
    assert meta["overlays"][0]["text"] == "new text"
    assert (meta["overlays"][0]["start"], meta["overlays"][0]["end"]) == (2, 5)


def test_speed_roundtrip_from_immutable_baseline():
    """0.80 → 1.15 → 1.00 → 0.80 phải về đúng mốc 0.80 ban đầu (không cascade)."""
    meta = {
        "duration": 10.0,
        "previewSec": 10,
        "segments": [
            {"id": "a", "start": 0.0, "end": 4.0, "translation": "một"},
            {"id": "b", "start": 4.0, "end": 10.0, "translation": "hai", "coverStart": 4.5, "coverEnd": 9.5},
        ],
        "overlays": [{"id": "o1", "start": 1.0, "end": 3.0, "text": "logo"}],
    }
    # Lần 1: 1.00 → 0.80
    remap_timeline_for_speed_change(meta, 1.0, 0.8)
    assert abs(meta["segments"][0]["end"] - 5.0) < 1e-6
    assert abs(meta["segments"][1]["end"] - 12.5) < 1e-6
    assert abs(float(meta["workDuration"]) - 12.5) < 1e-6
    snap_080 = {
        "segs": [(s["start"], s["end"]) for s in meta["segments"]],
        "ov": (meta["overlays"][0]["start"], meta["overlays"][0]["end"]),
        "wd": float(meta["workDuration"]),
    }
    baseline_id = id(meta.get("timelineBaseline"))

    # 0.80 → 1.15
    remap_timeline_for_speed_change(meta, 0.8, 1.15)
    assert abs(meta["segments"][0]["end"] - (4.0 / 1.15)) < 1e-6
    assert id(meta.get("timelineBaseline")) == baseline_id  # baseline không bị ghi đè

    # 1.15 → 1.00
    remap_timeline_for_speed_change(meta, 1.15, 1.0)
    assert abs(meta["segments"][0]["end"] - 4.0) < 1e-6
    assert abs(meta["segments"][1]["end"] - 10.0) < 1e-6

    # 1.00 → 0.80 lại = đúng snap_080
    remap_timeline_for_speed_change(meta, 1.0, 0.8)
    for i, (st, en) in enumerate(snap_080["segs"]):
        assert abs(meta["segments"][i]["start"] - st) < 1e-9
        assert abs(meta["segments"][i]["end"] - en) < 1e-9
    assert abs(meta["overlays"][0]["start"] - snap_080["ov"][0]) < 1e-9
    assert abs(meta["overlays"][0]["end"] - snap_080["ov"][1]) < 1e-9
    assert abs(float(meta["workDuration"]) - snap_080["wd"]) < 1e-9

    # 100 lần 0.80 ↔ 1.15 không drift
    for _ in range(100):
        remap_timeline_for_speed_change(meta, 0.8, 1.15)
        remap_timeline_for_speed_change(meta, 1.15, 0.8)
    for i, (st, en) in enumerate(snap_080["segs"]):
        assert abs(meta["segments"][i]["start"] - st) < 1e-9
        assert abs(meta["segments"][i]["end"] - en) < 1e-9


def test_same_speed_noop():
    meta = {
        "duration": 10.0,
        "segments": [{"id": "a", "start": 0.0, "end": 10.0, "translation": "x"}],
    }
    remap_timeline_for_speed_change(meta, 1.0, 0.8)
    before = (meta["segments"][0]["start"], meta["segments"][0]["end"], meta.get("workDuration"))
    remap_timeline_for_speed_change(meta, 0.8, 0.8)
    assert (meta["segments"][0]["start"], meta["segments"][0]["end"], meta.get("workDuration")) == before


def test_retime_video_segments_maps_tts_speed_to_export_clock(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(media, "ffprobe_duration", lambda _path: 10.0)
    monkeypatch.setattr(media, "_has_audio_stream", lambda _path: False)

    def fake_run(_project_id, command):
        Path(command[-1]).touch()

    monkeypatch.setattr(media, "run_cmd", fake_run)
    out, remapped = media.retime_video_segments(
        video,
        [
            {"id": "slow", "start": 2, "end": 4, "videoSpeed": 0.5},
            {"id": "fast", "start": 6, "end": 8, "videoSpeed": 2.0},
        ],
        tmp_path / "cache",
        "test",
    )

    assert out.name.startswith("retimed_")
    assert [(round(s["start"], 3), round(s["end"], 3)) for s in remapped] == [
        (2.0, 6.0),
        (8.0, 9.0),
    ]
    assert all("videoSpeed" not in s for s in remapped)


def test_retime_video_segments_applies_soft_preview_rate_to_gaps(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(media, "ffprobe_duration", lambda _path: 4.0)
    monkeypatch.setattr(media, "_has_audio_stream", lambda _path: False)
    monkeypatch.setattr(media, "run_cmd", lambda _project_id, command: Path(command[-1]).touch())

    _out, remapped = media.retime_video_segments(
        video,
        [{"id": "s1", "start": 1, "end": 2}],
        tmp_path / "cache",
        "test",
        base_speed=0.8,
    )

    # Every source second is 1/0.8 seconds on the export clock.
    assert (round(remapped[0]["start"], 3), round(remapped[0]["end"], 3)) == (1.25, 2.5)
    assert media.retime_timeline_time(
        3.0,
        4.0,
        [{"start": 1, "end": 2}],
        base_speed=0.8,
    ) == 3.75


def test_retime_prefers_mid_lane_when_ocr_segments_overlap(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(media, "ffprobe_duration", lambda _path: 6.0)
    monkeypatch.setattr(media, "_has_audio_stream", lambda _path: False)
    monkeypatch.setattr(media, "run_cmd", lambda _project_id, command: Path(command[-1]).touch())

    _out, remapped = media.retime_video_segments(
        video,
        [
            {"id": "vertical", "layout": "vertical", "start": 0, "end": 6, "videoSpeed": 1},
            {"id": "mid", "layout": "mid", "start": 2, "end": 4, "videoSpeed": 0.5},
        ],
        tmp_path / "cache",
        "test",
    )

    # Preview picks Mid over the long vertical OCR lane: 2 seconds become 4.
    by_id = {s["id"]: s for s in remapped}
    assert (round(by_id["vertical"]["start"], 3), round(by_id["vertical"]["end"], 3)) == (0.0, 8.0)
    assert (round(by_id["mid"]["start"], 3), round(by_id["mid"]["end"], 3)) == (2.0, 6.0)


def test_preview_caption_layout_does_not_expand_committed_box():
    from PIL import ImageFont

    font = ImageFont.load_default()
    layout = _preview_caption_layout(
        {
            "layout": "horizontal",
            "captionLayout": {
                "x": 20,
                "y": 30,
                "w": 12,
                "h": 14,
                "lines": ["a deliberately wider line"],
                "fontSize": 20,
            },
        },
        20,
        lambda _size: font,
    )

    assert layout is not None
    assert layout["box"] == (20, 30, 32, 44)


def test_mid_cover_uses_frontend_flex_center_without_shadow():
    from PIL import ImageFont

    font = ImageFont.truetype(_font_for_preset("system"), 48)
    layout = _preview_caption_layout(
        {
            "captionLayout": {
                "x": 0,
                "y": 0,
                "w": 903,
                "h": 177,
                "lines": ["first line", "second line", "third line"],
                "fontSize": 48,
            },
        },
        48,
        lambda _size: font,
    )
    assert layout is not None
    layout.update({"css_cover_mode": "mid", "stroke": True})

    rgba, x, y = _caption_overlay(layout)
    alpha = rgba[:, :, 3]
    ys, _xs = (alpha > 180).nonzero()

    # Overlay may extend past the box to preserve accents/descenders, but its
    # source-coordinate ink placement must still match the frontend flex box.
    assert x <= 0 and y <= 0
    assert x + rgba.shape[1] >= 903 and y + rgba.shape[0] >= 177
    assert 15 <= y + int(ys.min()) <= 25
    assert not ((rgba[:, :, :3] == 0).all(axis=2) & (alpha > 0)).any()


def test_external_stem_uses_same_retimed_clock(tmp_path, monkeypatch):
    audio = tmp_path / "stem.wav"
    audio.write_bytes(b"stem")
    monkeypatch.setattr(media, "ffprobe_duration", lambda _path: 4.0)
    monkeypatch.setattr(media, "run_cmd", lambda _project_id, command: Path(command[-1]).touch())

    out = media.retime_audio_track(
        audio,
        [{"id": "s1", "start": 1, "end": 2, "videoSpeed": 0.5}],
        tmp_path / "cache",
        "test",
        base_speed=0.8,
    )

    assert out.exists()
    assert out.name.startswith("retimed_audio_")


def test_export_tts_keeps_preview_speed_and_full_words(tmp_path):
    tts = tmp_path / "tts"
    tts.mkdir()
    (tts / "a.wav").touch()
    (tts / "b.wav").touch()

    clips, factor = _tts_clip_plan(
        [
            {"id": "a", "start": 0, "end": 1, "audioDuration": 2.0},
            {"id": "b", "start": 1, "end": 2, "audioDuration": 1.0},
        ],
        tmp_path,
        allow_video_slowdown=False,
        match="natural",
    )

    assert factor == 1.0
    assert clips[0][2] >= 2.0
    assert clips[0][3] == 1.0
    assert clips[1][1] >= clips[0][1] + clips[0][2]


def test_export_tts_uses_same_baked_speed_as_preview(tmp_path):
    tts = tmp_path / "tts"
    tts.mkdir()
    (tts / "a.wav").touch()

    clips, _factor = _tts_clip_plan(
        [{"id": "a", "start": 0, "end": 1, "audioDuration": 2.0, "ttsSpeed": 1.1}],
        tmp_path,
        allow_video_slowdown=False,
        match="preferVideo",
        bake_speed=1.15,
    )

    assert clips[0][3] == 1.1 * 1.15
    assert clips[0][2] >= 2.0 / clips[0][3]


def test_export_retime_base_always_one_global_in_file_only(tmp_path):
    from pipeline.orchestrate.export_job import _export_retime_base, _timeline_is_final

    baked = tmp_path / "preview_30_s115.mp4"
    baked.write_bytes(b"x")
    meta = {
        "bakedSpeed": 1.15,
        "bakedPreferVideo": True,
        "timelineClock": "display",
        "workVideo": str(baked),
        "settings": {"matchDuration": "preferVideo"},
    }
    # Global bake chỉ trong file — retime export base luôn 1.0
    assert _export_retime_base(meta, baked, "preferVideo") == 1.0
    assert _timeline_is_final(meta, baked) is True

    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    assert _export_retime_base({"settings": {}}, src, "preferVideo") == 1.0
    assert _timeline_is_final({"bakedSpeed": 1.0}, src) is True
    assert _timeline_is_final({}, src) is False
