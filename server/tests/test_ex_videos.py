#!/usr/bin/env python3
"""Run OCR-first translate on ex_video/9_16.mp4 and ex_video/16_9.mp4."""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parents[2]
VIDEOS = [ROOT / "ex_video" / "9_16.mp4", ROOT / "ex_video" / "16_9.mp4"]
SETTINGS = {
    "engine": "paddleocr",
    "sourceLang": "zh",
    "targetLang": "vi",
    "translator": "ollama",
    "matchDuration": "natural",
    "defaultVoice": "system",
}


def post_json(url: str, data: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def upload(path: Path) -> dict:
    boundary = "----vcboundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{API}/api/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def wait_done(pid: str, label: str, timeout_s: int = 1800) -> dict:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        with urllib.request.urlopen(f"{API}/api/projects/{pid}/status", timeout=30) as r:
            st = json.loads(r.read().decode())
        msg = f"{st.get('step')} {st.get('progress')}% {st.get('message')}"
        if msg != last:
            print(f"  [{label}] {msg}", flush=True)
            last = msg
        if not st.get("running"):
            return st
        time.sleep(2)
    raise TimeoutError(label)


def run_one(path: Path) -> None:
    print(f"\n=== {path.name} ({path.stat().st_size // 1024} KB) ===", flush=True)
    up = upload(path)
    pid = up["projectId"]
    print(f"  project={pid} duration={up['duration']:.1f}s", flush=True)
    post_json(f"{API}/api/projects/{pid}/run", SETTINGS)
    st = wait_done(pid, path.name)
    with urllib.request.urlopen(f"{API}/api/projects/{pid}/segments", timeout=30) as r:
        segs = json.loads(r.read().decode())
    print(f"  status={st.get('message')}")
    print(f"  segments={len(segs)}")
    for s in segs[:8]:
        print(f"    {s['index']:02d} [{s['start']:.1f}-{s['end']:.1f}] {s['source'][:40]} → {s['translation'][:40]}")
    if len(segs) > 8:
        print(f"    … +{len(segs) - 8} đoạn")
    out = ROOT / "server" / "data" / pid / "segments_preview.json"
    out.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  saved {out}")


def main() -> int:
    for p in VIDEOS:
        if not p.exists():
            print("missing", p, file=sys.stderr)
            return 1
        run_one(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
