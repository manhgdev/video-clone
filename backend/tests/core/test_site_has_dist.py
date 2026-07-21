"""Package folder names use underscores; pip spec uses hyphens."""
from pathlib import Path

from pipeline.core import system_check as sc


def test_site_has_dist_matches_underscore_folder(tmp_path: Path) -> None:
    sp = tmp_path / "site-packages"
    sp.mkdir()
    (sp / "faster_whisper").mkdir()
    (sp / "rapidocr_onnxruntime-1.4.4.dist-info").mkdir()

    assert sc._site_has_dist(sp, "faster-whisper") is True
    assert sc._site_has_dist(sp, "rapidocr-onnxruntime") is True
    assert sc._site_has_dist(sp, "missing-pkg") is False
