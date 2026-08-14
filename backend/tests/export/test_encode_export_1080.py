from pathlib import Path

from pipeline.core import media


def test_encode_export_1080_copies_video_already_at_target(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    dst = tmp_path / "out.mp4"
    src.touch()
    seen: list[str] = []

    def fake_run(_project_id, cmd):
        seen.extend(cmd)
        Path(cmd[-1]).write_bytes(b"x")

    monkeypatch.setattr(media, "video_size", lambda _path: (1710, 1080))
    monkeypatch.setattr(media, "video_codec", lambda _path: "h264")
    monkeypatch.setattr(media, "run_cmd", fake_run)

    media.encode_export_1080(src, dst)

    assert seen[seen.index("-c:v") + 1] == "copy"
    assert "-vf" not in seen
    assert dst.read_bytes() == b"x"


def test_encode_export_1080_scales_non_target_video(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    dst = tmp_path / "out.mp4"
    src.touch()
    seen: list[str] = []

    def fake_run(_project_id, cmd):
        seen.extend(cmd)
        Path(cmd[-1]).write_bytes(b"y")

    monkeypatch.setattr(media, "video_size", lambda _path: (1280, 720))
    monkeypatch.setattr(media, "nvenc_available", lambda: True)
    monkeypatch.setattr(media, "run_cmd", fake_run)

    media.encode_export_1080(src, dst)

    assert seen[seen.index("-vf") + 1] == "scale=-2:1080"
    assert seen[seen.index("-c:v") + 1] == "h264_nvenc"
    assert dst.read_bytes() == b"y"


def test_windows_amd_uses_amf_when_probe_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(media, "nvenc_available", lambda: False)
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr(media, "detect_device", lambda: {"gpuKind": "amd"})
    monkeypatch.setattr(media, "_h264_encoder_available", lambda codec: codec == "h264_amf")

    args = media.h264_encoder_args()

    assert args[args.index("-c:v") + 1] == "h264_amf"


def test_windows_intel_uses_qsv_when_probe_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(media, "nvenc_available", lambda: False)
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr(media, "detect_device", lambda: {"gpuKind": "intel"})
    monkeypatch.setattr(media, "_h264_encoder_available", lambda codec: codec == "h264_qsv")

    args = media.h264_encoder_args()

    assert args[args.index("-c:v") + 1] == "h264_qsv"


def test_atomic_replace_retries_permission_error(monkeypatch, tmp_path: Path) -> None:
    import os

    src = tmp_path / "t.tmp"
    dst = tmp_path / "t.mp4"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")
    n = {"i": 0}
    real = os.replace

    def flaky(a, b):
        n["i"] += 1
        if n["i"] < 3:
            raise PermissionError(5, "Access is denied")
        return real(a, b)

    monkeypatch.setattr(media.os, "replace", flaky)
    media._atomic_replace(src, dst, attempts=10)
    assert dst.read_bytes() == b"new"
    assert n["i"] == 3
