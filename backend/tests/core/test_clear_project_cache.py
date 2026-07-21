"""clear_project_cache: selective parts + full wipe; keeps source video."""
from __future__ import annotations

import json
from pathlib import Path

import pipeline.core.project as project


def _setup(tmp_path, monkeypatch):
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(project, "PUBLIC_DATA", public)
    monkeypatch.setattr(project, "DATA", tmp_path / "data")
    project.DATA.mkdir(exist_ok=True)

    pid = "testproj01"
    root = public / pid
    root.mkdir()
    (root / "cache").mkdir()
    (root / "tts").mkdir()
    (root / "out").mkdir()
    src = root / "source.mp4"
    src.write_bytes(b"fake-video")
    (root / "cache" / "asr.json").write_text("{}", encoding="utf-8")
    (root / "cache" / "preview_20.mp4").write_bytes(b"p")
    (root / "tts" / "a.wav").write_bytes(b"x")
    (root / "out" / "final.mp4").write_bytes(b"y")
    (root / "meta.json").write_text(
        json.dumps(
            {
                "videoPath": str(src),
                "sourceFp": "abc",
                "duration": 12.0,
                "segments": [
                    {
                        "id": "1",
                        "source": "hi",
                        "translation": "xin",
                        "bbox": {"x": 1, "y": 2, "w": 3, "h": 4},
                        "captionLayout": {"x": 1},
                        "audioFile": "a.wav",
                    }
                ],
                "cache": {"asrKey": "k", "transKey": "t"},
                "translationCaches": {
                    "full": {
                        "asrKey": "k",
                        "segments": [{"id": "1", "bbox": {"x": 9, "y": 9, "w": 9, "h": 9}}],
                    }
                },
                "timelineBaseline": {"x": 1},
                "settings": {"engine": "whisper"},
                "status": {"step": "translate", "running": False},
                "workVideo": str(root / "cache" / "preview_20.mp4"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("pipeline.core.jobs.request_cancel", lambda _pid: True)
    monkeypatch.setattr("pipeline.core.jobs.clear_job", lambda _pid, gen=None: None)
    return pid, root, src


def test_clear_project_cache_keeps_source(tmp_path, monkeypatch):
    pid, root, src = _setup(tmp_path, monkeypatch)
    res = project.clear_project_cache(pid)
    assert res["ok"] is True
    assert src.is_file()
    assert not (root / "cache" / "asr.json").exists()
    assert not (root / "tts" / "a.wav").exists()
    assert not (root / "out" / "final.mp4").exists()
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    assert meta["segments"] == []
    assert meta.get("cache") == {}
    assert "workVideo" not in meta
    assert "translationCaches" not in meta
    assert "timelineBaseline" not in meta
    assert Path(meta["videoPath"]).is_file()
    assert meta["settings"]["engine"] == "whisper"


def test_clear_covers_only_keeps_segments(tmp_path, monkeypatch):
    pid, root, src = _setup(tmp_path, monkeypatch)
    res = project.clear_project_cache(pid, parts=["covers"])
    assert res["ok"] is True
    assert src.is_file()
    # TTS file not deleted when only covers
    assert (root / "tts" / "a.wav").is_file()
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    assert len(meta["segments"]) == 1
    seg = meta["segments"][0]
    assert "bbox" not in seg
    assert "captionLayout" not in seg
    assert seg.get("translation") == "xin"
    assert "timelineBaseline" not in meta
