"""Chốt các lỗi phát hiện trong đợt audit toàn bộ src — không cho tái phát."""
from pathlib import Path

from pipeline.core.cleanup import _is_purgeable, cleanup_public_files
from pipeline.core.config import safe_child
from pipeline.export.srt import write_subtitle


def test_safe_child_blocks_traversal(tmp_path: Path):
    base = tmp_path / "tts"
    base.mkdir()
    assert safe_child(base, "a.wav") == base / "a.wav"
    for bad in ("..", "../x", r"..\..\secret.env", "/etc/passwd", r"C:\Windows\x", "", "."):
        assert safe_child(base, bad) is None, bad


def test_cleanup_never_deletes_user_data(tmp_path: Path):
    project = tmp_path / "p1"
    (project / "cache").mkdir(parents=True)
    source = project / "source.mp4"
    meta = project / "meta.json"
    tts = project / "tts"
    tts.mkdir()
    voice = tts / "seg1.wav"
    cache_file = project / "cache" / "preview_5.mp4"
    for f in (source, meta, voice, cache_file):
        f.write_bytes(b"x")
        # quá hạn (mtime rất cũ)
        import os

        os.utime(f, (0, 0))
    assert _is_purgeable(cache_file, tmp_path)
    assert not _is_purgeable(source, tmp_path)
    assert not _is_purgeable(meta, tmp_path)
    assert not _is_purgeable(voice, tmp_path)
    deleted, _skipped = cleanup_public_files(tmp_path, retention_days=1)
    assert deleted == 1
    assert source.is_file() and meta.is_file() and voice.is_file()
    assert not cache_file.exists()


def test_subtitle_formats_are_real(tmp_path: Path):
    cues = [{"start": 0, "end": 1.5, "text": "Xin chao"}, {"start": 2, "end": 3, "text": "The gioi"}]
    vtt = tmp_path / "a.vtt"
    write_subtitle(vtt, cues, "vtt", capcut=False)
    body = vtt.read_text(encoding="utf-8")
    assert body.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in body  # dấu chấm, không phải phẩy
    txt = tmp_path / "a.txt"
    write_subtitle(txt, cues, "txt", capcut=False)
    assert txt.read_text(encoding="utf-8").splitlines() == ["Xin chao", "The gioi"]
    srt = tmp_path / "a.srt"
    write_subtitle(srt, cues, "srt", capcut=False)
    assert "00:00:00,000 --> 00:00:01,500" in srt.read_text(encoding="utf-8-sig")


def test_write_ass_and_cover_box_over_do_not_raise(tmp_path: Path):
    """Hai NameError chắc chắn (thiếu import / hằng) — gọi thật để chốt."""
    from pipeline.export.burn_parts.ass_util import write_ass
    from pipeline.export.burn_parts.layout_geo import _cover_box_over

    out = tmp_path / "a.ass"
    write_ass(out, [{"start": 0, "end": 1, "translation": "xin chao"}], 1280, 720)
    assert out.stat().st_size > 0
    box = _cover_box_over(None, (10, 10, 200, 60), 40, 1280, 720)
    assert box[2] > box[0] and box[3] > box[1]


def test_tts_fit_never_stretches_video():
    """Contract 2026-07-27: thước bất khả xâm phạm — không bao giờ gán
    videoSpeed; miền auto (<1) còn sót phải bị dọn, speed user (≥1) giữ."""
    from pipeline.orchestrate.tts_fit import assign_tts_fit_speeds

    segs = [{"id": "a", "start": 0, "end": 2.1, "audioDuration": 3.0}]
    assign_tts_fit_speeds(segs, match="preferVideo")
    assert "videoSpeed" not in segs[0]
    # videoSpeed auto cũ (<1) → dọn; user đặt 1.5× → giữ
    segs2 = [
        {"id": "a", "start": 0, "end": 2, "videoSpeed": 0.82},
        {"id": "b", "start": 3, "end": 5, "videoSpeed": 1.5},
    ]
    assign_tts_fit_speeds(segs2, match="preferVideo")
    assert "videoSpeed" not in segs2[0]
    assert segs2[1]["videoSpeed"] == 1.5


def test_fit_tts_audio_compresses_to_slot(tmp_path):
    """Wav dài hơn khe tới câu sau → atempo nén, audioDuration cập nhật."""
    import subprocess

    from pipeline.orchestrate.tts_fit import fit_tts_audio_to_slots

    (tmp_path / "tts").mkdir()
    wav = tmp_path / "tts" / "a.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=4", str(wav)],
        check=True, capture_output=True,
    )
    segs = [
        {"id": "a", "start": 0.0, "end": 2.0, "audioFile": "a.wav", "audioDuration": 4.0},
        {"id": "b", "start": 2.5, "end": 4.0},
    ]
    n = fit_tts_audio_to_slots(segs, tmp_path, match="preferVideo")
    assert n == 1
    # Tổng tăng tốc tự động bị chặn 1.25×: wav 4s không được ép ngắn quá ~3.2s.
    assert 3.1 < segs[0]["audioDuration"] < 3.4


def test_translate_parent_does_not_probe_cv2_or_ort():
    """Sau ASR/dịch, parent không import OpenCV/ORT — native crash tắt app."""
    import pipeline.orchestrate.asr_translate as asr_translate

    src = Path(asr_translate.__file__).read_text(encoding="utf-8")
    assert "ensure_cv2" not in src
    assert "_rapidocr_gpu_kwargs" not in src
    assert "generate_inpaint_preview" not in src


def test_job_python_frozen_is_not_the_exe(monkeypatch, tmp_path) -> None:
    import importlib.util

    path = Path("backend/api/job_spawn.py")
    spec = importlib.util.spec_from_file_location("vc_job_spawn", path)
    assert spec and spec.loader
    js = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(js)

    runtime = tmp_path / "python.exe"
    runtime.write_text("", encoding="utf-8")
    monkeypatch.setattr(js.sys, "frozen", True, raising=False)
    monkeypatch.setattr("pipeline.core.accel._runtime_python", lambda: str(runtime))
    assert js._job_python() == str(runtime)


def test_win_jobs_stay_out_of_uvicorn() -> None:
    spawn = Path("backend/api/job_spawn.py").read_text(encoding="utf-8")
    assert "_run_in_subprocess" in spawn
    locate = Path("backend/pipeline/ocr/locate.py").read_text(encoding="utf-8")
    assert "không chạy OCR trong process API" in locate


def test_desktop_supervisor_shows_copyable_crash() -> None:
    src = Path("build_app/launcher.py").read_text(encoding="utf-8")
    assert "VIDEO_CLONE_SUPERVISOR_CHILD" in src
    assert "show_copyable_crash" in src
    assert "Chép lỗi" in src
    assert "last_crash.txt" in src
    assert "Zone.Identifier" in src
    assert "Python.Runtime.dll" in src
    assert "prepare_pythonnet" in src
