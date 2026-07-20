"""Self-check: auto GPU jobs gần full; chỉ hạ nhẹ khi card đang full."""
from __future__ import annotations

from pipeline.core import resources


def test_gpu_job_cap_uses_vram_when_idle(monkeypatch):
    def fake_smi(cmd, **kwargs):
        # total, free, util — card 6GB rảnh
        assert "memory.total" in " ".join(cmd)
        return "6144, 5200, 12\n"

    monkeypatch.setattr(resources.subprocess, "check_output", fake_smi)
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 12)
    n = resources.gpu_job_cap(per_job_mb=1200, reserve_mb=700)
    assert n >= 3, n
    assert n <= 12, n


def test_gpu_job_cap_backs_off_when_util_full(monkeypatch):
    calls = {"n": 0}

    def fake_smi(cmd, **kwargs):
        calls["n"] += 1
        # total, free, util — gần full
        return "6144, 900, 96\n"

    monkeypatch.setattr(resources.subprocess, "check_output", fake_smi)
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 12)
    n = resources.gpu_job_cap(per_job_mb=1200, reserve_mb=700)
    assert 1 <= n <= 3, n


def test_gpu_auto_workers_no_hard_cap_four(monkeypatch):
    monkeypatch.setattr(resources, "_cpu_idle_and_memory", lambda: (0.8, 0.5))
    monkeypatch.setattr(resources, "_gpu_headroom", lambda: (0.6, 0.5))
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 16)
    n = resources.adaptive_workers(0, kind="gpu", cap=16)
    assert n > 4, n
