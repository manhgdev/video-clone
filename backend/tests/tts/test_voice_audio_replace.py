from pipeline.tts import voice_store


def test_replace_cloned_voice_audio_in_place(tmp_path, monkeypatch):
    cloned = tmp_path / "cloned"
    cloned.mkdir()
    target = cloned / "adam.wav"
    target.write_bytes(b"old")
    source = tmp_path / "new.wav"
    source.write_bytes(b"new audio")

    monkeypatch.setattr(voice_store, "VIENEU_ROOT", tmp_path)
    monkeypatch.setattr(
        voice_store,
        "load_cloned",
        lambda: [{"id": "adam", "name": "Adam", "ref": "cloned/adam.wav"}],
    )
    monkeypatch.setattr(voice_store, "get_reference_voice", lambda _voice_id: None)

    result = voice_store.replace_voice_audio("vn:clone:adam", source)

    assert result["id"] == "adam"
    assert target.read_bytes() == b"new audio"
    assert list(cloned.iterdir()) == [target]
