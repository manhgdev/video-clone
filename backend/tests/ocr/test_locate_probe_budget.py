from pipeline.ocr.locate import _STABLE_PROBE_LIMIT, _spread_probes


def test_stable_probe_budget_keeps_endpoints():
    items = [{"start": i, "end": i + 1} for i in range(155)]
    probes = _spread_probes(items, _STABLE_PROBE_LIMIT)

    assert len(probes) == _STABLE_PROBE_LIMIT
    assert probes[0] is items[0]
    assert probes[-1] is items[-1]
