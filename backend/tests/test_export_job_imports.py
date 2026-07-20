"""Self-check: run_export phải import đủ symbol — tránh kẹt Queued (full)."""
from __future__ import annotations

from pipeline.orchestrate import export_job


def test_run_export_has_source_helpers():
    assert callable(export_job.export_source_video)
    assert callable(export_job.expand_compound_segments)
    assert callable(export_job.run_export)
