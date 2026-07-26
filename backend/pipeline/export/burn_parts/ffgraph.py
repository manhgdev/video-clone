"""Burn nhanh: ffmpeg vẽ mask + chữ trực tiếp trong filter graph (P1 của PLAN).

Python vẫn CHUẨN BỊ toàn bộ (cue, layout WYSIWYG, render chữ ra RGBA) —
file này chỉ chuyển kết quả đó thành filter_complex để khung hình không phải
đi vòng GPU→RAM→Python→RAM→GPU (đo: 69fps → ~400fps trên 1080×1920).

Không xử lý được (trả False → pipeline dùng render_burned_video cũ):
- logo có fade/opacity theo thời gian (cần alpha ramp từng frame)
- cặp title dọc + nhãn xung đột nguồn (logic ẩn theo frame của bản cũ)
- quá 160 cue hoạt động (đợi P2 chia đoạn)
- VIDEO_CLONE_LEGACY_BURN=1 (van thoát khi nghi bug)
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pipeline.core.jobs import register_process, unregister_process
from pipeline.core.media import ffprobe_duration, h264_encoder_args
from pipeline.export.cover_mask import (
    _blur_css_radius,
    _blur_tint_alpha,
    _parse_hex_color,
)

_MAX_CUES = 160


def _esc_path(p: Path) -> str:
    """Đường dẫn trong filter script: / thay \\, escape dấu hai chấm."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def _enable(t0: float, t1: float) -> str:
    return f"enable='between(t,{max(0.0, t0):.3f},{max(0.0, t1):.3f})'"


def _hex_of(color: str) -> str:
    r, g, b = _parse_hex_color(color or "#4c1d95")
    return f"0x{r:02x}{g:02x}{b:02x}"


def _feasible(
    cues: list[tuple],
    cue_need_mask: list[bool],
    cue_overlays: list[Any],
    cue_segment_ids: list[str],
    segments_by_id: dict[str, dict[str, Any]],
) -> str | None:
    """None = chạy được bằng ffmpeg; str = lý do phải dùng đường cũ."""
    if os.environ.get("VIDEO_CLONE_LEGACY_BURN") == "1":
        return "VIDEO_CLONE_LEGACY_BURN=1"
    active = sum(
        1
        for i, _c in enumerate(cues)
        if (i < len(cue_need_mask) and cue_need_mask[i])
        or (i < len(cue_overlays) and cue_overlays[i] is not None)
    )
    if active > _MAX_CUES:
        return f"{active} cue > {_MAX_CUES} (đợi P2 chia đoạn)"
    for i, _c in enumerate(cues):
        sid = cue_segment_ids[i] if i < len(cue_segment_ids) else ""
        sm = segments_by_id.get(sid, {}) if sid else {}
        if sm.get("logoAssetPath"):
            return "logo overlay (fade theo frame)"
        if sm.get("logoFadeInEnd") or sm.get("logoFadeOutStart"):
            return "logo fade"
        try:
            if float(sm.get("logoOpacity", 1.0)) < 0.999:
                return "logo opacity"
        except (TypeError, ValueError):
            pass
    # Title dọc + nhãn xung đột nguồn cùng khung → bản cũ ẩn dọc theo frame
    for i, ci in enumerate(cues):
        if (ci[6] if len(ci) > 6 else "") != "vertical":
            continue
        vsrc = (ci[5] if len(ci) > 5 else "") or ""
        for j, cj in enumerate(cues):
            if i == j or (cj[6] if len(cj) > 6 else "") != "label":
                continue
            if cj[2] >= ci[3] or cj[3] <= ci[2]:
                continue  # không chồng thời gian
            lsrc = (cj[5] if len(cj) > 5 else "") or ""
            same = lsrc and vsrc and (
                lsrc == vsrc or lsrc in vsrc or vsrc in lsrc
                or abs(len(lsrc) - len(vsrc)) <= 1
            )
            if not same:
                return "title dọc + nhãn xung đột nguồn"
    return None


