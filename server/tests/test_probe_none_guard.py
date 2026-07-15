"""Guard: early-stop after OCR probe must not index None."""


def test_probe_early_stop_skips_none() -> None:
    hits = []
    for p in (None, (1.0, 40.0, (0, 0, 10, 10), "x"), None):
        if not p:
            continue
        hits.append(p)
        if p[1] >= 35:
            break
    assert len(hits) == 1
    assert hits[0][1] == 40.0
