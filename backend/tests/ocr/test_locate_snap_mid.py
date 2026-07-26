"""CAP-MID không bị snap Y xuống dải hardsub đáy."""
from __future__ import annotations

import sys

from pipeline.ocr.locate import (
    _apply_caption_box,
    _layout_from_cy,
    _python_can_ocr,
    _uv_run_cmd,
)
from pipeline.ocr.overlay_cover import mid_bottom_cutoff


def test_dev_locate_worker_python_can_run_ocr():
    """Worker OCR phải chạy bằng interpreter CÓ rapidocr/cv2 (ưu tiên CUDA).

    Trước đây dùng thẳng sys.executable: server khởi động ngoài .venv thì worker
    rơi vào Python hệ thống thiếu gói → OCR âm thầm chạy CPU hoặc trả 0 box.
    """
    if getattr(sys, "frozen", False):
        return
    cmd = _uv_run_cmd()
    assert cmd and len(cmd) == 1, cmd
    exe = cmd[0]
    can_ocr, _cuda = _python_can_ocr(exe)
    # Máy CI không cài rapidocr: chấp nhận fallback sys.executable
    assert can_ocr or exe == sys.executable, exe
    if _python_can_ocr(sys.executable)[0]:
        # sys.executable đủ điều kiện → phải ưu tiên nó (process sạch, ít bất ngờ)
        assert exe == sys.executable


def test_apply_caption_box_mid_not_tall():
    fw, fh = 1080, 1920
    seg: dict = {"source": "还有竹子"}
    # OCR poly cao bất thường
    _apply_caption_box(seg, (300, 1000, 700, 1200), fw, fh)
    assert seg["layout"] == "mid"
    assert seg["bboxInherited"] is True
    assert seg["bbox"]["h"] <= 130
    assert seg["bbox"]["y"] > 900


def test_apply_caption_box_wide_mid_stays_mid():
    seg: dict = {"source": "è¿™æ˜¯ä¸€æ¡å¾ˆé•¿çš„ä¸­é—´å­—å¹•"}
    _apply_caption_box(seg, (80, 860, 1000, 930), 1080, 1920)
    assert seg["layout"] == "mid"


def test_layout_bottom_band_uses_input_aspect_ratio():
    cy = 0.75
    assert _layout_from_cy(cy * 1920, 1920, 1080) == "mid"
    assert _layout_from_cy(cy * 1080, 1080, 1920) == "horizontal"
    portrait = mid_bottom_cutoff(1080, 1920)
    landscape = mid_bottom_cutoff(1920, 1080)
    assert 0.75 < mid_bottom_cutoff(4, 5) < portrait
    assert landscape < mid_bottom_cutoff(4, 3) < 0.75
