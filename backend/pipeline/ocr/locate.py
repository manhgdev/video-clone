"""OCR định vị hardsub / nhãn / title dọc trên khung (dùng lúc burn)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import threading
from functools import lru_cache

from ..core.jobs import Cancelled, check_cancel
from .extract import _ocr_join_lines, _rapidocr_labels
from .labels import pick_label_box
from .overlay_cover import mid_bottom_cutoff

# Lock: chỉ một request được chạy OCR in-process — native ext crash khi song song.
_inprocess_lock = threading.Lock()

# Quick mode samples the whole timeline instead of invoking OCR for every cue.
# 64 anchors keep style/location changes within a few neighbouring captions
# while bounding long-video model calls.
_QUICK_PROBE_LIMIT = 64
# Stable mode keeps first/middle/last samples but caps long timelines. The
# remaining cues inherit from the nearest stable anchor instead of triggering
# another OCR pass.


def _spread_probes(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Return up to ``limit`` items spread evenly, including both endpoints."""
    if limit < 2 or len(items) <= limit:
        return items
    last = len(items) - 1
    indices = sorted({round(i * last / (limit - 1)) for i in range(limit)})
    return [items[i] for i in indices]


# Worker subprocess tách sang locate_worker.py — re-export giữ import cũ
from .locate_worker import (  # noqa: F401
    _dev_worker_python,
    _locate_via_runtime_subprocess,
    _python_can_ocr,
    _uv_run_cmd,
)


def _source_matches(text: str, source: str) -> bool:
    """Khớp OCR với source; không cho 1 glyph chung kéo cả box nhiễu vào cue dài."""
    tx = "".join((text or "").split())
    src = "".join((source or "").split())
    if not tx or not src:
        return False
    if tx == src or src in tx:
        return True
    tc = {c for c in tx if 0x4E00 <= ord(c) <= 0x9FFF}
    sc = {c for c in src if 0x4E00 <= ord(c) <= 0x9FFF}
    if tx in src and (len(tc) >= 2 or len(sc) <= 2):
        return True
    overlap = len(tc & sc)
    need = 1 if len(sc) <= 2 else 2
    return overlap >= need and overlap / max(1, min(len(tc), len(sc))) >= 0.5


def rapidocr_labels() -> Any:
    """OCR lỏng cho nhãn / 1 chữ — default RapidOCR bỏ sót glyph nhỏ."""
    return _rapidocr_labels()


def ocr_mid_labels(
    frame_bgr: Any,
    ocr: Any,
    source: str = "",
) -> tuple[list[tuple[int, int, int, int]], str]:
    """OCR nhãn nhỏ / graphic (bỏ hardsub đáy + title dọc full giữa)."""
    h, w = frame_bgr.shape[:2]
    y1 = int(h * 0.82)
    roi = frame_bgr[0:y1, :]
    if roi.size == 0:
        return [], ""
    # dùng engine truyền vào — không tạo RapidOCR mới mỗi khung
    eng = ocr if ocr is not None else rapidocr_labels()
    result, _ = eng(roi)
    boxes: list[tuple[int, int, int, int]] = []
    texts: list[str] = []
    src = (source or "").strip()
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)
        if cjk < 1 and len(text) < 2:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bx0, by0 = int(min(xs)), int(min(ys))
            bx1, by1 = int(max(xs)), int(max(ys))
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if cjk == 1:
            if bw < max(10, w * 0.012) or bh < max(10, h * 0.012):
                continue
        elif bw < 4 or bh < 4:
            continue
        cx, cy = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5
        if cy > h * 0.72 and bw > w * 0.28 and bh < h * 0.08:
            continue
        # title dọc full giữa (mở clip) — pass vertical lo; cột nhãn hẹp vẫn giữ
        if bh > h * 0.45 and bh > bw * 1.8 and w * 0.35 < cx < w * 0.65:
            continue
        # cột dọc nguyên liệu: cao hẹp — bắt cả box từng chữ (sẽ gộp sau)
        tall_col = bh > bw * 1.15 and bw < w * 0.25 and bh < h * 0.55
        side = cx < w * 0.42 or cx > w * 0.68
        small = bw < w * 0.45 and bh < h * 0.28
        single = cjk >= 1 and len(text.strip()) <= 6 and bw < w * 0.35 and bh < h * 0.22
        mid_g = (
            h * 0.20 < cy < h * 0.72
            and bw < w * 0.55
            and bh < h * 0.30
            and cjk <= 14
        )
        matched = bool(src) and _source_matches(text, src)
        if not (side or small or single or matched or tall_col or mid_g):
            continue
        # nới nhẹ bbox OCR (unclip hay cắt stroke)
        pad = max(4, int(min(bw, bh) * 0.12))
        boxes.append(
            (
                max(0, bx0 - pad),
                max(0, by0 - pad),
                min(w, bx1 + pad),
                min(h, by1 + pad),
            )
        )
        texts.append(text)
    if not boxes:
        return [], ""
    if src:
        matched_boxes = [
            (b, t)
            for b, t in zip(boxes, texts)
            if _source_matches(t, src)
        ]
        if matched_boxes:
            boxes = [b for b, _ in matched_boxes]
            texts = [t for _, t in matched_boxes]
        else:
            return [], ""
    # gộp chữ cùng cột trước khi trả
    from .labels import expand_label_column

    boxes = expand_label_column(boxes, w, h)
    return boxes, _ocr_join_lines(texts)


