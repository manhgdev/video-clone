def test_remap_timeline_for_speed_change() -> None:
    from pipeline.core.media import remap_timeline_for_speed_change

    meta = {
        "segments": [{"start": 0.8, "end": 1.6, "coverStart": 0.7, "coverEnd": 1.7}],
        "overlays": [{"start": 0.8, "end": 1.6}],
    }
    remap_timeline_for_speed_change(meta, 0.8, 1.0)
    assert abs(meta["segments"][0]["start"] - 0.64) < 1e-9
    assert abs(meta["segments"][0]["end"] - 1.28) < 1e-9
    assert meta["segments"][0]["videoSpeed"] == 1.0
    assert abs(meta["overlays"][0]["start"] - 0.64) < 1e-9


if __name__ == "__main__":
    test_remap_timeline_for_speed_change()
    print("ok")
