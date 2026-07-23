"""Paddle/RapidOCR hardsub extract — textutil."""
from __future__ import annotations

"""RapidOCR extract — hardsub đáy + mid/vertical/labels.

Tách khỏi asr.py (Whisper) và đường dịch/phụ đề burn layout.
Không sửa logic — chỉ di chuyển.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

from pipeline.core.jobs import check_cancel, run_cmd
from pipeline.core.project import cache_frames, set_status
from pipeline.core.resources import adaptive_workers

# giới hạn tổng luồng OCR phụ — tránh 100% CPU (để UI/OS ~5–10%)
_ocr_sem: threading.Semaphore | None = None
_ocr_sem_n: int = 0

def _ocr_seg(
    index: int,
    start: float,
    end: float,
    text: str,
    *,
    layout: str = "horizontal",
    bbox: dict[str, int] | None = None,
) -> dict[str, Any]:
    lay = layout if layout in ("horizontal", "vertical", "label", "mid") else "horizontal"
    min_dur = 0.04 if lay in ("vertical", "label", "mid") else 0.35
    dub_default = False if lay in ("vertical", "label") else True
    seg: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "index": index,
        "start": float(start),
        "end": float(max(end, start + min_dur)),
        "source": text,
        "translation": "",
        "voice": "",
        "layout": lay,
        "dub": dub_default,
    }
    if bbox and isinstance(bbox, dict):
        seg["bbox"] = {
            "x": int(bbox.get("x", 0)),
            "y": int(bbox.get("y", 0)),
            "w": int(bbox.get("w", 0)),
            "h": int(bbox.get("h", 0)),
        }
    return seg

def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
    )


def _hardsub_line_keep(text: str, source_lang: str) -> bool:
    """Giữ các dòng chữ hợp lệ (kể cả phụ đề ngắn 1-3 chữ, Tiếng Anh, Tiếng Việt, CJK)."""
    raw = (text or "").strip()
    if not raw:
        return False
    compact = re.sub(r"\s+", "", raw)
    if not compact or len(compact) < 1:
        return False
    cjk = sum(1 for c in raw if _is_cjk(c))
    lang = (source_lang or "auto").lower()
    # Nếu có chữ CJK → giữ từ 1 chữ trở lên (VD: "好", "你好", "没问题")
    if cjk >= 1:
        return True
    # Nếu là chữ Latinh / Tiếng Anh / Tiếng Việt / Số → giữ từ 2 ký tự hợp lệ trở lên
    if len(compact) >= 2:
        # Bỏ các dòng rác chỉ chứa ký tự đặc biệt thuẫn như "...", "---"
        if re.fullmatch(r"[._:/\-\\~*#@!+=|\(\)\[\]{}]+", compact):
            return False
        return True
    return False


def _ocr_join_lines(lines: list[str]) -> str:
    """Ghép dòng OCR: chữ Hán không chèn space (tránh '打炉 子呢')."""
    import re

    parts = [ln.strip() for ln in lines if ln and ln.strip()]
    if not parts:
        return ""
    out = parts[0]
    for ln in parts[1:]:
        if out and ln and _is_cjk(out[-1]) and _is_cjk(ln[0]):
            out += ln
        else:
            out += " " + ln
    return re.sub(
        r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])",
        "",
        out,
    )


def _ocr_fix_zh(texts: list[str], project_id: str | None = None) -> list[str]:
    """Sửa nhầm OCR phổ biến trên chữ Hán — rule, không gọi LLM (tránh đổi tên riêng)."""
    # ponytail: whitelist cặp hay nhầm; LLM từng sửa 阿达西→阿拉斯
    swaps = (
        ("免子", "兔子"),
        ("免儿", "兔儿"),
        ("刚铁", "钢铁"),
        ("珠木老马峰", "珠穆朗玛峰"),
        ("珠穆朗马峰", "珠穆朗玛峰"),
        ("玛里亚纳海构", "马里亚纳海沟"),
        ("马里亚纳海构", "马里亚纳海沟"),
        ("设想机", "摄像机"),
        ("信誓淡淡", "信誓旦旦"),
        # Watermark dọc 花木紫 thường bị OCR nhầm đúng một glyph.
        ("花木業", "花木紫"),
        ("花木葉", "花木紫"),
        ("花水紫", "花木紫"),
        ("花水業", "花木紫"),
        ("花木荣", "花木紫"),
        ("花水荣", "花木紫"),
        ("花木菜", "花木紫"),
    )
    out: list[str] = []
    for text in texts:
        s = _ocr_join_lines([text])
        for a, b in swaps:
            s = s.replace(a, b)
        out.append(s)
    return out


def _ocr_norm(s: str) -> str:
    return "".join((s or "").split())


def _ocr_sim(a: str, b: str) -> float:
    """0..1 similarity — CJK flicker / partial read."""
    from difflib import SequenceMatcher

    na, nb = _ocr_norm(a), _ocr_norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # partial / containment: một bên là chuỗi con của bên kia
    if na in nb or nb in na:
        short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(short) >= 2:
            return 0.95
    # Token CJK chung (VD: "花木紫 HUAMUI" vs "花木紫 HUAMUN")
    cjk_a = {c for c in na if _is_cjk(c)}
    cjk_b = {c for c in nb if _is_cjk(c)}
    if cjk_a and cjk_b and len(cjk_a & cjk_b) >= min(len(cjk_a), len(cjk_b), 2):
        return 0.90
    return SequenceMatcher(None, na, nb).ratio()


def _ocr_same(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na, nb = _ocr_norm(a), _ocr_norm(b)
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return _ocr_sim(a, b) >= 0.55


def _ocr_pick_best(texts: list[str]) -> str:
    """Chọn bản OCR ổn định nhất trong cửa sổ (dài + xuất hiện nhiều)."""
    from collections import Counter

    clean = [t.strip() for t in texts if (t or "").strip()]
    if not clean:
        return ""
    norms = [_ocr_norm(t) for t in clean]
    cnt = Counter(norms)
    # score: tần suất × độ dài (ưu tiên dòng đầy đủ lặp lại)
    best_n = max(norms, key=lambda n: (cnt[n], len(n)))
    # nếu có bản dài hơn gần giống best → lấy bản dài (đủ chữ hơn)
    best_len = len(best_n)
    for t, n in zip(clean, norms):
        if len(n) > best_len and _ocr_sim(best_n, n) >= 0.78:
            best_n, best_len = n, len(n)
    for t, n in zip(clean, norms):
        if n == best_n:
            return t
    return clean[0]


def _ocr_box_wh(box: Any) -> tuple[float, float]:
    """RapidOCR box: 4 điểm → (width, height)."""
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        return max(xs) - min(xs), max(ys) - min(ys)
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0


def _xyxy_to_bbox(
    x0: float, y0: float, x1: float, y1: float, fw: int, fh: int, *, pad: int = 2
) -> dict[str, int]:
    """xyxy → {x,y,w,h} sát ink (pad mỏng)."""
    x0i = max(0, int(round(x0)) - pad)
    y0i = max(0, int(round(y0)) - pad)
    x1i = min(fw, int(round(x1)) + pad)
    y1i = min(fh, int(round(y1)) + pad)
    return {
        "x": x0i,
        "y": y0i,
        "w": max(8, x1i - x0i),
        "h": max(8, y1i - y0i),
    }


def _union_bbox(boxes: list[dict[str, int]], fw: int, fh: int) -> dict[str, int] | None:
    if not boxes:
        return None
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return _xyxy_to_bbox(x0, y0, x1, y1, fw, fh, pad=0)


def _ocr_label_overlap(a: str, b: str) -> float:
    """Độ chồng chéo token CJK giữa 2 chuỗi nhãn (· tách cột)."""
    def toks(s: str) -> set[str]:
        parts = re.split(r"[·・,，/\s|]+", s or "")
        out: set[str] = set()
        for p in parts:
            p = re.sub(r"\s+", "", p)
            if len(p) >= 2:
                out.add(p)
            for i in range(len(p)):
                if _is_cjk(p[i]):
                    out.add(p[i])
        return out

    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return _ocr_sim(a, b)
    inter = len(ta & tb)
    return inter / max(1, min(len(ta), len(tb)))


def _cjk_len(text: str) -> int:
    return sum(1 for c in (text or "") if "\u4e00" <= c <= "\u9fff")


def _join_whisper_sources(a: str, b: str) -> str:
    """Nối mảnh Whisper — CJK không chèn space; tránh lặp ký tự biên."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a:
        return b
    if not b:
        return a
    if b.startswith(a):
        return b
    if a.endswith(b):
        return a
    # 最奢侈 + 最豪華 → không lặp 最 ở giữa nếu trùng
    if a[-1] == b[0]:
        return a + b[1:]
    return a + b


__all__ = [
    '_ocr_sem',
    '_ocr_sem_n',
    '_ocr_seg',
    '_is_cjk',
    '_hardsub_line_keep',
    '_ocr_join_lines',
    '_ocr_fix_zh',
    '_ocr_norm',
    '_ocr_sim',
    '_ocr_same',
    '_ocr_pick_best',
    '_ocr_box_wh',
    '_xyxy_to_bbox',
    '_union_bbox',
    '_ocr_label_overlap',
    '_cjk_len',
    '_join_whisper_sources',
]