def ocr_mid_vertical(
    frame_bgr: Any, ocr: Any, source: str = ""
) -> tuple[list[tuple[int, int, int, int]], str]:
    """OCR vùng giữa khung cho chữ dọc (tiêu đề)."""
    import cv2

    h, w = frame_bgr.shape[:2]
    x0, x1 = int(w * 0.05), int(w * 0.75)
    y0, y1 = int(h * 0.12), int(h * 0.88)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return [], ""
    scale = 1.5
    img = cv2.resize(roi, (int(roi.shape[1] * scale), int(roi.shape[0] * scale)))
    result, _ = ocr(img)
    found: list[tuple[tuple[int, int, int, int], str]] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        try:
            xs = [float(p[0]) / scale + x0 for p in box]
            ys = [float(p[1]) / scale + y0 for p in box]
            bx0, by0 = int(min(xs)), int(min(ys))
            bx1, by1 = int(max(xs)), int(max(ys))
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 4 or bh < 4:
            continue
        if bh < bw * 0.9 and len(text) > 6:
            continue
        found.append(((bx0, by0, bx1, by1), text))
    if not found:
        return [], ""

    src = (source or "").strip()
    anchors = [
        (box, text)
        for box, text in found
        if not src or _source_matches(text, src)
    ]
    if src and anchors:
        # Giữ thêm cột Latin sát title (HUAMUZ), nhưng bỏ hardsub ngang ở giữa
        # khung. Trước đây union tất cả kết quả làm bbox lệch phải và cao quá mức.
        selected = list(anchors)
        for box, text in found:
            if (box, text) in selected:
                continue
            bx0, by0, bx1, by1 = box
            bw, bh = bx1 - bx0, by1 - by0
            if bh <= bw * 1.35:
                continue
            for ab, _at in anchors:
                ax0, ay0, ax1, ay1 = ab
                horizontal_gap = max(0, ax0 - bx1, bx0 - ax1)
                vertical_gap = max(0, ay0 - by1, by0 - ay1)
                if horizontal_gap <= w * 0.08 and vertical_gap <= h * 0.08:
                    selected.append((box, text))
                    break
        found = selected
    elif src:
        return [], ""

    boxes = [box for box, _text in found]
    texts = [text for _box, text in found]
    return boxes, _ocr_join_lines(texts)


def ocr_mid_hardsub_boxes(
    frame_bgr: Any,
    ocr: Any,
    source: str = "",
) -> tuple[list[tuple[int, int, int, int]], str]:
    """OCR caption ngang bất kỳ Y (giữa hoặc đáy) — khớp source, không hardcode vị trí.

    Ưu tiên dòng đủ glyph/trái-phải (không lấy mảnh OCR lệch một bên).
    """
    hit = _probe_mid_hardsub(frame_bgr, ocr, source=source)
    if not hit:
        return [], ""
    _score, box, text = hit
    return [box], text


def _normalize_analysis_region(
    raw: Any,
) -> tuple[float, float, float, float] | None:
    """Parse {x,y,w,h} 0–1 → clamp. None nếu không hợp lệ."""
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw.get("x", 0))
        y = float(raw.get("y", 0))
        w = float(raw.get("w", 0))
        h = float(raw.get("h", 0))
    except (TypeError, ValueError):
        return None
    x = max(0.0, min(0.95, x))
    y = max(0.0, min(0.95, y))
    w = max(0.05, min(1.0 - x, w))
    h = max(0.05, min(1.0 - y, h))
    return (x, y, w, h)


