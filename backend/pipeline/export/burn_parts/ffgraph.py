"""Burn nhanh: ffmpeg vẽ mask + chữ trực tiếp trong filter graph (P1 của PLAN).

Python vẫn CHUẨN BỊ toàn bộ (cue, layout WYSIWYG, render chữ ra RGBA) —
file này chỉ chuyển kết quả đó thành filter_complex để khung hình không phải
đi vòng GPU→RAM→Python→RAM→GPU (đo: 69fps → ~400fps trên 1080×1920).

Không xử lý được (trả False → pipeline dùng render_burned_video cũ):
- cặp title dọc + nhãn xung đột nguồn (logic ẩn theo frame của bản cũ)
- quá 160 cue hoạt động (đợi P2 chia đoạn)
- VIDEO_CLONE_LEGACY_BURN=1 (van thoát khi nghi bug)

P1.5: nhận post_crop/post_height — nối crop,scale vào cuối graph để bỏ hẳn
lần encode thứ hai (encode_export_1080 chỉ còn copy). Logo opacity tĩnh nướng
vào alpha PNG; logo fade dùng input `-loop 1` + fade=alpha (ramp tuyến tính
y hệt _blit_overlay của render.py).
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


def _collect_ops(
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
) -> list[dict[str, Any]]:
    """Cue → op tuyến tính (mask trước, chữ sau) — cùng thứ tự _paint_one."""
    ops: list[dict[str, Any]] = []
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
            ops.append({
                "kind": "mask", "t0": float(cue[0]), "t1": float(cue[1]),
                "box": fit, "style": st_cue, "color": col_cue, "opacity": op_cue,
            })
    if burn:
        for bi, cue in enumerate(cues):
            ov = cue_overlays[bi] if bi < len(cue_overlays) else None
            if ov is None:
                continue
            rgba, ox, oy = ov
            sid = cue_segment_ids[bi] if bi < len(cue_segment_ids) else ""
            sm = segments_by_id.get(sid, {}) if sid else {}
            # Cùng công thức _blit_overlay của render.py: alpha tĩnh × ramp
            # tuyến tính [start→fadeInEnd] và [fadeOutStart→end].
            try:
                alpha = max(0.0, min(1.0, float(sm.get("logoOpacity", 1.0))))
            except (TypeError, ValueError):
                alpha = 1.0
            s0 = float(sm.get("start") or 0.0)
            s1 = float(sm.get("end") or cue[3])
            try:
                fin = float(sm.get("logoFadeInEnd") or s0)
            except (TypeError, ValueError):
                fin = s0
            try:
                fout = float(sm.get("logoFadeOutStart") or s1)
            except (TypeError, ValueError):
                fout = s1
            op: dict[str, Any] = {
                "kind": "text", "t0": float(cue[2]), "t1": float(cue[3]),
                "x": int(ox), "y": int(oy), "rgba": rgba, "idx": bi,
                "alpha": alpha,
            }
            if fin > s0 + 1e-3:
                op["fade_in"] = (s0, fin - s0)
            if s1 - 1e-3 > fout:
                op["fade_out"] = (fout, s1 - fout)
            ops.append(op)
    return ops


def _post_chain(
    w: int, h: int,
    crop: tuple[int, int, int, int] | None,
    target_height: int | None,
) -> list[str]:
    """crop+scale cuối graph — cùng công thức encode_export_1080 (media.py).

    Rỗng = không cần hậu xử lý (encode_export_1080 sẽ tự copy) — caller
    không được đánh dấu post_applied khi rỗng.
    """
    parts: list[str] = []
    if crop is not None:
        cx, cy, cw, ch = (int(v) for v in crop)
        parts.append(f"crop={cw}:{ch}:{cx}:{cy}")
        in_w, in_h = cw, ch
    else:
        in_w, in_h = int(w), int(h)
    if target_height:
        th = int(target_height)
        if in_h >= in_w:
            if crop is not None or in_w != th:
                parts.append(f"scale={th}:-2")
        elif crop is not None or in_h != th:
            parts.append(f"scale=-2:{th}")
    return parts


def _lines_for_ops(
    ops: list[dict[str, Any]],
    tmpdir: Path,
    w: int,
    h: int,
    t_off: float,
    t_len: float | None,
    post: list[str] | None = None,
) -> tuple[list[str], int, list[str]]:
    """Sinh filter graph cho các op giao với [t_off, t_off+t_len); t dịch về 0.

    Trả (lines, số node, extra_inputs) — extra_inputs là PNG cần thêm vào lệnh
    dạng `-loop 1 -i png` (logo fade cần stream theo thời gian, movie= chỉ ra
    1 frame pts=0 nên fade= không chạy được trên nó).
    """
    from PIL import Image

    lines: list[str] = []
    extra_inputs: list[str] = []
    cur = "[0:v]"
    k = 0
    for op in ops:
        t0, t1 = op["t0"] - t_off, op["t1"] - t_off
        if t_len is not None and (t1 <= 0.0 or t0 >= t_len):
            continue
        t0 = max(0.0, t0)
        if t_len is not None:
            t1 = min(t1, t_len + 0.05)
        if op["kind"] == "mask":
            mops, cur = _mask_ops(
                cur, k, op["box"], t0, t1,
                op["style"], op["color"], op["opacity"], w, h,
            )
            lines.extend(mops)
            k += 1
            continue
        alpha = float(op.get("alpha", 1.0))
        fade_in = op.get("fade_in")
        fade_out = op.get("fade_out")
        png = tmpdir / f"ov_{op['idx']}.png"
        if not png.exists():
            rgba = op["rgba"]
            if alpha < 0.999 and not (fade_in or fade_out):
                # Opacity tĩnh: nướng thẳng vào kênh alpha = _blit_overlay(alpha)
                rgba = rgba.copy()
                rgba[..., 3] = (rgba[..., 3].astype("float32") * alpha).astype("uint8")
            Image.fromarray(rgba).save(png)
        if fade_in or fade_out:
            n = len(extra_inputs) + 1  # [0] luôn là video
            extra_inputs.append(str(png))
            chain = "format=rgba"
            if alpha < 0.999:
                chain += f",colorchannelmixer=aa={alpha:.3f}"
            if fade_in:
                fst, fd = fade_in
                chain += f",fade=t=in:st={max(0.0, fst - t_off):.3f}:d={max(0.033, fd):.3f}:alpha=1"
            if fade_out:
                fst, fd = fade_out
                chain += f",fade=t=out:st={max(0.0, fst - t_off):.3f}:d={max(0.033, fd):.3f}:alpha=1"
            # trim chặn stream -loop 1 vô hạn — bảo đảm ffmpeg luôn kết thúc
            chain += f",trim=duration={t1 + 0.5:.3f}"
            lines.append(f"[{n}:v]{chain}[png{k}]")
        else:
            lines.append(f"movie=filename='{_esc_path(png)}'[png{k}]")
        lines.append(f"{cur}[png{k}]overlay={op['x']}:{op['y']}:{_enable(t0, t1)}[vo{k}]")
        cur = f"[vo{k}]"
        k += 1
    if k == 0:
        return [], 0, []
    tail = list(post) if post else []
    if not tail:
        ew, eh = int(w) - int(w) % 2, int(h) - int(h) % 2
        if (ew, eh) != (int(w), int(h)):
            tail = [f"crop={ew}:{eh}:0:0"]
    if tail:
        lines.append(f"{cur}{','.join(tail)}[vout]")
    else:
        lines.append(f"{cur}copy[vout]")
    return lines, k, extra_inputs


def _loop_inputs(extra: list[str]) -> list[str]:
    out: list[str] = []
    for p in extra:
        out += ["-loop", "1", "-i", p]
    return out


def _run_ffmpeg(cmd: list[str], project_id: str | None) -> int:
    kw: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE}
    if sys.platform == "win32":
        kw["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
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


def _keyframe_times(video: Path) -> list[float]:
    """PTS mọi keyframe — đọc packet header, không decode (nhanh cả video dài)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=600,
            creationflags=(
                int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
                if sys.platform == "win32" else 0
            ),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    kfs: list[float] = []
    for line in out.splitlines():
        bits = line.strip().split(",")
        if len(bits) >= 2 and "K" in bits[1]:
            try:
                kfs.append(float(bits[0]))
            except ValueError:
                pass
    return sorted(set(kfs))


