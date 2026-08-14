from __future__ import annotations

from pipeline.core import media


def test_windows_inventory_keeps_hybrid_adapters(monkeypatch) -> None:
    monkeypatch.setattr(
        media,
        "_windows_video_controllers",
        lambda: [
            {"Name": "Intel(R) Iris Xe Graphics", "AdapterRAM": 2 * 1024**3},
            {"Name": "NVIDIA GeForce RTX 4060 Laptop GPU", "AdapterRAM": 8 * 1024**3},
        ],
    )
    nvidia = [{
        "index": 0,
        "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
        "kind": "nvidia",
        "vramMb": 8192,
        "driver": "555.0",
        "accel": "cuda",
        "source": "nvidia-smi",
    }]

    result = media._windows_gpu_inventory(nvidia)

    assert [gpu["kind"] for gpu in result] == ["nvidia", "intel"]
    assert result[1]["accel"] == "directml"


def test_windows_inventory_keeps_amd_apu_and_discrete_gpu(monkeypatch) -> None:
    monkeypatch.setattr(
        media,
        "_windows_video_controllers",
        lambda: [
            {"Name": "AMD Radeon(TM) Graphics", "AdapterRAM": 512 * 1024**2},
            {"Name": "AMD Radeon RX 7800 XT", "AdapterRAM": 16 * 1024**3},
        ],
    )

    result = media._windows_gpu_inventory([])

    assert len(result) == 2
    assert all(gpu["kind"] == "amd" for gpu in result)


def test_windows_inventory_does_not_merge_similarly_named_amd_gpus(monkeypatch) -> None:
    monkeypatch.setattr(
        media,
        "_windows_video_controllers",
        lambda: [
            {"Name": "AMD Radeon Graphics", "AdapterRAM": 512 * 1024**2},
            {"Name": "AMD Radeon Graphics 780M", "AdapterRAM": 2 * 1024**3},
        ],
    )

    assert len(media._windows_gpu_inventory([])) == 2