def _mask_ops(
    label_in: str,
    k: int,
    box: tuple[int, int, int, int],
    t0: float,
    t1: float,
    style: str,
    color: str,
    opacity: int,
    w: int,
    h: int,
) -> tuple[list[str], str]:
    """Filter chain cho MỘT vùng che — khớp cover_mask.py từng style."""
    x0, y0, x1, y1 = (max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3]))
    mw, mh = x1 - x0, y1 - y0
    if mw < 8 or mh < 8:
        return [], label_in
    en = _enable(t0, t1)
    st = (style or "blur").lower()
    lines: list[str] = []
    if st == "solid":
        a = max(0.0, min(1.0, opacity / 100.0))
        lines.append(
            f"{label_in}drawbox=x={x0}:y={y0}:w={mw}:h={mh}:"
            f"color={_hex_of(color)}@{a:.3f}:t=fill:{en}[vm{k}]"
        )
        return lines, f"[vm{k}]"
    if st == "mosaic":
        # cover_mask._blur_region: pixelate (w/20 × h/14) + gaussian nhẹ
        pw = max(2, mw // 20)
        ph = max(2, mh // 14)
        lines.append(f"{label_in}split=2[bg{k}][fg{k}]")
        lines.append(
            f"[fg{k}]crop={mw}:{mh}:{x0}:{y0},"
            f"pixelize=width={max(1, mw // pw)}:height={max(1, mh // ph)},"
            f"gblur=sigma=3[reg{k}]"
        )
        lines.append(f"[bg{k}][reg{k}]overlay={x0}:{y0}:{en}[vm{k}]")
        return lines, f"[vm{k}]"
    # blur «kính CapCut» — khớp _blur_tint_region: downscale→gauss→upscale,
    # desaturate 0.88, tint mỏng theo opacity
    css_blur = _blur_css_radius(opacity)
    css_to_src = max(1.0, min(w, h) / 560.0)
    radius = css_blur * css_to_src
    down = max(1.0, radius / 8.0)
    sw = max(4, int(round(mw / down)))
    sh = max(4, int(round(mh / down)))
    sigma = max(0.5, radius / (2.0 * down))
    tint = _blur_tint_alpha(opacity)
    lines.append(f"{label_in}split=2[bg{k}][fg{k}]")
    lines.append(
        f"[fg{k}]crop={mw}:{mh}:{x0}:{y0},"
        f"scale={sw}:{sh}:flags=area,gblur=sigma={sigma:.2f},"
        f"scale={mw}:{mh}:flags=bilinear,eq=saturation=0.88[reg{k}]"
    )
    lines.append(f"[bg{k}][reg{k}]overlay={x0}:{y0}:{en}[vb{k}]")
    lines.append(
        f"[vb{k}]drawbox=x={x0}:y={y0}:w={mw}:h={mh}:"
        f"color={_hex_of(color)}@{tint:.3f}:t=fill:{en}[vm{k}]"
    )
    return lines, f"[vm{k}]"


def try_render_ffmpeg(
    video: Path,
    out: Path,
    *,
    cues: list[tuple],
    cue_need_mask: list[bool],
    cue_fits: list[list[tuple[int, int, int, int]]],
    cue_overlays: list[Any],
    cue_segment_ids: list[str],
    segments_by_id: dict[str, dict[str, Any]],
    mask_style: str,
    mask_color: str,
    mask_opacity: int,
    burn: bool,
    w: int,
    h: int,
    project_id: str | None,
) -> bool:
    """True = đã ghi `out` bằng filter graph; False = caller dùng đường cũ."""
    reason = _feasible(cues, cue_need_mask, cue_overlays, cue_segment_ids, segments_by_id)
    if reason is not None:
        _log(f"[ffgraph] fallback legacy: {reason}")
        return False

    tmpdir = Path(tempfile.mkdtemp(prefix="vc-ffgraph-"))
    try:
        lines: list[str] = []
        cur = "[0:v]"
        k = 0

        # 1) Mask che — đúng thứ tự cue như _paint_one (mask trước, chữ sau)
        for ci, cue in enumerate(cues):
            if ci >= len(cue_need_mask) or not cue_need_mask[ci]:
                continue
            sid = cue_segment_ids[ci] if ci < len(cue_segment_ids) else ""
            sm = segments_by_id.get(sid, {}) if sid else {}
            st_cue = str(sm.get("coverMaskStyle") or mask_style)
            col_cue = str(sm.get("coverMaskColor") or mask_color)
            op_cue = int(
                sm.get("coverMaskOpacity")
                if sm.get("coverMaskOpacity") is not None
                else mask_opacity
            )
            for fit in cue_fits[ci] if ci < len(cue_fits) else []:
                if fit is None:
                    continue
                ops, cur = _mask_ops(
                    cur, k, fit, float(cue[0]), float(cue[1]),
                    st_cue, col_cue, op_cue, w, h,
                )
                lines.extend(ops)
                k += 1

        # 2) Chữ / overlay RGBA đã render sẵn (WYSIWYG giữ nguyên từ layout)
        if burn:
            from PIL import Image

            for bi, cue in enumerate(cues):
                ov = cue_overlays[bi] if bi < len(cue_overlays) else None
                if ov is None:
                    continue
                rgba, ox, oy = ov
                png = tmpdir / f"ov_{bi}.png"
                Image.fromarray(rgba).save(png)
                en = _enable(float(cue[2]), float(cue[3]))
                lines.append(f"movie=filename='{_esc_path(png)}'[png{k}]")
                lines.append(f"{cur}[png{k}]overlay={ox}:{oy}:{en}[vo{k}]")
                cur = f"[vo{k}]"
                k += 1

        if k == 0:
            # Không có gì để vẽ — copy nhanh, không re-encode
            import shutil

            shutil.copy2(video, out)
            return True

        # h264 cần kích thước chẵn
        ew, eh = int(w) - int(w) % 2, int(h) - int(h) % 2
        if (ew, eh) != (int(w), int(h)):
            lines.append(f"{cur}crop={ew}:{eh}:0:0[vout]")
        else:
            lines.append(f"{cur}copy[vout]")
        script = tmpdir / "graph.txt"
        script.write_text(";\n".join(lines) + "\n", encoding="utf-8")

        def _run() -> int:
            # Không -hwaccel: filter chạy CPU nên hwaccel chỉ thêm chuyến
            # GPU→CPU (đo 2707ms vs 2506ms) + 1 lần probe nvdec (~0.5-1s).
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
            cmd += [
                "-i", str(video),
                "-filter_complex_script", str(script),
                "-map", "[vout]", "-map", "0:a?",
                *h264_encoder_args(throughput=True),
                "-c:a", "aac", "-b:a", "192k",
                "-map_metadata", "-1", "-map_chapters", "-1",
                str(out),
            ]
            kw: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.PIPE,
            }
            if sys.platform == "win32":
                kw["creationflags"] = int(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                )
            proc = subprocess.Popen(cmd, **kw)
            register_process(project_id, proc)
            try:
                _o, err = proc.communicate(timeout=6 * 3600)
            finally:
                unregister_process(project_id, proc)
            if proc.returncode != 0:
                tail = (err or b"").decode("utf-8", "replace").strip()[-400:]
                _log(f"[ffgraph] ffmpeg rc={proc.returncode}: {tail}")
            return proc.returncode

        rc = _run()
        if rc != 0 or not out.exists() or out.stat().st_size < 1024:
            out.unlink(missing_ok=True)
            return False
        src_dur = float(ffprobe_duration(video) or 0.0)
        out_dur = float(ffprobe_duration(out) or 0.0)
        if src_dur > 1.0 and out_dur + 0.5 < src_dur:
            _log(f"[ffgraph] thiếu thời lượng {out_dur:.2f}/{src_dur:.2f}s → legacy")
            out.unlink(missing_ok=True)
            return False
        _log(f"[ffgraph] OK {k} node, {out_dur:.2f}s")
        return True
    except Exception as e:  # bất kỳ lỗi nào → đường cũ vẫn còn
        _log(f"[ffgraph] exception {type(e).__name__}: {e}")
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def _log(msg: str) -> None:
    try:
        from pipeline.core.app_log import append_log

        append_log(msg)
    except Exception:
        pass
