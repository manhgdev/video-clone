from pipeline.ocr.locate import _QUICK_PROBE_LIMIT, _spread_probes


def test_probe_budget_keeps_endpoints():
    """Trần mốc OCR: giữ câu đầu + câu cuối, rải đều ở giữa."""
    items = [{"start": i, "end": i + 1} for i in range(155)]
    probes = _spread_probes(items, _QUICK_PROBE_LIMIT)

    assert len(probes) == _QUICK_PROBE_LIMIT
    assert probes[0] is items[0]
    assert probes[-1] is items[-1]


def test_short_list_untouched():
    items = [{"start": i, "end": i + 1} for i in range(5)]
    assert _spread_probes(items, _QUICK_PROBE_LIMIT) == items
