"""Self-check: rename/delete zmAI + move zmAI ↔ clone."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.tts import voice_store


@pytest.fixture()
def isolated_voices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ref_root = tmp_path / "voice-ref"
    ref_root.mkdir()
    vieneu = tmp_path / "vieneu"
    cloned = vieneu / "cloned"
    cloned.mkdir(parents=True)
    (vieneu / "voices.json").write_text(
        json.dumps({"version": 1, "cloned": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    wav = ref_root / "demo.wav"
    wav.write_bytes(b"RIFF....WAVEfmt ")
    voices_json = ref_root / "voices.json"
    voices_json.write_text(
        json.dumps(
            [
                {
                    "id": "demo",
                    "name": "Demo Voice",
                    "label": "Demo Voice",
                    "type": "zmAI",
                    "engine": "vieneu",
                    "mode": "reference",
                    "ref_file": "demo.wav",
                    "hidden": False,
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(voice_store, "REFERENCE_ROOT", ref_root)
    monkeypatch.setattr(voice_store, "REFERENCE_VOICES_JSON", voices_json)
    monkeypatch.setattr(voice_store, "VIENEU_ROOT", vieneu)
    monkeypatch.setattr(voice_store, "CLONED_DIR", cloned)
    monkeypatch.setattr(voice_store, "VOICES_JSON", vieneu / "voices.json")
    monkeypatch.setattr(voice_store, "ensure_vieneu_dirs", lambda: vieneu)
    return ref_root, cloned


def test_rename_and_move_zmai_to_clone(isolated_voices):
    renamed = voice_store.rename_reference("demo", "Demo Renamed")
    assert renamed and renamed["name"] == "Demo Renamed"

    moved = voice_store.move_voice_engine("demo", "clone")
    assert moved["engine"] == "clone"
    assert moved["id"].startswith("vn:clone:")
    assert voice_store.get_reference_voice("demo") is None
    assert len(voice_store.load_cloned()) == 1

    back = voice_store.move_voice_engine(moved["id"], "zmai")
    assert back["engine"] == "zmai"
    assert voice_store.get_reference_voice(back["id"]) is not None
    assert voice_store.load_cloned() == []


def test_soft_delete_zmai(isolated_voices):
    assert voice_store.remove_reference("demo") is True
    assert voice_store.get_reference_voice("demo") is None
    # WAV vẫn còn
    assert (isolated_voices[0] / "demo.wav").is_file()


def test_bulk_move_reports_partial_failure_and_keeps_distinct_ids(isolated_voices):
    ref_root, _ = isolated_voices
    (ref_root / "demo-2.wav").write_bytes(b"RIFF....WAVEfmt ")
    items = json.loads((ref_root / "voices.json").read_text(encoding="utf-8"))
    items.append(
        {
            **items[0],
            "id": "demo-2",
            "name": "Demo Voice",
            "ref_file": "demo-2.wav",
        }
    )
    (ref_root / "voices.json").write_text(json.dumps(items), encoding="utf-8")

    result = voice_store.move_voice_engines(["demo", "missing", "demo-2"], "clone")

    assert [item["voiceId"] for item in result["successes"]] == ["demo", "demo-2"]
    assert result["failures"][0]["voiceId"] == "missing"
    moved_ids = [item["voice"]["id"] for item in result["successes"]]
    assert len(set(moved_ids)) == 2
    assert len(voice_store.load_cloned()) == 2
