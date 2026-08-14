from pathlib import Path

import numpy as np

from pipeline.export.cover_mask import _inpaint_region
from pipeline.orchestrate import export_job


def test_inpaint_changes_logo_but_not_distant_pixels():
    yy, xx = np.mgrid[:160, :240]
    frame = np.stack(
        ((xx + yy) % 255, (xx * 2) % 255, (yy * 2) % 255), axis=-1
    ).astype(np.uint8)
    frame[50:90, 70:140] = 255
    original = frame.copy()

    result = _inpaint_region(frame, (70, 50, 140, 90))

    assert np.mean(np.abs(result[55:85, 75:135].astype(int) - 255)) > 20
    assert np.array_equal(result[:20, :20], original[:20, :20])
    assert np.array_equal(result[130:, 200:], original[130:, 200:])


def test_logo_mask_is_independent_from_caption_cover(monkeypatch):
    monkeypatch.setattr(export_job, "video_size", lambda _video: (1920, 1080))
    meta = {
        "settings": {
            "coverLogo": True,
            "coverHardsubs": False,
            "burnSubs": False,
        },
        "logoDetection": {
            "bbox": {"x": 0.8, "y": 0.05, "w": 0.1, "h": 0.08}
        },
    }

    cue = export_job._logo_mask_cue(meta, Path("video.mp4"), 30.0, "project")

    assert cue is not None
    assert cue["maskOnly"] is True
    assert cue["coverMaskStyle"] == "inpaint"
    assert cue["end"] == 30.0
    assert cue["bbox"] == {"x": 1536, "y": 54, "w": 192, "h": 86}


def test_logo_mask_disabled_is_noop():
    assert (
        export_job._logo_mask_cue(
            {"settings": {"coverLogo": False}},
            Path("video.mp4"),
            30.0,
            "project",
        )
        is None
    )
