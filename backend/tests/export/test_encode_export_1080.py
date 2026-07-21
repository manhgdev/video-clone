from pathlib import Path

from pipeline.core import media


def test_encode_export_1080_copies_video_already_at_target(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    dst = tmp_path / "out.mp4"
    src.touch()
    seen: list[str] = []

    monkeypatch.setattr(media, "video_size", lambda _path: (1710, 1080))
    monkeypatch.setattr(media, "video_codec", lambda _path: "h264")
    monkeypatch.setattr(media, "run_cmd", lambda _project_id, cmd: seen.extend(cmd))
    monkeypatch.setattr(Path, "replace", lambda self, target: target)

    media.encode_export_1080(src, dst)

    assert seen[seen.index("-c:v") + 1] == "copy"
    assert "-vf" not in seen


def test_encode_export_1080_scales_non_target_video(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    dst = tmp_path / "out.mp4"
    src.touch()
    seen: list[str] = []

    monkeypatch.setattr(media, "video_size", lambda _path: (1280, 720))
    monkeypatch.setattr(media, "nvenc_available", lambda: True)
    monkeypatch.setattr(media, "run_cmd", lambda _project_id, cmd: seen.extend(cmd))
    monkeypatch.setattr(Path, "replace", lambda self, target: target)

    media.encode_export_1080(src, dst)

    assert seen[seen.index("-vf") + 1] == "scale=-2:1080"
    assert seen[seen.index("-c:v") + 1] == "h264_nvenc"
