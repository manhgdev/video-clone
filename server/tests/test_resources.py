from unittest.mock import patch

from pipeline.core.resources import adaptive_workers


def test_fixed_workers_remain_fixed() -> None:
    assert adaptive_workers(8, cap=16) == 8
    assert adaptive_workers(20, cap=16) == 16


def test_auto_uses_more_cpu_when_machine_is_idle() -> None:
    with patch("pipeline.core.resources.os.cpu_count", return_value=16):
        with patch(
            "pipeline.core.resources._cpu_idle_and_memory", return_value=(1.0, 1.0)
        ):
            idle = adaptive_workers(0, cap=16)
        with patch(
            "pipeline.core.resources._cpu_idle_and_memory", return_value=(0.0, 1.0)
        ):
            busy = adaptive_workers(0, cap=16)
    assert idle > busy >= 1


def test_auto_gpu_respects_vram_and_utilization() -> None:
    with patch("pipeline.core.resources.os.cpu_count", return_value=16), patch(
        "pipeline.core.resources._cpu_idle_and_memory", return_value=(1.0, 1.0)
    ):
        with patch("pipeline.core.resources._gpu_headroom", return_value=(0.9, 0.8)):
            idle = adaptive_workers(0, kind="gpu", cap=6)
        with patch("pipeline.core.resources._gpu_headroom", return_value=(0.1, 0.1)):
            busy = adaptive_workers(0, kind="gpu", cap=6)
    assert idle == 4
    assert busy == 1
