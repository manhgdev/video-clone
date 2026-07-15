"""Demucs speed / backend selection helpers."""
from pipeline.export.mux import (
    _apple_silicon,
    _demucs_backend_wanted,
    _demucs_jobs,
    _nvidia_smi_ok,
)


def test_demucs_jobs_bounded() -> None:
    j = _demucs_jobs()
    assert 1 <= j <= 6


def test_nvidia_smi_probe_runs() -> None:
    assert isinstance(_nvidia_smi_ok(), bool)


def test_backend_wanted_is_known() -> None:
    b = _demucs_backend_wanted()
    assert b in ("cuda", "mlx", "cpu")
    # Windows + NVIDIA ở máy dev → cuda; không GPU → cpu; Mac arm → mlx
    if _apple_silicon():
        assert b == "mlx"
    elif _nvidia_smi_ok():
        assert b == "cuda"


if __name__ == "__main__":
    test_demucs_jobs_bounded()
    test_nvidia_smi_probe_runs()
    test_backend_wanted_is_known()
    print("ok", _demucs_backend_wanted(), _demucs_jobs())
