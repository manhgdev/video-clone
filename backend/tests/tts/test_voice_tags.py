from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.tts import voice_store
from pipeline.tts.engines import vieneu


@pytest.fixture()
def isolated_tag_voices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ref_root = tmp_path / "voice-ref"
    cloned_root = tmp_path / "vieneu" / "cloned"
    ref_root.mkdir()
    cloned_root.mkdir(parents=True)
    ref_json = ref_root / "voices.json"
    clone_json = cloned_root.parent / "voices.json"
    (ref_root / "demo.wav").write_bytes(b"RIFF....WAVEfmt ")
    ref_json.write_text(
        json.dumps(
            [
                {
                    "id": "demo",
                    "name": "Demo",
                    "label": "Demo",
                    "type": "zmAI",
                    "engine": "vieneu",
                    "mode": "reference",
                    "ref_file": "demo.wav",
                    "hidden": False,
                    "tags": ["👨 Nam", "unknown", "👨 Nam"],
                    "custom": "preserve-me",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    clone_json.write_text('{"version": 1, "cloned": []}', encoding="utf-8")
    monkeypatch.setattr(voice_store, "REFERENCE_ROOT", ref_root)
    monkeypatch.setattr(voice_store, "REFERENCE_VOICES_JSON", ref_json)
    monkeypatch.setattr(voice_store, "VIENEU_ROOT", cloned_root.parent)
    monkeypatch.setattr(voice_store, "CLONED_DIR", cloned_root)
    monkeypatch.setattr(voice_store, "VOICES_JSON", clone_json)
    monkeypatch.setattr(voice_store, "ensure_vieneu_dirs", lambda: cloned_root.parent)
    return ref_root


def test_tag_normalization_rejects_requests_and_filters_legacy():
    assert voice_store.normalize_voice_tags(["👨 Nam", "👨 Nam", "📢 Quảng cáo"], strict=True) == [
        "👨 Nam",
        "📢 Quảng cáo",
    ]
    with pytest.raises(ValueError, match="Tag không hợp lệ"):
        voice_store.normalize_voice_tags(["Kể chuyện"], strict=True)
    assert voice_store.normalize_voice_tags(["👩 Nữ", "legacy"]) == ["👩 Nữ"]


def test_clone_and_reference_edits_save_tags(isolated_tag_voices: Path):
    clone = voice_store.add_cloned(
        "clone-demo",
        "Clone Demo",
        "cloned/clone-demo.wav",
        tags=["👩 Nữ", "⭐ Review"],
    )
    assert clone["tags"] == ["👩 Nữ", "⭐ Review"]

    reference = voice_store.update_reference("demo", tags=["🏔️ Miền Bắc", "📰 Tin tức"])
    assert reference and reference["tags"] == ["🏔️ Miền Bắc", "📰 Tin tức"]
    saved = json.loads((isolated_tag_voices / "voices.json").read_text(encoding="utf-8"))
    assert saved[0]["tags"] == ["🏔️ Miền Bắc", "📰 Tin tức"]


def test_tags_and_metadata_survive_round_trip_and_listing(isolated_tag_voices: Path, monkeypatch):
    # Legacy unknown tag is safely removed on load before the first move.
    assert voice_store.get_reference_voice("demo")["tags"] == ["👨 Nam"]
    moved = voice_store.move_voice_engine("demo", "clone")
    assert moved["tags"] == ["👨 Nam"]
    clone = voice_store.load_cloned()[0]
    assert clone["tags"] == ["👨 Nam"]
    assert clone["custom"] == "preserve-me"

    back = voice_store.move_voice_engine(moved["id"], "zmai")
    reference = voice_store.get_reference_voice(back["id"])
    assert reference and reference["tags"] == ["👨 Nam"]
    assert reference["custom"] == "preserve-me"

    monkeypatch.setattr(vieneu, "available", lambda: True)
    monkeypatch.setattr(vieneu, "list_preset_from_assets", lambda: [])
    listed = vieneu.list_voices()
    assert listed[0]["tags"] == ["👨 Nam"]