def _decode_frames_batch(
    video: Path,
    times: list[float],
    fps: float,
    width: int,
    height: int,
    *,
    use_cuda: bool,
):
    """Yield (frame_index, frame_bgr) cho cac moc — ffmpeg doc tuan tu theo LO.

    OpenCV seek tung moc giai ma lai tu keyframe va tu bung ~8/12 core CPU
    (do duoc), lam treo may. ffmpeg + select filter chi giai ma mot lan; kem
    -hwaccel cuda thi phan giai ma nam tren GPU.

    ponytail: ffmpeg khong parse noi select expr qua ~100 nhanh eq() -> chia lo
    <=48 moc, moi lo input-seek toi moc dau (pad 1s de chac co keyframe).
    Nang cap: dung -skip_frame/-vf fps neu can lay day dac hon.
    """
    if fps <= 0 or width <= 0 or height <= 0 or not times:
        return
    frame_bytes = width * height * 3
    wanted = sorted({max(0, int(round(t * fps))) for t in times})
    if not wanted:
        return
    batch = 48
    seek_pad = 1.0
    for start_i in range(0, len(wanted), batch):
        part = wanted[start_i : start_i + batch]
        seek_t = max(0.0, part[0] / fps - seek_pad)
        base_n = int(round(seek_t * fps))
        rel = [max(0, idx - base_n) for idx in part]
        expr = "+".join("eq(n\\,%d)" % i for i in rel)
        cmd = ["ffmpeg", "-v", "error", "-threads", "1"]
        if use_cuda:
            cmd += ["-hwaccel", "cuda"]
        if seek_t > 0.01:
            cmd += ["-ss", "%.3f" % seek_t]
        cmd += [
            "-i", str(video),
            "-vf", "select='%s'" % expr,
            "-vsync", "0",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
        ]
        kw: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kw["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        proc = subprocess.Popen(cmd, **kw)
        try:
            import numpy as np

            assert proc.stdout is not None
            for idx in part:
                buf = bytearray()
                while len(buf) < frame_bytes:
                    chunk = proc.stdout.read(frame_bytes - len(buf))
                    if not chunk:
                        break
                    buf.extend(chunk)
                if len(buf) < frame_bytes:
                    break
                yield idx, np.frombuffer(bytes(buf), dtype=np.uint8).reshape(
                    (height, width, 3)
                )
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except OSError:
                pass
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass


def _probe_mid_hardsub(
    frame_bgr: Any,
    ocr: Any,
    source: str = "",
    analysis_region: Any = None,
    layout: str = "horizontal",
) -> tuple[float, tuple[int, int, int, int], str] | None:
    """Trả (score, xyxy, text) — score cao = khớp source + đủ bề ngang."""
    h, w = frame_bgr.shape[:2]
    reg = _normalize_analysis_region(analysis_region)
    if reg:
        rx, ry, rw, rh = reg
        x0, y0 = int(w * rx), int(h * ry)
        x1, y1 = int(w * (rx + rw)), int(h * (ry + rh))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, max(x0 + 8, x1)), min(h, max(y0 + 8, y1))
    else:
        # Bottom hardsubs commonly extend to 95–98% of the frame.  Cropping
        # at 92% cut the glyphs before OCR and forced an inaccurate fallback.
        y0, y1 = int(h * 0.12), h
        x0, x1 = int(w * 0.04), int(w * 0.96)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    eng = ocr if ocr is not None else rapidocr_labels()
    result, _ = eng(roi)
    src = (source or "").strip()
    src_cjk = _cjk_len(src)
    cands: list[tuple[float, tuple[int, int, int, int], str]] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)
        is_sub = bool(src and len(text) >= 2 and text.lower() in src.lower())
        
        if cjk < 1 or cjk > 28:
            if not is_sub:
                continue
        # mảnh quá thiếu so với Whisper — bỏ; dòng dài hơn thì GIỮ (Whisper cắt nửa hardsub)
        if not is_sub and src_cjk >= 3 and cjk < max(2, int(src_cjk * 0.55)):
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            # Keep the detector's exact vertical polygon here.  The shared
            # caption-box step adds one symmetric, height-aware ink bleed;
            # padding here as well made bottom boxes loose and off-centre.
            bx0 = x0 + int(min(xs)) - 10
            by0 = y0 + int(min(ys))
            bx1 = x0 + int(max(xs)) + 10
            by1 = y0 + int(max(ys))
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 8 or bh < 8:
            continue
        if bh > h * 0.24:
            continue
        if layout != "vertical":
            if bh > bw * 1.35:
                continue
            cy = (by0 + by1) * 0.5
            if not (h * 0.12 < cy < h * 0.995):
                continue
            # quá hẹp so với Whisper — bỏ; rộng hơn OK (che full dòng trên khung)
            if not is_sub and src_cjk >= 3 and bw < src_cjk * max(28, int(w * 0.028)):
                continue
        bb = (max(0, bx0), max(0, by0), min(w, bx1), min(h, by1))
        score = float(cjk) + (bw / max(1, w)) * 4
        if src:
            from .extract import _ocr_sim

            if _source_matches(text, src):
                score = 30 + cjk + (bw / max(1, w)) * 6
            # ưu tiên dòng hardsub đủ bề ngang khi Whisper chỉ có nửa câu
            if cjk > src_cjk + 1:
                score += 14 + (bw / max(1, w)) * 10
            else:
                sim = float(_ocr_sim(text, src) or 0)
                min_sim = 0.28 if src_cjk <= 8 else 0.42
                if sim < min_sim:
                    continue
                score = sim * 22 + cjk - abs(cjk - src_cjk) * 2
                score += (bw / max(1, w)) * 6
            if cjk == src_cjk:
                score += 10
            elif abs(cjk - src_cjk) <= 1:
                score += 4
        cands.append((score, bb, text))
    if not cands:
        return None
    cands.sort(key=lambda x: -x[0])
    best_score, best_bb, best_text = cands[0]
    
    # Gom (union) bbox của các mảnh khác cũng thuộc về source (ví dụ chữ tiếng Anh kế bên chữ Hán)
    if src:
        ux0, uy0, ux1, uy1 = best_bb
        for score, bb, text in cands[1:]:
            if len(text) >= 1 and text.lower() in src.lower():
                bx0, by0, bx1, by1 = bb
                ux0 = min(ux0, bx0)
                uy0 = min(uy0, by0)
                ux1 = max(ux1, bx1)
                uy1 = max(uy1, by1)
        best_bb = (ux0, uy0, ux1, uy1)
        
    return best_score, best_bb, best_text


