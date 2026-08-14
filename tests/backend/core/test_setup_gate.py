"""Setup gate persists on disk under VIDEO_CLONE_HOME."""
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="Python hệ thống thiếu fastapi — test API chạy trong venv backend")

from api.routes import system as sysroutes


def test_setup_gate_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_CLONE_HOME", str(tmp_path))
    assert sysroutes._setup_gate_passed() is False
    sysroutes._mark_setup_gate()
    assert sysroutes._setup_gate_passed() is True
    assert (tmp_path / "setup_ok").is_file()


def test_first_checks_request_starts_background_warm(monkeypatch) -> None:
    from pipeline.core import system_check as sc

    warmed = threading.Event()
    monkeypatch.setattr(sc, "_CHECKS_CACHE", None)
    monkeypatch.setattr(sc, "system_checks", lambda **_kwargs: warmed.set())
    monkeypatch.setattr(sysroutes, "_checks_warming", False)

    result = sysroutes.api_system_checks()

    assert result["loading"] is True
    assert warmed.wait(2), "first request must start the checks worker"


def test_checks_route_reads_real_submodule_cache(monkeypatch) -> None:
    from pipeline.core import system_check as sc

    expected = {"ok": False, "items": [{"id": "ai_runtime"}]}
    # Reproduce the stale facade copy that previously kept first-run at loading forever.
    monkeypatch.setattr(sc, "_CHECKS_CACHE", None, raising=False)
    monkeypatch.setattr(sc.checks, "_CHECKS_CACHE", (0.0, True, expected))

    assert sysroutes.api_system_checks() == expected
