import os
from pathlib import Path

from pipeline.core import config


def test_export_display_path_dev_relative(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    backend = repo / "backend"
    public = backend / "public"
    public.mkdir(parents=True)
    file = public / "exports" / "abc.mp4"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"x")

    monkeypatch.setattr(config, "REPO_ROOT", repo)
    monkeypatch.delenv("VIDEO_CLONE_DESKTOP", raising=False)
    assert config.export_display_path(file) == "backend/public/exports/abc.mp4"


def test_export_display_path_desktop_absolute(monkeypatch, tmp_path) -> None:
    file = tmp_path / "public_data" / "exports" / "abc.mp4"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"x")

    monkeypatch.setenv("VIDEO_CLONE_DESKTOP", "1")
    assert config.export_display_path(file) == str(file.resolve())
