"""Job nền không được chiếm hết CPU (OCR từng làm đơ máy: 8.4/12 core)."""
import os

from pipeline.core.winproc import cpu_budget_cores


def test_leaves_cores_for_ui():
    cores = os.cpu_count() or 4
    used = cpu_budget_cores(0.6)
    assert 2 <= used <= cores
    if cores >= 4:
        assert used < cores, "phải chừa core cho UI/hệ điều hành"


def test_env_override(monkeypatch):
    cores = os.cpu_count() or 4
    monkeypatch.setenv("VIDEO_CLONE_JOB_CPU_FRACTION", "0.25")
    assert cpu_budget_cores(0.6) == max(2, int(cores * 0.25))
    monkeypatch.setenv("VIDEO_CLONE_JOB_CPU_FRACTION", "xx")
    assert cpu_budget_cores(0.5) == max(2, int(cores * 0.5))


def test_ocr_threads_capped_on_gpu():
    """ONNX mặc định -1 = lấy hết core; GPU chỉ cần vài luồng CPU."""
    from pipeline.ocr.extract_parts.runtime import _ort_threads

    gpu = _ort_threads(True)
    cpu = _ort_threads(False)
    cores = os.cpu_count() or 4
    assert gpu["intra_op_num_threads"] <= 2
    assert gpu["inter_op_num_threads"] == 1
    assert 1 <= cpu["intra_op_num_threads"] <= cores
    if cores >= 4:
        assert cpu["intra_op_num_threads"] < cores
