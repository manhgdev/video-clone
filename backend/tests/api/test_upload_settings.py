import asyncio
from io import BytesIO

from fastapi import UploadFile

from api.routes import projects


def test_new_upload_returns_fresh_video_transform_settings(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(projects, "DATA", tmp_path)
    monkeypatch.setattr(projects, "video_fingerprint", lambda _path: "new-video")
    monkeypatch.setattr(projects, "find_project_by_fp", lambda _fp: None)
    monkeypatch.setattr(projects, "ensure_layout", lambda _project_id: root)
    monkeypatch.setattr(projects, "ffprobe_duration", lambda _path: 10.0)
    monkeypatch.setattr(projects, "save_meta", lambda _project_id, _meta: None)
    monkeypatch.setattr(
        "pipeline.core.media.ensure_project_initial_playback_rate",
        lambda _meta, _settings: None,
    )

    result = asyncio.run(
        projects.api_upload(UploadFile(filename="new.mp4", file=BytesIO(b"video")))
    )

    assert result["settings"]["videoScaleX"] == 100.0
    assert result["settings"]["videoScaleY"] == 100.0
