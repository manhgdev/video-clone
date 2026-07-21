from pipeline.core.media import remap_timeline_for_speed_change


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
