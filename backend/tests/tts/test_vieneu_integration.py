"""Minimal VieNeu + studio tests — no full model download required for unit cases."""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from pipeline.export.srt import parse_srt, write_srt
from pipeline.tts.engines import vieneu as vieneu_engine
from pipeline.tts.manager import engines_status, list_voices, tts_cache_key
from pipeline.tts.schemas import PREFIX_VIENEU
from pipeline.tts.text_split import split_sentences
from pipeline.tts.voice_store import load_reference_voices


def test_text_split_long():
    text = "Câu một. " * 40
    parts = split_sentences(text, max_chars=80)
    assert len(parts) > 1
    assert all(len(p) <= 90 for p in parts)


def test_srt_roundtrip():
    cues = [
        {"start": 0.0, "end": 1.5, "text": "Xin chào"},
        {"start": 2.0, "end": 3.25, "text": "VieNeu TTS"},
    ]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.srt"
        write_srt(p, cues)
        raw = p.read_text(encoding="utf-8")
        assert "-->" in raw
        back = parse_srt(raw)
        assert len(back) == 2
        assert back[0]["text"] == "Xin chào"
        assert abs(back[1]["start"] - 2.0) < 0.01


def test_parse_voice_prefix():
    assert vieneu_engine.parse_voice("vn:Phạm Tuyên") == ("preset", "Phạm Tuyên")
    assert vieneu_engine.parse_voice("vn:clone:my_voice") == ("clone", "my_voice")
    assert vieneu_engine.parse_voice("cc:x:y") is None


def test_reference_voice_preview_path():
    refs = load_reference_voices()
    assert refs
    path = vieneu_engine.preview_path(str(refs[0]["id"]))
    assert path and path.is_file()
    assert vieneu_engine.preview_path("vn:not-a-real-preset") is None


def test_list_voices_no_crash():
    vs = list_voices("vi")
    assert isinstance(vs, list)
    # CapCut/system always present even without vieneu model load
    assert any(v["id"] == "system" or v["id"].startswith("cc:") for v in vs)


def test_engines_status_shape():
    st = engines_status()
    assert "vieneu" in st
    assert "capcut" in st
    assert "elevenlabs" in st
    assert "system" in st
    vn = st["vieneu"]
    assert "installed" in vn
    assert "message" in vn


def test_tts_cache_key_vieneu_differs():
    a = tts_cache_key("hello", f"{PREFIX_VIENEU}A", "vi", "none")
    b = tts_cache_key("hello", f"{PREFIX_VIENEU}B", "vi", "none")
    c = tts_cache_key("hello", "cc:x:y", "vi", "none")
    assert a != b
    assert a != c


def test_preset_assets_without_model_load():
    if not vieneu_engine.available():
        return
    presets = vieneu_engine.list_preset_from_assets()
    assert isinstance(presets, list)
    if presets:
        assert presets[0]["name"] and presets[0]["id"]
        assert presets[0]["language"] == "vi"


def test_list_voices_keeps_clones_out_of_presets(monkeypatch):
    class LiveClient:
        def list_preset_voices(self):
            return [("My Clone", "my-clone")]

    monkeypatch.setattr(vieneu_engine, "available", lambda: True)
    monkeypatch.setattr(vieneu_engine, "_client", LiveClient())
    monkeypatch.setattr(vieneu_engine.voice_store, "load_reference_voices", lambda: [])
    monkeypatch.setattr(
        vieneu_engine,
        "list_preset_from_assets",
        lambda: [{"id": "built-in", "name": "Built in"}],
    )
    monkeypatch.setattr(
        vieneu_engine.voice_store,
        "load_cloned",
        lambda: [{"id": "my-clone", "name": "My Clone"}],
    )

    ids = {voice["id"] for voice in vieneu_engine.list_voices()}
    assert "vn:built-in" in ids
    assert "vn:clone:my-clone" in ids
    assert "vn:my-clone" not in ids


def test_capcut_metadata_uses_provider_fields_only():
    from pipeline.tts.manager import _capcut_voice_metadata

    metadata = _capcut_voice_metadata(
        {
            "lang": "vi-VN",
            "voice_type": "multi_female_richgirl_uranus_bigtts",
            "display_name": "Review Phim",
        }
    )
    assert metadata == {"language": "vi", "gender": "female", "category": "review"}


def test_lazy_status_does_not_force_ready_loaded():
    if importlib.util.find_spec("vieneu") is None:
        return
    st = vieneu_engine.status()
    assert st["installed"] is True
    assert st.get("loadState") in ("cold", "ready", "loading", "error")


def test_studio_module_import():
    from pipeline.tts import studio

    assert callable(studio.synth_text_job)
    assert callable(studio.list_history)


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("PASS", name)
        except Exception as e:
            print("FAIL", name, e)
            failed += 1
    raise SystemExit(failed)