def _bbox_fits_source(bb: dict[str, Any], source: str, fw: int) -> bool:
    """False nếu bbox quá hẹp so với CJK. Rộng hơn OK — che full dòng hardsub."""
    cjk = _cjk_len(source)
    if cjk < 2:
        return True
    try:
        w = float(bb.get("w") or 0)
    except (TypeError, ValueError):
        return False
    lo = cjk * max(28.0, fw * 0.025)
    return w >= lo


def _xyxy_to_seg_bbox(
    x0: int, y0: int, x1: int, y1: int, fw: int, fh: int, pad: int = 6,
    *,
    pad_x: int | None = None,
    pad_y: int | None = None,
) -> dict[str, int]:
    px = pad if pad_x is None else pad_x
    py = pad if pad_y is None else pad_y
    x0 = max(0, x0 - px)
    y0 = max(0, y0 - py)
    x1 = min(fw, x1 + px)
    y1 = min(fh, y1 + py)
    return {
        "x": int(x0),
        "y": int(y0),
        "w": max(8, int(x1 - x0)),
        "h": max(8, int(y1 - y0)),
    }


def _bbox_cy_frac(bbox: dict[str, Any] | None, fh: int) -> float | None:
    if not isinstance(bbox, dict) or fh <= 0:
        return None
    try:
        y = float(bbox.get("y") or 0)
        h = float(bbox.get("h") or 0)
    except (TypeError, ValueError):
        return None
    return (y + h * 0.5) / fh


def _cjk_len(text: str) -> int:
    return sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)


