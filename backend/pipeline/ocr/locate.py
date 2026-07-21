"""OCR định vị hardsub / nhãn / title dọc trên khung (dùng lúc burn)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .extract import _ocr_join_lines, _rapidocr_labels
from .labels import pick_label_box


def _runtime_python() -> Path | None:
    """Desktop .venv-runtime python — clean OpenCV, unlike frozen parent."""
    if not getattr(sys, "frozen", False):
        return None
    home = (os.environ.get("VIDEO_CLONE_HOME") or "").strip()
    if not home:
        return None
    py = Path(home) / ".venv-runtime" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    return py if py.is_file() else None


def _locate_via_runtime_subprocess(
    video: Path | str,
    segments: list[dict[str, Any]],
    *,
    only_missing: bool,
    stable: bool,
    analysis_region: Any,
) -> int | None:
    """Chạy attach_speech_hardsub_boxes trong process runtime riêng.

    Crash/native OpenCV recursion chỉ giết worker — app desktop sống.
    Trả số bbox gắn, hoặc None nếu không spawn được (gọi in-process).
    """
    py = _runtime_python()
    if py is None:
        return None
    # Bundle onedir: _MEIPASS/pipeline/… ; dev: backend/
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        pipeline_root = Path(meipass)
    else:
        pipeline_root = Path(__file__).resolve().parents[2]
    if not (pipeline_root / "pipeline" / "ocr" / "locate.py").is_file():
        return None

    payload = {
        "video": str(Path(video).resolve()),
        "segments": segments,
        "only_missing": only_missing,
        "stable": stable,
        "analysis_region": analysis_region,
    }
    # Worker script file — tránh quoting -c trên Windows
    worker_src = '''# vc-locate-worker
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
raw = Path(sys.argv[2]).read_text(encoding="utf-8-sig")
data = json.loads(raw)
from pipeline.ocr.locate import attach_speech_hardsub_boxes_inprocess
n = attach_speech_hardsub_boxes_inprocess(
    data["video"],
    data["segments"],
    only_missing=bool(data.get("only_missing", True)),
    project_id=None,
    stable=bool(data.get("stable", False)),
    analysis_region=data.get("analysis_region"),
)
Path(sys.argv[3]).write_text(
    json.dumps({"n": int(n), "segments": data["segments"]}, ensure_ascii=False),
    encoding="utf-8",
)
'''
    try:
        with tempfile.TemporaryDirectory(prefix="vc-locate-") as td:
            tdir = Path(td)
            pin = tdir / "in.json"
            pout = tdir / "out.json"
            wpy = tdir / "worker.py"
            pin.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            wpy.write_text(worker_src, encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(pipeline_root) + os.pathsep + env.get("PYTHONPATH", "")
            # Worker là python sạch — không frozen
            env.pop("VIDEO_CLONE_DESKTOP", None)
            cmd = [str(py), str(wpy), str(pipeline_root), str(pin), str(pout)]
            kw: dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "timeout": 900,
                "cwd": str(pipeline_root),
                "env": env,
            }
            if sys.platform == "win32":
                kw["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            proc = subprocess.run(cmd, **kw)
            if proc.returncode != 0 or not pout.is_file():
                err = (proc.stderr or proc.stdout or "")[-1500:]
                try:
                    from pipeline.core.app_log import append_log

                    append_log(
                        f"[locate-subprocess] fail code={proc.returncode}\n{err}",
                    )
                except Exception:
                    pass
                return 0  # fail soft — keep translation, no crash parent
            out = json.loads(pout.read_text(encoding="utf-8"))
            segs_out = out.get("segments")
            if isinstance(segs_out, list) and len(segs_out) == len(segments):
                for dst, src in zip(segments, segs_out):
                    if not isinstance(src, dict) or not isinstance(dst, dict):
                        continue
                    for k in (
                        "bbox",
                        "bboxInherited",
                        "layout",
                        "captionLayout",
                        "coverStart",
                        "coverEnd",
                        "_probeAnchored",
                    ):
                        if k in src:
                            dst[k] = src[k]
                        elif k in dst and k.startswith("_"):
                            dst.pop(k, None)
            return int(out.get("n") or 0)
    except Exception as e:
        try:
            from pipeline.core.app_log import append_exception

            append_exception("[locate-subprocess] exception", e)
        except Exception:
            pass
        return 0


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


def _probe_mid_hardsub(
    frame_bgr: Any,
    ocr: Any,
    source: str = "",
    analysis_region: Any = None,
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
        y0, y1 = int(h * 0.12), int(h * 0.92)
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
        if cjk < 1 or cjk > 28:
            continue
        # mảnh quá thiếu so với Whisper — bỏ; dòng dài hơn thì GIỮ (Whisper cắt nửa hardsub)
        if src_cjk >= 3 and cjk < max(2, int(src_cjk * 0.55)):
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            # pad nhẹ — vừa stroke, không phình theo VI
            bx0 = x0 + int(min(xs)) - 10
            by0 = y0 + int(min(ys)) - 8
            bx1 = x0 + int(max(xs)) + 10
            by1 = y0 + int(max(ys)) + 8
        except (TypeError, ValueError, IndexError):
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 8 or bh < 8:
            continue
        if bh > h * 0.24:
            continue
        if bh > bw * 1.35:
            continue
        cy = (by0 + by1) * 0.5
        if not (h * 0.12 < cy < h * 0.92):
            continue
        # quá hẹp so với Whisper — bỏ; rộng hơn OK (che full dòng trên khung)
        if src_cjk >= 3 and bw < src_cjk * max(28, int(w * 0.028)):
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
    return cands[0]


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


def _layout_from_cy(cy: float, fh: int) -> str:
    """mid = hardsub giữa/giữa-dưới khung; horizontal = đáy cổ điển."""
    if fh <= 0:
        return "horizontal"
    # 0.78: TikTok mid thường thấp hơn 0.70 — không đẩy nhầm vào Caption
    if fh * 0.18 < cy < fh * 0.78:
        return "mid"
    return "horizontal"


def _retag_layout_from_bbox(seg: dict[str, Any], fh: int) -> None:
    """Gắn layout theo bbox đã đo — không để None/horizontal khi chữ ở giữa."""
    lay = str(seg.get("layout") or "")
    if lay in ("vertical", "label"):
        return
    bbox = seg.get("bbox") if isinstance(seg.get("bbox"), dict) else None
    cy = _bbox_cy_frac(bbox, fh)
    if cy is None:
        return
    # A very wide, shallow OCR strip is a normal horizontal subtitle even in
    # portrait video, where its Y center can still be below the 0.78 cutoff.
    if bbox and float(bbox.get("w") or 0) >= float(bbox.get("h") or 1) * 8:
        seg["layout"] = "horizontal"
        return
    seg["layout"] = _layout_from_cy(cy * fh, fh)


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
    # Pad mỏng ôm chữ — mid không phình H (chữ bé trong hộp cao)
    pad_x = max(3, int(round(fw * 0.003)), int(round(ow * 0.02)))
    pad_y = max(2, int(round(fh * 0.0015)), int(round(oh * 0.06)))
    # Hộp OCR quá cao: cắt H, ưu tiên giữ mép trên (cắt dư dưới — không center)
    max_h = max(32, min(int(round(fh * 0.065)), int(round(ow * 0.26))))
    if oh > max_h:
        y1 = y0 + max_h
        oh = max_h
        pad_y = max(2, min(5, int(round(oh * 0.05))))
    # Pad Y không đối xứng: trên mỏng, dưới vừa stroke (tránh dải vàng dưới chữ)
    pad_top = max(2, min(pad_y, 4))
    pad_bot = max(2, min(pad_y + 1, 5))
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
    seg["layout"] = (
        "horizontal"
        if saved["w"] >= max(1, saved["h"]) * 8
        else _layout_from_cy(cy, fh)
    )
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
        seg["layout"] = (
            "horizontal"
            if width >= max(1, donor["h"]) * 8
            else _layout_from_cy(cy, fh)
        )
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


def _sample_times_in_cue(s0: float, e0: float) -> list[float]:
    """Đầu / giữa / cuối cửa sổ cue (tránh sát biên fade)."""
    dur = max(0.05, e0 - s0)
    if dur < 0.35:
        return [s0 + dur * 0.5]
    return [
        s0 + dur * 0.12,
        s0 + dur * 0.50,
        s0 + dur * 0.88,
    ]


def _stable_box_from_hits(
    hits: list[tuple[float, float, tuple[int, int, int, int], str]],
    *,
    fw: int,
    fh: int,
) -> tuple[int, int, int, int] | None:
    """Majority cluster theo cy (+cx), median box — chống OCR nhảy lung tung.

    hits: (t, score, xyxy, text)
    """
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0][2]

    # Cluster theo cy (dải hardsub), tol ~3% chiều cao
    tol_y = max(8.0, fh * 0.03)
    tol_x = max(12.0, fw * 0.04)
    clusters: list[list[tuple[float, float, tuple[int, int, int, int], str]]] = []
    for h in hits:
        box = h[2]
        cx = (box[0] + box[2]) * 0.5
        cy = (box[1] + box[3]) * 0.5
        placed = False
        for cl in clusters:
            b0 = cl[0][2]
            cx0 = (b0[0] + b0[2]) * 0.5
            cy0 = (b0[1] + b0[3]) * 0.5
            if abs(cy - cy0) <= tol_y and abs(cx - cx0) <= tol_x * 2:
                cl.append(h)
                placed = True
                break
        if not placed:
            clusters.append([h])

    def cl_key(cl: list[tuple[float, float, tuple[int, int, int, int], str]]) -> tuple:
        # Ưu tiên cụm đông + điểm OCR cao
        return (len(cl), sum(x[1] for x in cl) / len(cl))

    best = max(clusters, key=cl_key)
    # Median từng cạnh trong cụm thắng
    xs0 = sorted(h[2][0] for h in best)
    ys0 = sorted(h[2][1] for h in best)
    xs1 = sorted(h[2][2] for h in best)
    ys1 = sorted(h[2][3] for h in best)
    mid = len(best) // 2
    return (xs0[mid], ys0[mid], xs1[mid], ys1[mid])


def _global_cy_band(
    boxes: list[tuple[int, int, int, int]],
    fh: int,
) -> float | None:
    """cy đa số của các probe — dùng neo inheritance ổn định."""
    if not boxes or fh <= 0:
        return None
    cys = sorted((b[1] + b[3]) * 0.5 for b in boxes)
    return cys[len(cys) // 2]


def _snap_inherited_y(
    segments: list[dict[str, Any]],
    anchor_cy: float | None,
    fh: int,
) -> None:
    """Neo Y chỉ cho Caption đáy (horizontal) kế thừa — không kéo CAP-MID xuống cuối."""
    if anchor_cy is None or fh <= 0:
        return
    # Anchor đáy: chỉ dùng nếu dải neo thực sự gần đáy (tránh mid median kéo horizontal)
    if anchor_cy < fh * 0.72:
        return
    tol = fh * 0.06
    for seg in segments:
        lay = str(seg.get("layout") or "horizontal")
        # mid/vertical/label: giữ Y probe — snap chung sẽ nhảy xuống hardsub đáy
        if lay in ("vertical", "label", "mid"):
            continue
        bb = seg.get("bbox")
        if not isinstance(bb, dict):
            continue
        # Chỉ snap bản inherit (không có probe riêng)
        if seg.get("_probeAnchored") or seg.get("bboxInherited") is False:
            continue
        if seg.get("bboxInherited") is not True:
            continue
        try:
            y = float(bb["y"])
            h = float(bb["h"])
        except (KeyError, TypeError, ValueError):
            continue
        cy = y + h * 0.5
        # Đã ở dải đáy thì thôi; mid-ish cy đừng kéo xuống
        if cy < fh * 0.70:
            continue
        if abs(cy - anchor_cy) <= tol:
            continue
        new_y = max(0, min(fh - h, round(anchor_cy - h * 0.5)))
        bb["y"] = int(new_y)
        seg["bbox"] = bb
        seg["layout"] = "horizontal"


def attach_speech_hardsub_boxes(
    video: Path | str,
    segments: list[dict[str, Any]],
    *,
    only_missing: bool = True,
    project_id: str | None = None,
    stable: bool = False,
    analysis_region: Any = None,
) -> int:
    """Whisper giữ timecode; OCR đo bbox.

    stable=False (mặc định, nhanh): 1 frame giữa mỗi mốc.
    stable=True: đầu•giữa•cuối + majority + neo Y (chậm hơn ~3×).
    analysis_region: {x,y,w,h} 0–1 — thu hẹp ROI OCR (nhanh + ít nhiễu).

    Frozen desktop: chạy trong subprocess .venv-runtime — crash OpenCV
    không kéo tắt VideoClone.exe.
    """
    if getattr(sys, "frozen", False):
        n = _locate_via_runtime_subprocess(
            video,
            segments,
            only_missing=only_missing,
            stable=stable,
            analysis_region=analysis_region,
        )
        if n is not None:
            return n
        # Không fallback in-process trên frozen — cv2 recursion kill cả app.
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
    )


def attach_speech_hardsub_boxes_inprocess(
    video: Path | str,
    segments: list[dict[str, Any]],
    *,
    only_missing: bool = True,
    project_id: str | None = None,
    stable: bool = False,
    analysis_region: Any = None,
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
    if fw <= 0 or fh <= 0:
        cap.release()
        return 0
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    video_end = (frame_total / fps) if frame_total > 0 else None
    roi = _normalize_analysis_region(analysis_region)

    # Migration cho project cũ: trước đây bbox của ba mốc bị copy cho gần như
    # toàn bộ câu và chưa có cờ provenance. Xóa một lần để tạo lại đúng anchor
    # OCR thật + bbox ước lượng; không đụng project chỉ có vài bbox kéo tay.
    legacy = [
        seg
        for seg in segments
        if isinstance(seg.get("bbox"), dict) and "bboxInherited" not in seg
    ]
    if len(legacy) >= max(4, len(segments) // 2):
        for seg in legacy:
            seg.pop("bbox", None)
            seg.pop("captionLayout", None)

    for seg in segments:
        _retag_layout_from_bbox(seg, fh)

    filtered: list[dict[str, Any]] = []
    for seg in segments:
        lay = str(seg.get("layout") or "horizontal")
        if lay in ("vertical", "label"):
            continue
        src = str(seg.get("source") or "")
        if _cjk_len(src) < 1:
            continue
        bb = seg.get("bbox")
        if only_missing and isinstance(bb, dict) and bb.get("w") and bb.get("h"):
            cy = _bbox_cy_frac(bb, fh)
            if cy is not None and _bbox_fits_source(bb, src, fw):
                _retag_layout_from_bbox(seg, fh)
                continue
        filtered.append(seg)
    if not filtered:
        n_inh = _inherit_caption_bboxes(segments, fh, fw)
        _ensure_cover_times(segments, video_end)
        cap.release()
        return n_inh

    try:
        ocr = rapidocr_labels()
    except ImportError:
        cap.release()
        n = _inherit_caption_bboxes(segments, fh, fw)
        _ensure_cover_times(segments, video_end)
        return n

    def _read_probe(
        t: float, src: str
    ) -> tuple[float, float, tuple[int, int, int, int], str] | None:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            return None
        hit = _probe_mid_hardsub(
            frame,
            ocr,
            source=src,
            analysis_region=(
                {"x": roi[0], "y": roi[1], "w": roi[2], "h": roi[3]} if roi else None
            ),
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

        mode_lab = "ổn định đầu•giữa•cuối" if stable else "nhanh 1 frame"
        set_status(
            project_id,
            step="translate",
            progress=pct,
            message=f"Định vị OCR {done}/{total} mốc ({mode_lab})…",
            running=True,
        )

    from .cover_timing import attach_cover_times

    attached = 0
    probes = _three_point_segments(filtered)
    total = len(probes)
    anchor_boxes: list[tuple[int, int, int, int]] = []
    try:
        for si, seg in enumerate(probes):
            _report(si + 1, total)
            s0 = float(seg.get("start") or 0)
            e0 = float(seg.get("end") or s0)
            if e0 <= s0:
                e0 = s0 + 0.4
            src = str(seg.get("source") or "").strip()
            dur = max(0.2, e0 - s0)
            if stable:
                # 3 frame × majority — chậm hơn, ít nhảy
                hits: list[tuple[float, float, tuple[int, int, int, int], str]] = []
                for t in _sample_times_in_cue(s0, e0):
                    hit = _read_probe(t, src)
                    if not hit:
                        continue
                    box = hit[2]
                    probe_bb = {"w": box[2] - box[0], "h": box[3] - box[1]}
                    if _bbox_fits_source(probe_bb, src, fw):
                        hits.append(hit)
                if not hits:
                    for t in _sample_times_in_cue(s0, e0):
                        hit = _read_probe(t, src)
                        if hit:
                            hits.append(hit)
                stable_box = _stable_box_from_hits(hits, fw=fw, fh=fh)
            else:
                # Nhanh: 1 frame giữa cue
                hit = _read_probe(s0 + dur * 0.5, src)
                stable_box = None
                if hit:
                    box = hit[2]
                    probe_bb = {"w": box[2] - box[0], "h": box[3] - box[1]}
                    if _bbox_fits_source(probe_bb, src, fw):
                        stable_box = box
                    else:
                        stable_box = box  # vẫn dùng nếu OCR có hit
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
    if stable:
        # Neo Y chỉ Caption đáy inherit — không kéo CAP-MID xuống cuối
        bottom_boxes = [
            b
            for b in anchor_boxes
            if b and (b[1] + b[3]) * 0.5 >= fh * 0.72
        ]
        _snap_inherited_y(
            segments,
            _global_cy_band(bottom_boxes or [], fh),
            fh,
        )
    _ensure_cover_times(segments, video_end)
    for seg in segments:
        seg.pop("_probeAnchored", None)
        seg.pop("_fromInherit", None)
        _retag_layout_from_bbox(seg, fh)
    return attached
