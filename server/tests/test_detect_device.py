"""Device probe for setup — OS + GPU + full install plan."""
from pipeline.core.media import detect_device, hardware


def test_detect_device_has_os_and_accel() -> None:
    d = detect_device()
    assert d["os"] in ("windows", "macos", "linux", "unknown")
    assert d["osLabel"]
    assert d["accel"] in ("cuda", "metal", "cpu")
    assert d["gpuKind"] in ("nvidia", "apple", "none")
    assert "install" in d
    assert d["install"]["demucsBackend"] in ("cuda", "mlx", "cpu")
    assert d["install"]["demucsLabel"]
    items = d["install"]["items"]
    assert "python" in items and "ffmpeg" in items and "demucs" in items
    assert items["python"]["value"].startswith("http")
    assert items["demucs"]["kind"] == "action"


def test_hardware_matches_detect() -> None:
    d = detect_device()
    h = hardware()
    assert h["accel"] == d["accel"]
    assert h["os"] == d["os"]
    assert h["gpuKind"] == d["gpuKind"]


def test_system_checks_uses_device_plan() -> None:
    from pipeline.core.system_check import system_checks

    c = system_checks()
    assert "device" in c
    by_id = {i["id"]: i for i in c["items"]}
    assert "installLabel" in by_id["ffmpeg"]
    assert by_id["demucs"].get("installLabel")
    if c["device"]["gpuKind"] == "nvidia":
        assert by_id["ocr_cuda"]["install"] == "ocr_cuda"
    else:
        assert by_id["ocr_cuda"]["install"] == ""


if __name__ == "__main__":
    test_detect_device_has_os_and_accel()
    test_hardware_matches_detect()
    test_system_checks_uses_device_plan()
    d = detect_device()
    print("ok", d["os"], d["gpuKind"], d["install"]["summary"])
    print("items", sorted(d["install"]["items"]))
    print("actions", d["install"]["actions"])
