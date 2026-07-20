from pathlib import Path

from pipeline.tts import voice_store
from pipeline.tts.engines import vieneu


def test_clone_registers_once_and_infers_by_id(tmp_path, monkeypatch):
    ref = tmp_path / "cloned" / "adam.wav"
    ref.parent.mkdir()
    ref.write_bytes(b"audio")
    monkeypatch.setattr(voice_store, "VIENEU_ROOT", tmp_path)
    monkeypatch.setattr(
        voice_store,
        "load_cloned",
        lambda: [{"id": "adam", "name": "Tên có thể đổi", "ref": "cloned/adam.wav"}],
    )
    monkeypatch.setattr(vieneu, "_clone_cache", {})

    class Client:
        def __init__(self):
            self.added = 0
            self.voices = []

        def add_voice(self, name, _path, **_kwargs):
            self.added += 1
            assert name == "adam"

        def infer(self, _text, **kwargs):
            self.voices.append(kwargs)
            return [0.0]

        def save(self, _audio, path):
            Path(path).write_bytes(b"wav")

    client = Client()
    monkeypatch.setattr(vieneu, "get_client", lambda: client)
    vieneu.synthesize("Một", "vn:clone:adam", tmp_path / "one.wav")
    vieneu.synthesize("Hai", "vn:clone:adam", tmp_path / "two.wav")

    assert client.added == 1
    assert [call["voice"] for call in client.voices] == ["adam", "adam"]
    assert all("ref_audio" not in call for call in client.voices)
