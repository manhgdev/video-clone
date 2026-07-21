from pipeline.core import system_check as sc


def test_system_checks_uses_cache_until_refresh(monkeypatch) -> None:
    calls = 0

    def fake_uncached(*, fast: bool = True) -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True, "platform": "test", "python": "3.12", "items": [], "fast": fast}

    monkeypatch.setattr(sc, "_system_checks_uncached", fake_uncached)
    sc._invalidate_checks_cache()

    first = sc.system_checks()
    second = sc.system_checks()
    third = sc.system_checks(refresh=True)

    assert calls == 2
    assert first["ok"] is True
    assert second is first
    assert third is not first