def _similar_hardsub_len(a: str, b: str) -> bool:
    """Whisper phồn thể / OCR giản / lệch 1 glyph — vẫn cùng độ dài khoảng."""
    ca, cb = _cjk_len(a), _cjk_len(b)
    if ca < 1 or cb < 1:
        return False
    return abs(ca - cb) <= max(1, ca // 3)


def _layout_from_cy(cy: float, fh: int, fw: int = 1080) -> str:
    """mid = free-position caption; horizontal = aspect-aware bottom band."""
    if fw <= 0 or fh <= 0:
        return "horizontal"
    if fh * 0.18 < cy < fh * mid_bottom_cutoff(fw, fh):
        return "mid"
    return "horizontal"


def _retag_layout_from_bbox(seg: dict[str, Any], fh: int, fw: int = 1080) -> None:
    """Gắn layout theo bbox đã đo — không để None/horizontal khi chữ ở giữa."""
    lay = str(seg.get("layout") or "")
    if lay in ("vertical", "label"):
        return
    bbox = seg.get("bbox") if isinstance(seg.get("bbox"), dict) else None
    cy = _bbox_cy_frac(bbox, fh)
    if cy is None:
        return
    seg["layout"] = _layout_from_cy(cy * fh, fh, fw)


def _apply_caption_box(
    seg: dict[str, Any],
    box: tuple[int, int, int, int],
    fw: int,
    fh: int,
) -> None:
    """Gắn bbox OCR; layout tag chỉ theo cy đo được."""
    x0, y0, x1, y1 = box
    cy = (y0 + y1) * 0.5
    ow = max(1, x1 - x0)
    oh = max(1, y1 - y0)
    lay = str(seg.get("layout") or "")
    if lay not in ("vertical", "label"):
        # ponytail: cap abnormal single-lane OCR polygons; if multi-row source
        # captions are supported later, derive this from the individual rows.
        max_caption_h = max(24, int(round(min(fw, fh) * 0.085)))
        if oh > max_caption_h:
            y0 = max(0, min(fh - max_caption_h, int(round(cy - max_caption_h * 0.5))))
            y1 = y0 + max_caption_h
            oh = max_caption_h
    if lay == "vertical":
        pad_x = max(12, int(round(fw * 0.015)), int(round(ow * 0.15)))
        pad_top = max(12, int(round(fh * 0.015)), int(round(oh * 0.05)))
        pad_bot = max(12, int(round(fh * 0.015)), int(round(oh * 0.05)))
    else:
        pad_x = max(8, int(round(fw * 0.008)), int(round(ow * 0.04)))
        # RapidOCR may return only the bright glyph body. Scale the bleed with
        # glyph height and keep it symmetric; the old top cap (10px) missed
        # thick black/white outlines while shifting the box centre downward.
        pad_y = max(6, min(18, int(round(oh * 0.16))))
        pad_top = pad_bot = pad_y
    x0p = max(0, x0 - pad_x)
    y0p = max(0, y0 - pad_top)
    x1p = min(fw, x1 + pad_x)
    y1p = min(fh, y1 + pad_bot)
    seg["bbox"] = {
        "x": int(x0p),
        "y": int(y0p),
        "w": max(8, int(x1p - x0p)),
        "h": max(8, int(y1p - y0p)),
    }
    # True = OCR auto (editor được fit chữ lại). False chỉ khi user kéo/Áp Y.
    seg["bboxInherited"] = True
    saved = seg["bbox"]
    old_lay = str(seg.get("layout") or "")
    if old_lay not in ("vertical", "label"):
        seg["layout"] = _layout_from_cy(cy, fh, fw)
    seg.pop("captionLayout", None)


def _inherit_caption_bboxes(
    segments: list[dict[str, Any]], fh: int, fw: int = 1080
) -> int:
    """ponytail: OCR fail 1 câu → mượn bbox câu kề (cùng video hay cùng dải). Ceiling: video đổi vị trí giữa shot."""
    n = 0
    caps: list[dict[str, int] | None] = []
    for seg in segments:
        bb = seg.get("bbox")
        lay = str(seg.get("layout") or "")
        cy = _bbox_cy_frac(bb if isinstance(bb, dict) else None, fh)
        # Donor = có bbox probe/OCR (bboxInherited True|False đều ok; chỉ loại thiếu box)
        if (
            lay in ("mid", "horizontal")
            and isinstance(bb, dict)
            and cy is not None
            and int(bb.get("w") or 0) > 0
            and int(bb.get("h") or 0) > 0
            and not seg.get("_fromInherit")
        ):
            caps.append(
                {
                    "x": int(bb["x"]),
                    "y": int(bb["y"]),
                    "w": int(bb["w"]),
                    "h": int(bb["h"]),
                    "cjk": _cjk_len(str(seg.get("source") or "")),
                }
            )
        else:
            caps.append(None)

    glyph_widths = sorted(
        cap["w"] / max(1, cap["cjk"])
        for cap in caps
        if cap is not None and cap["cjk"] > 0
    )
    median_glyph_w = (
        glyph_widths[len(glyph_widths) // 2] if glyph_widths else 0.0
    )

    def nearest(i: int) -> dict[str, int] | None:
        for d in range(1, len(segments)):
            for j in (i - d, i + d):
                if 0 <= j < len(caps) and caps[j] is not None:
                    return caps[j]
        return None

    for i, seg in enumerate(segments):
        lay = str(seg.get("layout") or "horizontal")
        if lay in ("vertical", "label"):
            continue
        src = str(seg.get("source") or "")
        if _cjk_len(src) < 1:
            continue
        bb = seg.get("bbox")
        if isinstance(bb, dict) and bb.get("w") and bb.get("h"):
            continue
        donor = nearest(i)
        if not donor:
            continue
        # Chỉ OCR ba câu: mượn vị trí từ câu mẫu nhưng ước lượng lại chiều
        # ngang theo số glyph nguồn. Không sao chép nguyên bbox dài sang câu ngắn.
        target_cjk = max(1, _cjk_len(src))
        glyph_w = median_glyph_w or donor["w"] / max(1, int(donor.get("cjk") or 1))
        glyph_w = max(donor["h"] * 0.42, min(donor["h"] * 0.95, glyph_w))
        bleed = max(12, round(glyph_w * 0.45))
        width = max(48, min(fw, round(target_cjk * glyph_w + bleed * 2)))
        cx = donor["x"] + donor["w"] * 0.5
        x = max(0, min(fw - width, round(cx - width * 0.5)))
        cy = donor["y"] + donor["h"] * 0.5
        seg["bbox"] = {
            "x": x,
            "y": donor["y"],
            "w": width,
            "h": donor["h"],
        }
        seg["bboxInherited"] = True
        seg["_fromInherit"] = True
        # Giữ mid/horizontal theo Y donor — không ép horizontal khi mượn từ mid
        seg["layout"] = _layout_from_cy(cy, fh, fw)
        seg.pop("captionLayout", None)
        n += 1
    return n


def _ensure_cover_times(
    segments: list[dict[str, Any]], video_end: float | None
) -> None:
    """Gán coverStart/coverEnd mặc định khi segment chưa có."""
    from .cover_timing import attach_cover_times

    for seg in segments:
        if seg.get("coverStart") is not None and seg.get("coverEnd") is not None:
            continue
        lay = str(seg.get("layout") or "horizontal")
        if lay in ("vertical", "label"):
            continue
        if _cjk_len(str(seg.get("source") or "")) < 1:
            continue
        attach_cover_times(seg, video_end=video_end)


def _three_point_segments(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Chọn tối đa ba cue đại diện: đầu, giữa và cuối video."""
    if len(segments) <= 3:
        return list(segments)
    return [segments[i] for i in (0, len(segments) // 2, len(segments) - 1)]


def attach_speech_hardsub_boxes(
    video: Path | str,
    segments: list[dict[str, Any]],
    *,
    only_missing: bool = True,
    project_id: str | None = None,
    stable: bool = False,
    analysis_region: Any = None,
    status_workers: int = 0,
) -> int:
    """Whisper giữ timecode; OCR đo bbox.

    Luôn OCR 1 frame giữa mỗi mốc (chế độ «đầu•giữa•cuối» đã bỏ: chậm gấp 3
    mà bbox gần như không đổi). `stable` giữ lại cho call site cũ — bỏ qua.
    analysis_region: {x,y,w,h} 0–1 — thu hẹp ROI OCR (nhanh + ít nhiễu).

    Frozen desktop: chạy trong subprocess .venv-runtime — crash OpenCV/RapidOCR
    không kéo tắt VideoClone.exe. Không fallback in-process — native ext crash
    trong frozen parent kill cả app, không bắt được bằng try/except.
    """
    n = _locate_via_runtime_subprocess(
        video,
        segments,
        only_missing=only_missing,
        stable=stable,
        analysis_region=analysis_region,
        project_id=project_id,
        status_workers=status_workers,
    )
    if project_id:
        from pipeline.core.jobs import Cancelled as _C, is_cancelled as _isc

        if _isc(project_id):
            raise _C("locate cancelled")
    if n == 0 and analysis_region:
        # A stale/manual ROI can miss the actual subtitle band completely.
        # Retry once on the full frame before allowing the UI to use a bottom fallback.
        try:
            from pipeline.core.app_log import append_log

            append_log("[locate-subprocess] ROI found 0 boxes; retry full frame")
        except Exception:
            pass
        n = _locate_via_runtime_subprocess(
            video,
            segments,
            only_missing=only_missing,
            stable=stable,
            analysis_region=None,
            project_id=project_id,
            status_workers=status_workers,
        )
    # Worker bi kill boi «Huy» -> KHONG duoc fallback chay lai in-process
    # (dung nguyen nhan "huy roi CPU van chay").
    if project_id:
        from pipeline.core.jobs import Cancelled, is_cancelled

        if is_cancelled(project_id):
            raise Cancelled("locate cancelled")
    if n is not None and n >= 0:
        return n
    if getattr(sys, "frozen", False):
        # subprocess không chạy được — skip an toàn thay vì crash
        try:
            from pipeline.core.app_log import append_log
            append_log("[locate] no runtime subprocess — skip OCR boxes (keep translation)")
        except Exception:
            pass
        return 0
    return attach_speech_hardsub_boxes_inprocess(
        video,
        segments,
        only_missing=only_missing,
        project_id=project_id,
        stable=stable,
        analysis_region=analysis_region,
        status_workers=status_workers,
    )


def attach_speech_hardsub_boxes_inprocess(
    video: Path | str,
    segments: list[dict[str, Any]],
    *,
    only_missing: bool = True,
    project_id: str | None = None,
    stable: bool = False,
    analysis_region: Any = None,
    status_workers: int = 0,
) -> int:
    """In-process locate (dev + runtime worker). Không gọi từ frozen parent nếu tránh được."""
    # Frozen parent fallback: NEVER bare ``import cv2``.
    try:
        from pipeline.core.runtime_site import ensure_cv2, prepare_cv2_import_path

        prepare_cv2_import_path()
        cv2 = ensure_cv2()
    except Exception as e:
        if not getattr(sys, "frozen", False):
            try:
                import cv2  # type: ignore
            except Exception:
                try:
                    from pipeline.core.app_log import append_exception

                    append_exception("[locate] ensure_cv2 failed — skip OCR boxes", e)
                except Exception:
                    pass
                return 0
        else:
            try:
                from pipeline.core.app_log import append_exception

                append_exception("[locate] ensure_cv2 failed — skip OCR boxes", e)
            except Exception:
                pass
            return 0

    path = Path(video)
    if not path.is_file() or not segments:
        return 0
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return 0
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    # Use the decoded frame shape, not a portrait default or container hint.
    # This also follows OpenCV's applied rotation, so bbox coordinates and
    # aspect classification are measured in the same coordinate system.
    ok_first, first_frame = cap.read()
    if ok_first and first_frame is not None and first_frame.size:
        fh, fw = first_frame.shape[:2]
    if fw <= 0 or fh <= 0:
        cap.release()
        return 0
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    video_end = (frame_total / fps) if frame_total > 0 else None
    roi = _normalize_analysis_region(analysis_region)

    # Bbox already present without a baked caption layout came from OCR, not a
    # user drag. Keep its geometry but preserve the auto-layout semantics so
    # the editor can expand translated text at the shared lane font.
    for seg in segments:
        bb = seg.get("bbox")
        if isinstance(bb, dict) and bb.get("w") and bb.get("h"):
            cl = seg.get("captionLayout")
            manual = (
                seg.get("bboxInherited") is False
                and isinstance(cl, dict)
                and isinstance(cl.get("lines"), list)
                and bool(cl.get("lines"))
            )
            seg["bboxInherited"] = False if manual else True
            _retag_layout_from_bbox(seg, fh, fw)

    filtered: list[dict[str, Any]] = []
    for seg in segments:
        lay = str(seg.get("layout") or "horizontal")
        src = str(seg.get("source") or "")
        if _cjk_len(src) < 1:
            continue
        bb = seg.get("bbox")
        if only_missing and isinstance(bb, dict) and bb.get("w") and bb.get("h"):
            cy = _bbox_cy_frac(bb, fh)
            if cy is not None and _bbox_fits_source(bb, src, fw):
                _retag_layout_from_bbox(seg, fh, fw)
                continue
        filtered.append(seg)
    if not filtered:
        n_inh = _inherit_caption_bboxes(segments, fh, fw)
        _ensure_cover_times(segments, video_end)
        cap.release()
        return n_inh

    _device_label = ["CPU"]
    try:
        ocr = rapidocr_labels()
        try:
            from .extract_parts.runtime import engine_device_label, engine_providers

            _device_label[0] = engine_device_label(ocr)
            from pipeline.core.app_log import append_log

            append_log(
                f"[locate] OCR device={_device_label[0]} providers={engine_providers(ocr)}"
            )
        except Exception:
            pass
    except ImportError:
        cap.release()
        n = _inherit_caption_bboxes(segments, fh, fw)
        _ensure_cover_times(segments, video_end)
        return n

    def _read_frame_at(t: float) -> Any | None:
        """Seek + decode 1 frame - chi chay TUAN TU tren main thread (cv2 khong thread-safe)."""
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        return frame if ok else None

    # MOT engine dung chung, OCR tuan tu. Do thuc te tren GTX 1660S: 4 session
    # RapidOCR CUDA song song = 98s cho 3 probe, 1 session = 2.4s (tranh chap
    # cuDNN + VRAM). Toc do den tu prefetch decode chu khong phai da luong OCR.
    def _ocr_probe(
        t: float, frame: Any, src: str, lay: str
    ) -> tuple[float, float, tuple[int, int, int, int], str] | None:
        hit = _probe_mid_hardsub(
            frame,
            ocr,
            source=src,
            analysis_region=(
                {"x": roi[0], "y": roi[1], "w": roi[2], "h": roi[3]} if roi else None
            ),
            layout=lay,
        )
        if not hit:
            return None
        score, box, text = hit
        return (float(t), float(score), box, text)

    def _report(done: int, total: int) -> None:
        if not project_id or total <= 0:
            return
        prev = getattr(_report, "_prev_done", -1)
        if done == prev:
            return
        # ponytail: ghi meta mỗi câu = chậm; heartbeat mỗi 4 câu + đầu/cuối
        if done not in (1, total) and done % 4 != 0:
            return
        _report._prev_done = done
        pct = 95 + min(3, int(3 * done / total))
        from ..core.project import set_status
        from ..core.resources import progress_msg

        set_status(
            project_id,
            step="translate",
            progress=pct,
            # «Định vị OCR · 3/10 · GPU · 8 luồng»
            message=progress_msg(
                "Định vị OCR",
                done,
                total,
                extra=_device_label[0],
                workers=status_workers or None,
            ),
            running=True,
        )

    from .cover_timing import attach_cover_times

    attached = 0
    # ponytail: OCR tối đa 64 mốc; cue còn lại kế thừa mốc gần nhất và tính
    # lại bề ngang theo số glyph CJK của chính nó.
    probes = _spread_probes(filtered, _QUICK_PROBE_LIMIT)
    total = len(probes)
    anchor_boxes: list[tuple[int, int, int, int]] = []
    # Gom job: 1 frame giữa mỗi cue (một chế độ duy nhất).
    probe_meta: list[tuple[str, str]] = []
    jobs: list[tuple[int, float]] = []
    for si, seg in enumerate(probes):
        s0 = float(seg.get("start") or 0)
        e0 = float(seg.get("end") or s0)
        if e0 <= s0:
            e0 = s0 + 0.4
        probe_meta.append(
            (str(seg.get("source") or "").strip(), str(seg.get("layout") or "horizontal"))
        )
        jobs.append((si, s0 + max(0.2, e0 - s0) * 0.5))

    # Prefetch decode: 1 thread doc/seek frame truoc (cv2 chi dung trong thread
    # nay - khong thread-safe), main thread OCR. Decode chong len OCR thay vi
    # noi tiep => nhanh hon ma GPU van 1 session duy nhat.
    hits_by_probe: dict[
        int, list[tuple[float, tuple[float, float, tuple[int, int, int, int], str]]]
    ] = {}
    jobs_per_probe: dict[int, int] = {}
    for si, _t in jobs:
        jobs_per_probe[si] = jobs_per_probe.get(si, 0) + 1
    done_per_probe: dict[int, int] = {}
    done_probes = 0
    try:
        import queue as _queue

        frame_q: _queue.Queue = _queue.Queue(maxsize=3)
        decode_err: list[BaseException] = []

        def _decoder() -> None:
            try:
                # Uu tien MOT process ffmpeg (NVDEC neu GPU san sang): giai ma
                # tuan tu, CPU ~1 core thay vi ~8 core cua OpenCV seek.
                by_index: dict[int, list[tuple[int, float]]] = {}
                if fps > 0:
                    for si, t in jobs:
                        by_index.setdefault(max(0, int(round(t * fps))), []).append((si, t))
                served: set[int] = set()
                if by_index:
                    try:
                        from pipeline.core.media import nvdec_available

                        use_cuda = bool(nvdec_available(Path(video)))
                    except Exception:
                        use_cuda = False
                    try:
                        for idx, frame in _decode_frames_batch(
                            Path(video),
                            [t for _si, t in jobs],
                            fps,
                            fw,
                            fh,
                            use_cuda=use_cuda,
                        ):
                            check_cancel(project_id)
                            for si, t in by_index.get(idx, []):
                                frame_q.put((si, t, frame))
                            served.add(idx)
                    except Cancelled:
                        raise
                    except Exception:
                        pass
                # Moc nao ffmpeg khong tra duoc -> fallback OpenCV seek
                for idx, pairs in by_index.items():
                    if idx in served:
                        continue
                    for si, t in pairs:
                        check_cancel(project_id)
                        frame_q.put((si, t, _read_frame_at(t)))
                if not by_index:
                    for si, t in jobs:
                        check_cancel(project_id)
                        frame_q.put((si, t, _read_frame_at(t)))
            except BaseException as e:  # noqa: BLE001 - day len main thread
                decode_err.append(e)
            finally:
                frame_q.put(None)

        dec_thread = threading.Thread(
            target=_decoder, name="ocr-locate-decode", daemon=True
        )
        dec_thread.start()
        while True:
            item = frame_q.get()
            if item is None:
                break
            si, t, fr = item
            check_cancel(project_id)
            if fr is not None:
                try:
                    hit = _ocr_probe(t, fr, *probe_meta[si])
                except Exception:
                    hit = None
                if hit:
                    hits_by_probe.setdefault(si, []).append((t, hit))
            done_per_probe[si] = done_per_probe.get(si, 0) + 1
            if done_per_probe[si] == jobs_per_probe[si]:
                done_probes += 1
                _report(min(done_probes, total), total)
        dec_thread.join(timeout=5)
        if decode_err:
            raise decode_err[0]

        for si, seg in enumerate(probes):
            ordered_hits = [
                h for _t, h in sorted(hits_by_probe.get(si, []), key=lambda x: x[0])
            ]
            stable_box = ordered_hits[0][2] if ordered_hits else None
            if stable_box is None:
                continue
            _apply_caption_box(seg, stable_box, fw, fh)
            seg["_probeAnchored"] = True
            seg.pop("captionLayout", None)
            attach_cover_times(seg, video_end=video_end)
            anchor_boxes.append(stable_box)
            attached += 1
        _report(total, total)
    finally:
        cap.release()
    attached += _inherit_caption_bboxes(segments, fh, fw)
    _ensure_cover_times(segments, video_end)
    for seg in segments:
        seg.pop("_probeAnchored", None)
        seg.pop("_fromInherit", None)
        _retag_layout_from_bbox(seg, fh, fw)
    return attached
