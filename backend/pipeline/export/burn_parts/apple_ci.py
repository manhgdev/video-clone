"""Apple Silicon Core Image blur-mask renderer with an FFmpeg-safe fallback."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pipeline.core.jobs import Cancelled, is_cancelled, register_process, unregister_process
from pipeline.export.cover_mask import _blur_css_radius, _blur_tint_alpha, _parse_hex_color

_SOURCE = Path(__file__).with_name("apple_ci_blur.swift")
_CACHE = Path(tempfile.gettempdir()) / "video-clone-apple-ci"


def _binary() -> Path | None:
    """Compile a source-hashed Swift helper once; absence is a normal fallback."""
    if sys.platform != "darwin" or platform.machine() not in {"arm64", "arm64e"}:
        return None
    try:
        digest = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()[:16]
        binary = _CACHE / f"blur-{digest}"
        if binary.is_file() and os.access(binary, os.X_OK):
            return binary
        _CACHE.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["swiftc", "-O", "-framework", "AVFoundation", "-framework", "CoreImage",
             "-framework", "Metal", str(_SOURCE), "-o", str(binary)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            binary.unlink(missing_ok=True)
            return None
        return binary if binary.is_file() else None
    except (OSError, subprocess.SubprocessError):
        return None


def _blur_masks(ops: list[dict[str, Any]], width: int, height: int) -> list[dict[str, float]]:
    """Translate ffgraph blur ops to the Core Image helper's stable JSON input."""
    masks: list[dict[str, float]] = []
    for op in ops:
        if op.get("kind") != "mask" or str(op.get("style") or "blur").lower() != "blur":
            return []
        x0, y0, x1, y1 = op["box"]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        opacity = int(op["opacity"])
        red, green, blue = _parse_hex_color(str(op["color"]))
        css_to_src = max(1.0, min(width, height) / 560.0)
        masks.append({
            "start": float(op["t0"]), "end": float(op["t1"]),
            # FFmpeg/Pillow boxes are top-left based; Core Image is bottom-left.
            "x": float(x0), "y": float(height - y1),
            "width": float(x1 - x0), "height": float(y1 - y0),
            "radius": _blur_css_radius(opacity) * css_to_src,
            "tintRed": red / 255.0, "tintGreen": green / 255.0, "tintBlue": blue / 255.0,
            "tintAlpha": _blur_tint_alpha(opacity),
        })
    return masks


def try_render_blur_masks(
    video: Path,
    out: Path,
    ops: list[dict[str, Any]],
    width: int,
    height: int,
    project_id: str | None,
) -> bool:
    """Render only all-blur mask sets on Metal, returning False for normal fallback."""
    binary = _binary()
    masks = _blur_masks(ops, width, height)
    if binary is None or not masks:
        return False
    spec = out.with_suffix(".json")
    try:
        spec.write_text(json.dumps(masks, separators=(",", ":")), encoding="utf-8")
        proc = subprocess.Popen([str(binary), str(video), str(out), str(spec)], stderr=subprocess.DEVNULL)
        register_process(project_id, proc)
        try:
            while proc.poll() is None:
                if is_cancelled(project_id):
                    proc.kill()
                    proc.wait(timeout=5)
                    raise Cancelled("apple_ci_blur: đã hủy")
                time.sleep(0.25)
        finally:
            unregister_process(project_id, proc)
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 1024:
            return True
        out.unlink(missing_ok=True)
        return False
    except Cancelled:
        out.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, TypeError):
        out.unlink(missing_ok=True)
        return False
    finally:
        spec.unlink(missing_ok=True)
