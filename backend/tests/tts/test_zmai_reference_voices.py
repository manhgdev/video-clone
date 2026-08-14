from __future__ import annotations

import os
from pathlib import Path

import pytest

from pipeline.tts.engines import vieneu
from pipeline.tts import voice_store


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def encode_reference(self, path: Path, denoise: bool = False):
        self.calls += 1
        return f"embedding-{self.calls}", f"codes-{self.calls}"


def test_reference_voice_list_excludes_missing_audio(tmp_path, monkeypatch) -> None:
    existing = tmp_path / "ready.wav"
    existing.write_bytes(b"wav")
    monkeypatch.setattr(voice_store, "REFERENCE_ROOT", tmp_path)
    monkeypatch.setattr(
        voice_store,
        "_read_reference_raw",
        lambda: [
            {"id": "ready", "engine": "vieneu", "type": "zmAI", "ref_file": "ready.wav"},
            {"id": "missing", "engine": "vieneu", "type": "zmAI", "ref_file": "missing.wav"},
        ],
    )

    assert [item["id"] for item in voice_store.load_reference_voices()] == ["ready"]


def test_reference_encode_cache_refreshes_when_wav_changes(tmp_path, monkeypatch) -> None:
    ref = tmp_path / "voice.wav"
    ref.write_bytes(b"first")
    entry = {"id": "voice", "name": "Voice", "ref_file": ref.name}
    monkeypatch.setattr(vieneu.voice_store, "get_reference_voice", lambda _: entry)
    monkeypatch.setattr(vieneu.voice_store, "reference_path", lambda _: ref)
    vieneu._reference_cache.clear()
    client = _FakeClient()

    assert vieneu._encoded_reference(client, "voice") == vieneu._encoded_reference(client, "voice")
    assert client.calls == 1

    ref.write_bytes(b"changed-reference")
    os.utime(ref, None)
    vieneu._encoded_reference(client, "voice")
    assert client.calls == 2


def test_missing_reference_is_explicit_and_never_falls_back(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing.wav"
    entry = {"id": "voice", "name": "Voice", "ref_file": missing.name}
    monkeypatch.setattr(vieneu.voice_store, "get_reference_voice", lambda _: entry)
    monkeypatch.setattr(vieneu.voice_store, "reference_path", lambda _: missing)

    with pytest.raises(RuntimeError, match="Thiếu file reference"):
        vieneu._encoded_reference(_FakeClient(), "voice")
