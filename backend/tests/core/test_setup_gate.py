"""Setup gate persists on disk under VIDEO_CLONE_HOME."""
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