# Segment hoá đáng làm khi phần trống tiết kiệm được ≥ MIN_SAVED giây encode
_SEG_MIN_SAVED = 5.0
_SEG_MAX_ACTIVE = 40
_SEG_MAX_COVERAGE = 0.7


def _plan_segments(
    ops: list[dict[str, Any]], duration: float, keyframes: list[float]
) -> list[tuple[float, float, bool]] | None:
    """Chia [0,duration) thành (a, b, active). None = không đáng segment."""
    if duration <= 0 or len(keyframes) < 3:
        return None
    # 1) cửa sổ op (pad nhẹ) → gộp
    wins: list[list[float]] = []
    for op in sorted(ops, key=lambda o: o["t0"]):
        a, b = max(0.0, op["t0"] - 0.2), min(duration, op["t1"] + 0.2)
        if wins and a <= wins[-1][1] + 1.0:
            wins[-1][1] = max(wins[-1][1], b)
        else:
            wins.append([a, b])
    # 2) nới về keyframe 2 phía
    import bisect

    aligned: list[list[float]] = []
    for a, b in wins:
        ia = bisect.bisect_right(keyframes, a) - 1
        ka = keyframes[ia] if ia >= 0 else 0.0
        ib = bisect.bisect_left(keyframes, b)
        kb = keyframes[ib] if ib < len(keyframes) else duration
        if aligned and ka <= aligned[-1][1] + 0.001:
            aligned[-1][1] = max(aligned[-1][1], kb)
        else:
            aligned.append([ka, kb])
    if len(aligned) > _SEG_MAX_ACTIVE:
        return None
    active_total = sum(b - a for a, b in aligned)
    if active_total / duration > _SEG_MAX_COVERAGE:
        return None
    if duration - active_total < _SEG_MIN_SAVED:
        return None
    # 3) đan xen copy/active phủ kín [0, duration)
    spans: list[tuple[float, float, bool]] = []
    cursor = 0.0
    for a, b in aligned:
        if a > cursor + 0.001:
            spans.append((cursor, a, False))
        spans.append((a, min(b, duration), True))
        cursor = min(b, duration)
    if cursor < duration - 0.001:
        spans.append((cursor, duration, False))
    return spans


