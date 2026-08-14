"""Facade package system_check/: gán attr lên facade phải thấy được ở submodule."""
from pipeline.core import system_check as sc
from pipeline.core.system_check import checks, install, probe


def test_setattr_propagates_to_submodules(monkeypatch) -> None:
    fake = lambda: (True, "fake")  # noqa: E731
    monkeypatch.setattr(sc, "_demucs_venv_fast", fake)
    # probe định nghĩa, checks from-import — cả hai phải nhận bản patch
    assert probe._demucs_venv_fast is fake
    assert checks._demucs_venv_fast is fake


def test_install_log_fn_assignment_reaches_install_module() -> None:
    # routes/system.py gán sc._install_log_fn khi start install job
    lines: list[str] = []
    fn = lines.append
    old = sc._install_log_fn
    try:
        sc._install_log_fn = fn
        assert install._install_log_fn is fn
    finally:
        sc._install_log_fn = old
