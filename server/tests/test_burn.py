from __future__ import annotations

import numpy as np

from pipeline.export.burn import (
    _blur_region,
    _cover_box_fit,
    _is_corner_ui_box,
    _merge_ocr_samples,
)


def test_cover_box_uses_resolution_aware_horizontal_padding() -> None:
    box = _cover_box_fit([(400, 800, 680, 850)], None, 1080, 1920)

    # pad_x=22 (2%), pad_y=6 (0.3%) — trên = dưới
    assert box == (378, 794, 702, 856)


def test_translation_box_expands_width_not_height() -> None:
    # Caption chỉ nới ngang; Y bám OCR (không dính pad layout).
    box = _cover_box_fit(
        [(400, 800, 680, 850)], (250, 760, 830, 900), 1080, 1920
    )

    assert box[0] == 228 and box[2] == 852
    assert box[1] == 794 and box[3] == 856  # Y = OCR ± pad_y, không theo caption


def test_far_caption_does_not_shift_cover_y() -> None:
    box = _cover_box_fit(
        [(400, 1600, 680, 1680)], (200, 900, 880, 980), 1080, 1920
    )

    assert box[1] >= 1590
    assert box[3] <= 1690
    assert box[0] <= 200
    assert box[2] >= 880


def test_cover_box_clamps_excessive_height() -> None:
    box = _cover_box_fit([(300, 400, 780, 1200)], None, 1080, 1920)

    assert box[2] - box[0] == 524
    assert box[3] - box[1] <= max(36, int(1920 * 0.09)) + 2
    cy = (400 + 1200) // 2
    assert abs((box[1] + box[3]) // 2 - cy) <= 2


def test_blur_region_fully_replaces_horizontal_edges() -> None:
    frame = np.zeros((60, 100, 3), dtype=np.uint8)
    frame[30:40, 20:80] = 255

    result = _blur_region(frame.copy(), (20, 25, 80, 45))

    # Horizontal boundary pixels are covered, not partially blended with the
    # original white subtitle pixels.
    assert np.all(result[32:38, 20] < 100)
    assert np.all(result[32:38, 79] < 100)


def test_corner_ui_box_detected() -> None:
    # "12" góc trái — hẹp, sát mép.
    assert _is_corner_ui_box((20, 1500, 120, 1650), 1080, 1920)
    # hardsub giữa không bị coi là UI.
    assert not _is_corner_ui_box((280, 1500, 800, 1580), 1080, 1920)


def test_merge_ocr_samples_ignores_left_corner_logo() -> None:
    # Logo góc + hardsub giữa cùng dải Y → chỉ giữ hardsub.
    logo = (10, 1500, 100, 1640)
    sub = (300, 1520, 780, 1580)
    boxes = _merge_ocr_samples([logo, sub], 1080, 1920)
    assert len(boxes) == 1
    x0, _y0, x1, _y1 = boxes[0]
    assert x0 >= 200  # không kéo sang logo "12"
    assert x1 >= 780