def _render_segmented(
    video: Path,
    out: Path,
    ops: list[dict[str, Any]],
    spans: list[tuple[float, float, bool]],
    w: int,
    h: int,
    fps: float,
    project_id: str | None,
    tmpdir: Path,
) -> bool:
    """Cắt theo keyframe (segment muxer, packet-chính-xác), encode CHỈ đoạn
    active, concat, mux audio gốc một lần cuối. Đã kiểm chứng: 902/902 frame,
    duration khớp từng mili, stream sạch."""
    eps = max(0.008, 0.5 / max(1.0, fps))
    cut_times = [f"{a - eps:.6f}" for a, _b, _act in spans[1:]]
    seg_pat = tmpdir / "seg_%d.mp4"
    rc = _run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-map", "0:v", "-an", "-c", "copy", "-f", "segment",
         "-segment_times", ",".join(cut_times), "-reset_timestamps", "1",
         str(seg_pat)],
        project_id,
    )
    if rc != 0:
        return False
    seg_files = [tmpdir / f"seg_{i}.mp4" for i in range(len(spans))]
    if not all(f.is_file() for f in seg_files):
        _log(f"[ffgraph] segment muxer trả {sum(f.is_file() for f in seg_files)}/{len(spans)} file")
        return False
    final_parts: list[Path] = []
    for i, (a, b, active) in enumerate(spans):
        if not active:
            final_parts.append(seg_files[i])
            continue
        lines, k, extra = _lines_for_ops(ops, tmpdir, w, h, a, b - a)
        if k == 0:
            final_parts.append(seg_files[i])
            continue
        script = tmpdir / f"graph_{i}.txt"
        script.write_text(";\n".join(lines) + "\n", encoding="utf-8")
        enc = tmpdir / f"seg_{i}_e.mp4"
        rc = _run_ffmpeg(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(seg_files[i]), *_loop_inputs(extra),
             "-filter_complex_script", str(script),
             "-map", "[vout]", "-an",
             *h264_encoder_args(throughput=True),
             str(enc)],
            project_id,
        )
        if rc != 0:
            return False
        final_parts.append(enc)
    lst = tmpdir / "concat.txt"
    lst.write_text(
        "\n".join(f"file '{str(f).replace(chr(92), '/')}'" for f in final_parts) + "\n",
        encoding="utf-8",
    )
    vcat = tmpdir / "vcat.mp4"
    rc = _run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-an", str(vcat)],
        project_id,
    )
    if rc != 0:
        return False
    rc = _run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(vcat), "-i", str(video),
         "-map", "0:v", "-map", "1:a?",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-map_metadata", "-1", "-map_chapters", "-1", str(out)],
        project_id,
    )
    return rc == 0


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
    post_crop: tuple[int, int, int, int] | None = None,
    post_height: int | None = None,
    render_info: dict[str, Any] | None = None,
) -> bool:
    """True = đã ghi `out` bằng filter graph; False = caller dùng đường cũ.

    post_crop/post_height: gộp crop+scale xuất cuối vào cùng lệnh (P1.5) —
    khi áp dụng xong sẽ ghi render_info["post_applied"]=True để caller bỏ
    encode_export_1080 lần hai.
    """
    reason = _feasible(cues, cue_need_mask, cue_overlays)
    if reason is not None:
        _log(f"[ffgraph] fallback legacy: {reason}")
        return False

    tmpdir = Path(tempfile.mkdtemp(prefix="vc-ffgraph-"))
    try:
        ops = _collect_ops(
            cues, cue_need_mask, cue_fits, cue_overlays, cue_segment_ids,
            segments_by_id, mask_style, mask_color, mask_opacity, burn,
        )
        if not ops:
            import shutil

            shutil.copy2(video, out)
            return True

        src_dur = float(ffprobe_duration(video) or 0.0)

        def _validate() -> bool:
            if not out.exists() or out.stat().st_size < 1024:
                out.unlink(missing_ok=True)
                return False
            out_dur = float(ffprobe_duration(out) or 0.0)
            if src_dur > 1.0 and out_dur + 0.5 < src_dur:
                _log(f"[ffgraph] thiếu thời lượng {out_dur:.2f}/{src_dur:.2f}s")
                out.unlink(missing_ok=True)
                return False
            return True

        # P1.5: crop/scale xuất cuối nối vào graph → mọi frame phải encode
        # → segment hoá (P2) hết lợi, đi thẳng full graph một lệnh.
        post = _post_chain(w, h, post_crop, post_height)

        # P2: video có nhiều khoảng trống → chỉ encode đoạn có cue
        fps = _probe_fps(video)
        spans = None if post else _plan_segments(ops, src_dur, _keyframe_times(video))
        if spans is not None:
            n_active = sum(1 for _a, _b, act in spans if act)
            act_t = sum(b - a for a, b, act in spans if act)
            _log(
                f"[ffgraph] segmented: {n_active} đoạn encode ({act_t:.1f}s)"
                f" / copy {src_dur - act_t:.1f}s"
            )
            if _render_segmented(video, out, ops, spans, w, h, fps, project_id, tmpdir) and _validate():
                _log(f"[ffgraph] OK segmented {len(ops)} op")
                return True
            _log("[ffgraph] segmented thất bại → full graph")
            out.unlink(missing_ok=True)

        # Full graph một lệnh (P1)
        lines, k, extra = _lines_for_ops(ops, tmpdir, w, h, 0.0, None, post=post)
        if k == 0:
            import shutil

            shutil.copy2(video, out)
            return True
        script = tmpdir / "graph.txt"
        script.write_text(";\n".join(lines) + "\n", encoding="utf-8")
        # Không -hwaccel: filter chạy CPU nên hwaccel chỉ thêm chuyến GPU→CPU
        rc = _run_ffmpeg(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(video), *_loop_inputs(extra),
             "-filter_complex_script", str(script),
             "-map", "[vout]", "-map", "0:a?",
             *h264_encoder_args(throughput=True),
             "-c:a", "aac", "-b:a", "192k",
             "-map_metadata", "-1", "-map_chapters", "-1",
             str(out)],
            project_id,
        )
        if rc != 0 or not _validate():
            out.unlink(missing_ok=True)
            return False
        if post and render_info is not None:
            render_info["post_applied"] = True
        _log(f"[ffgraph] OK full-graph {k} node" + (" +crop/scale" if post else ""))
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


def _probe_fps(video: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=60,
            creationflags=(
                int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
                if sys.platform == "win32" else 0
            ),
        ).stdout.strip()
        num, _sep, den = out.partition("/")
        return float(num) / float(den or 1)
    except (OSError, ValueError, ZeroDivisionError, subprocess.SubprocessError):
        return 25.0


def _log(msg: str) -> None:
    try:
        from pipeline.core.app_log import append_log

        append_log(msg)
    except Exception:
        pass
