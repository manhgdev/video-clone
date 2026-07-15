"""workVideo phải thắng source khi full preferVideo (previewSec=0)."""
from __future__ import annotations

from pathlib import Path


def test_resolve_prefers_work_video_when_preview_sec_zero(tmp_path: Path) -> None:
    from pipeline.core.project import audio_cache_tag, resolve_project_video

    source = tmp_path / "source.mp4"
    work = tmp_path / "cache" / "source_s080.mp4"
    work.parent.mkdir(parents=True)
    source.write_bytes(b"1x")
    work.write_bytes(b"s080")

    meta = {
        "videoPath": str(source),
        "workVideo": str(work),
        "previewSec": 0,
        "bakedPreferVideo": True,
    }
    assert resolve_project_video(meta, "proj") == work


def test_resolve_falls_back_to_source() -> None:
    from pipeline.core.project import resolve_project_video

    missing = Path("/nonexistent/work_s080.mp4")
    source = Path(__file__).resolve()  # any existing file
    meta = {
        "videoPath": str(source),
        "workVideo": str(missing),
        "previewSec": 0,
    }
    assert resolve_project_video(meta, "proj") == source


def test_audio_cache_tag_includes_speed() -> None:
    from pipeline.core.project import audio_cache_tag

    assert audio_cache_tag(0, "preferVideo") == "full_s080"
    assert audio_cache_tag(10, "preferVideo") == "p10_s080"
    assert audio_cache_tag(0, "natural") == "full_s1"


if __name__ == "__main__":
    from pathlib import Path as P

    d = P(__file__).resolve().parent / "_tmp_resolve_video"
    d.mkdir(exist_ok=True)
    try:
        test_resolve_prefers_work_video_when_preview_sec_zero(d)
        test_resolve_falls_back_to_source()
        test_audio_cache_tag_includes_speed()
        print("ok")
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)
