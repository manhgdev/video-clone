"""meta.json lock + atomic write."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from pipeline.core.project import load_meta, mutate_meta, project_dir, save_meta


def test_mutate_meta_serializes_concurrent_updates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pipeline.core.project.DATA", tmp_path)
    monkeypatch.setattr("pipeline.core.config.DATA", tmp_path)
    pid = "race-test"
    project_dir(pid)
    save_meta(pid, {"segments": [{"id": "a", "n": 0}, {"id": "b", "n": 0}]})

    def bump(seg_id: str) -> None:
        def apply(meta: dict) -> None:
            for seg in meta["segments"]:
                if seg["id"] == seg_id:
                    seg["n"] = int(seg.get("n", 0)) + 1

        mutate_meta(pid, apply)

    threads = [threading.Thread(target=bump, args=("a",)) for _ in range(20)]
    threads += [threading.Thread(target=bump, args=("b",)) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    meta = load_meta(pid)
    by_id = {s["id"]: s["n"] for s in meta["segments"]}
    assert by_id == {"a": 20, "b": 20}
    raw = (project_dir(pid) / "meta.json").read_text(encoding="utf-8")
    json.loads(raw)
