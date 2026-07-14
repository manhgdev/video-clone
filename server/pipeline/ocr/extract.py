"""RapidOCR extract — hardsub đáy + mid/vertical/labels.

Tách khỏi asr.py (Whisper) và đường dịch/phụ đề burn layout.
Không sửa logic — chỉ di chuyển.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

from ..core.jobs import check_cancel, run_cmd
from ..core.project import cache_frames, set_status
from ..core.resources import adaptive_workers

# giới hạn tổng luồng OCR phụ — tránh 100% CPU (để UI/OS ~5–10%)
_ocr_sem: threading.Semaphore | None = None
_ocr_sem_n: int = 0


def _cpu_budget(ratio: float = 0.9) -> int:
    """Số luồng CPU dùng cho OCR (mặc định 90% core — chừa UI)."""
    n = os.cpu_count() or 4
    return max(1, min(n, int(n * ratio)))


def _ocr_pool_workers(
    requested: int | None, *, cap: int | None = None, gpu: bool = False
) -> int:
    budget = _cpu_budget(0.9)
    hard = cap if cap is not None else min(4, budget)
    return adaptive_workers(
        requested, kind="gpu" if gpu else "cpu", cap=min(hard, budget)
    )


def _ocr_semaphore() -> threading.Semaphore:
    """Semaphore toàn cục: tổng job OCR phụ ≤ budget (không nhân 3 pass)."""
    global _ocr_sem, _ocr_sem_n
    n = _cpu_budget(0.9)
    if _ocr_sem is None or _ocr_sem_n != n:
        _ocr_sem = threading.Semaphore(n)
        _ocr_sem_n = n
    return _ocr_sem


def _limit_onnx_threads() -> None:
    """ONNX/OpenMP 1 thread / process — fan-out bằng pool, không nhân core."""
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    os.environ.setdefault("ORT_NUM_THREADS", "1")


def prepare_cuda_dlls() -> None:
    """PATH CUDA pip wheels — dùng chung Whisper + RapidOCR trên Windows."""
    if os.name != "nt":
        return
    import sysconfig

    root = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    path = os.environ.get("PATH", "")
    bins = [str(p) for p in root.glob("*/bin") if str(p) not in path]
    if bins:
        # ponytail: pip's CUDA sub-libraries are loaded by name at runtime.
        os.environ["PATH"] = os.pathsep.join(bins + [path])


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
    )


def _rapidocr_labels(*, use_cuda: bool | None = None) -> Any:
    """OCR lỏng hơn cho nhãn 1 chữ / graphic nhỏ (default min_height=30 bỏ sót 行)."""
    from rapidocr_onnxruntime import RapidOCR  # type: ignore

    _limit_onnx_threads()
    if use_cuda:
        prepare_cuda_dlls()
    gpu_kwargs = (
        _rapidocr_gpu_kwargs()
        if use_cuda is None
        else {
            "det_use_cuda": use_cuda,
            "cls_use_cuda": use_cuda,
            "rec_use_cuda": use_cuda,
        }
    )
    return RapidOCR(
        **gpu_kwargs,
        box_thresh=0.3,
        thresh=0.2,
        text_score=0.3,
        unclip_ratio=2.0,
        min_height=8,
    )


def _rapidocr_gpu_kwargs() -> dict[str, bool]:
    """Use CUDA for all OCR models when ONNX Runtime exposes its GPU provider."""
    try:
        prepare_cuda_dlls()
        import onnxruntime as ort

        use_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
    except (ImportError, OSError):
        use_cuda = False
    return {
        "det_use_cuda": use_cuda,
        "cls_use_cuda": use_cuda,
        "rec_use_cuda": use_cuda,
    }


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


def asr_paddleocr(
    video: Path,
    project_id: str | None = None,
    *,
    reuse_frames: bool = False,
    tag: str = "full",
    workers: int = 2,
    source_lang: str = "auto",
) -> list[dict[str, Any]]:
    """OCR hardsubs on screen (RapidOCR). Nhiều khung song song theo `workers`."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "OCR chưa cài. pip install rapidocr-onnxruntime — hoặc dùng Faster-Whisper."
        ) from e

    pid = project_id or video.parent.name
    frames = cache_frames(pid, tag)
    # crop_v4: hardsub đáy (ổn định ~99%) — tiêu đề dọc = pass riêng
    crop_mark = frames / ".crop_v4"
    fps_mark = frames / ".fps"
    need_extract = (
        not reuse_frames
        or not any(frames.glob("*.jpg"))
        or not crop_mark.exists()
    )
    # Độ dài ước lượng để chọn fps (video vài tiếng không quét 2fps)
    dur_hint = 0.0
    try:
        from ..core.media import ffprobe_duration

        dur_hint = float(ffprobe_duration(video) or 0.0)
    except Exception:
        dur_hint = 0.0
    from .overlay_scan import adaptive_bottom_fps

    if need_extract:
        fps = adaptive_bottom_fps(dur_hint if dur_hint > 0 else 120.0)
        if frames.exists():
            shutil.rmtree(frames)
        frames.mkdir(parents=True)

        w = h = 0
        try:
            probe = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0:s=x",
                    str(video),
                ],
                text=True,
            ).strip()
            w, h = (int(x) for x in probe.split("x"))
        except (subprocess.SubprocessError, ValueError):
            pass

        portrait = h > w > 0
        band = 0.40 if portrait else 0.30
        y0 = 1.0 - band
        # upscale 2× — soft subtitle trắng viền đen dễ đọc hơn khi phóng
        vf = f"fps={fps:g},crop=iw:ih*{band}:0:ih*{y0},scale=iw*2:ih*2"
        run_cmd(
            project_id,
            ["ffmpeg", "-y", "-i", str(video), "-vf", vf, str(frames / "%06d.jpg")],
        )
        crop_mark.write_text("v4\n", encoding="utf-8")
        fps_mark.write_text(f"{fps:g}\n", encoding="utf-8")
    else:
        try:
            fps = float((fps_mark.read_text(encoding="utf-8") or "2").strip() or 2)
        except (OSError, ValueError):
            fps = 2.0
        if fps <= 0:
            fps = 2.0
    jpgs = sorted(frames.glob("*.jpg"))
    total = max(1, len(jpgs))
    n = len(jpgs)
    w_req = int(workers or 0)
    # hardsub đáy: ≤90% core, tối đa 6 luồng
    # GPU nhỏ thrash khi dựng >4 bộ det/cls/rec; CPU vẫn cho tối đa 6.
    gpu_ocr = _rapidocr_gpu_kwargs()["det_use_cuda"]
    w = _ocr_pool_workers(
        w_req,
        cap=min(4 if gpu_ocr else 6, _cpu_budget(0.9)),
        gpu=gpu_ocr,
    )
    w = max(1, min(w, n if n else 1))
    _limit_onnx_threads()

    # Mỗi worker 1 engine RapidOCR (ONNX không share session an toàn giữa thread).
    # Lỏng hơn default: 1 chữ CJK (行) không bị min_height=30 bỏ sót.
    _tls = threading.local()

    def _engine() -> Any:
        eng = getattr(_tls, "ocr", None)
        if eng is None:
            try:
                eng = _rapidocr_labels()
            except Exception:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                eng = RapidOCR(**_rapidocr_gpu_kwargs())
            _tls.ocr = eng
        return eng

    # Hardsub đáy — luôn horizontal
    timed: list[tuple[float, str]] = [(-1.0, "")] * n
    done = 0
    done_lock = threading.Lock()
    sem = _ocr_semaphore()
    source_is_zh = source_lang.lower().startswith("zh")

    def _ocr_one(i: int, img: Path) -> tuple[int, str]:
        check_cancel(project_id)
        with sem:
            try:
                result, _ = _engine()(str(img))
            except Exception:
                _tls.ocr = _rapidocr_labels(use_cuda=False)
                result, _ = _tls.ocr(str(img))
        lines: list[str] = []
        for row in result or []:
            text = str(row[1] or "").strip()
            if not text:
                continue
            confidence = float(row[2]) if len(row) > 2 else 1.0
            if confidence < 0.5:
                continue
            # giữ 1 CJK; bỏ Latin/số nhiễu 1 ký tự
            cjk = sum(1 for c in text if _is_cjk(c))
            if source_is_zh and cjk < 1:
                continue
            # ponytail: hardsub đáy là câu; flash 1 glyph do pass giữa xử lý.
            if source_is_zh and cjk == 1:
                continue
            if cjk < 1 and len(text) < 2:
                continue
            lines.append(text)
        return i, _ocr_join_lines(lines)

    with ThreadPoolExecutor(max_workers=w, thread_name_prefix="ocr-asr") as pool:
        futs = {pool.submit(_ocr_one, i, img): i for i, img in enumerate(jpgs)}
        for fut in as_completed(futs):
            check_cancel(project_id)
            i, text = fut.result()
            timed[i] = (float(i) / fps, text)
            with done_lock:
                done += 1
                cur = done
            if project_id and (cur % max(1, w) == 0 or cur == n):
                pct = 15 + int(22 * cur / total)
                set_status(
                    project_id,
                    step="asr",
                    progress=pct,
                    message=f"OCR phụ đề {cur}/{total} ({w} luồng)",
                    running=True,
                )

    video_end = (len(jpgs) / fps) if jpgs else 0.0
    segs = _ocr_segments_from_timeline(timed, video_end) if any(t for _, t in timed) else []

    # Pass 1b+2+3 song song: mid hardsub / title dọc / nhãn bên
    if project_id:
        set_status(
            project_id,
            step="asr",
            progress=34,
            message="OCR phụ (giữa khung / dọc / nhãn)…",
            running=True,
        )
    mid: list[dict[str, Any]] = []
    vert: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    vend = video_end or dur_hint or 30.0
    # Overlay OCR: đường riêng (overlay_scan) — thưa theo độ dài, không 3× full refine
    sub_req = 0 if w_req <= 0 else max(1, w_req // 2)
    sub_w = _ocr_pool_workers(sub_req, cap=1 if gpu_ocr else 2, gpu=gpu_ocr)
    _limit_onnx_threads()
    try:
        from .overlay_scan import run_overlay_ocr

        mid, vert, labels = run_overlay_ocr(
            video,
            project_id=project_id,
            video_end=vend,
            workers=sub_w,
            set_status=set_status,
        )
    except Exception:
        mid, vert, labels = [], [], []
    if mid:
        segs = _merge_horizontal_vertical(segs, mid)
    if vert:
        segs = _merge_horizontal_vertical(segs, vert)
    if labels:
        segs = _merge_horizontal_vertical(segs, labels)

    # RapidOCR hay nhầm 免/兔… — sửa trên chữ nguồn trước khi dịch ngôn ngữ
    looks_zh = sum(1 for s in segs if any(_is_cjk(c) for c in s["source"])) >= max(
        1, len(segs) // 2
    )
    if looks_zh:
        fixed = _ocr_fix_zh([s["source"] for s in segs], project_id=project_id)
        for seg, src in zip(segs, fixed):
            # OCR full-frame đôi lúc nối watermark cố định vào title dọc giữa
            # khung. Tách nó ra để export định vị đúng cột title, trong khi
            # segment watermark dài vẫn được giữ riêng.
            if (
                str(seg.get("layout") or "") == "vertical"
                and "花木紫" in src
                and len(_ocr_norm(src)) > 3
            ):
                src = src.replace("花木紫", "").strip(" ·・|/")
            seg["source"] = src
    return segs


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
    # partial: khung chỉ đọc được nửa dòng
    if na in nb or nb in na:
        short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(short) >= 3 and len(short) / len(long) >= 0.55:
            return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def _ocr_same(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na, nb = _ocr_norm(a), _ocr_norm(b)
    if na == nb:
        return True
    # quá lệch độ dài → câu khác (trừ containment đã xử lý trong _ocr_sim)
    if abs(len(na) - len(nb)) > max(4, int(0.35 * max(len(na), len(nb)))):
        if not (na in nb or nb in na):
            return False
    thr = 0.78 if max(len(na), len(nb)) >= 6 else 0.88
    return _ocr_sim(a, b) >= thr


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

def _ocr_segments_from_timeline(
    timed: list[tuple[float, str]], video_end: float
) -> list[dict[str, Any]]:
    """Mỗi đoạn = chuỗi khung cùng chữ; hardsub đáy = horizontal."""
    segs: list[dict[str, Any]] = []
    i = 0
    n = len(timed)
    while i < n:
        t0, text = timed[i]
        if not text:
            i += 1
            continue
        j = i + 1
        window = [text]
        while j < n:
            nxt = timed[j][1]
            if not nxt:
                k = j + 1
                while k < n and not timed[k][1]:
                    k += 1
                gap = (timed[k][0] if k < n else video_end) - timed[j][0]
                if k < n and gap <= 0.6 and _ocr_same(text, timed[k][1]):
                    j = k
                    window.append(timed[k][1])
                    text = _ocr_pick_best(window)
                    j += 1
                    continue
                break
            if _ocr_same(text, nxt) or _ocr_same(window[-1], nxt):
                window.append(nxt)
                text = _ocr_pick_best(window)
                j += 1
                continue
            break
        end = timed[j][0] if j < n else video_end
        end = max(float(end), float(t0) + 0.35)
        segs.append(
            _ocr_seg(len(segs) + 1, t0, end, _ocr_pick_best(window), layout="horizontal")
        )
        i = j

    merged: list[dict[str, Any]] = []
    for seg in segs:
        if (
            merged
            and _ocr_same(merged[-1]["source"], seg["source"])
            and float(seg["start"]) - float(merged[-1]["end"]) <= 0.55
        ):
            prev = merged[-1]
            prev["end"] = max(float(prev["end"]), float(seg["end"]))
            prev["source"] = _ocr_pick_best([prev["source"], seg["source"]])
        else:
            merged.append(seg)
    for i, s in enumerate(merged, start=1):
        s["index"] = i
    # Hardsub hay lộ trước/sau khung OCR 2fps — nới start/end trên timeline
    return _ocr_pad_hardsub_windows(merged, video_end)


def _ocr_pad_hardsub_windows(
    segs: list[dict[str, Any]], video_end: float
) -> list[dict[str, Any]]:
    """Nới nhẹ cửa sổ hardsub ngang để khớp lúc che/xuất (ASR 2fps dễ cắt sớm/muộn)."""
    if not segs:
        return segs
    out = [dict(s) for s in segs]
    for i, seg in enumerate(out):
        if str(seg.get("layout") or "horizontal") != "horizontal":
            continue
        s0 = float(seg.get("start") or 0)
        e0 = float(seg.get("end") or s0)
        prev_end = float(out[i - 1].get("end") or 0) if i > 0 else 0.0
        next_start = (
            float(out[i + 1].get("start") or video_end)
            if i + 1 < len(out)
            else float(video_end)
        )
        lead = 0.18
        tail = 0.22
        src = str(seg.get("source") or "")
        src_cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
        if (e0 - s0) <= 0.75 and src_cjk <= 4:
            tail = max(tail, 0.45)
        new_start = max(0.0 if i == 0 else prev_end + 0.03, s0 - lead)
        new_end = min(next_start - 0.03 if i + 1 < len(out) else video_end, e0 + tail)
        if new_end > new_start + 0.12:
            seg["start"] = round(new_start, 3)
            seg["end"] = round(new_end, 3)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _ocr_vertical_titles(
    video: Path,
    *,
    project_id: str | None,
    video_end: float,
) -> list[dict[str, Any]]:
    """OCR tiêu đề dọc — đo start/end theo ms (bám khung thật, không hardcode 1.35s)."""
    import cv2

    try:
        ocr = _rapidocr_labels()
    except ImportError:
        return []

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    try:
        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0) or 25.0
        # coarse 100ms đầu/cuối; refine 40ms chỉ quanh hit
        step_ms = 200
        windows: list[tuple[int, int]] = [(0, min(int(video_end * 1000), 5000))]
        if video_end > 8.0:
            end0 = max(0, int(video_end * 1000) - 2500)
            end1 = int(video_end * 1000)
            if end0 > windows[0][1] + 500:
                windows.append((end0, end1))

        hits: list[tuple[float, str]] = []
        for w0, w1 in windows:
            t_ms = w0
            while t_ms <= w1:
                check_cancel(project_id)
                with _ocr_semaphore():
                    cap.set(cv2.CAP_PROP_POS_MSEC, float(t_ms))
                    ok, frame = cap.read()
                    if not ok:
                        break
                    text = _ocr_vertical_from_frame(frame, ocr, vw, vh)
                if text:
                    hits.append((t_ms / 1000.0, text))
                t_ms += step_ms
        # refine mép cụm (±120ms, step 40ms) — không full 25fps
        if hits:
            refine_ms = 40
            extra: list[tuple[float, str]] = []
            for t0, tx0 in hits:
                for d in (-120, -80, -40, 40, 80, 120):
                    t_ms = int(round(t0 * 1000 + d))
                    if t_ms < 0 or t_ms > int(video_end * 1000):
                        continue
                    cap.set(cv2.CAP_PROP_POS_MSEC, float(t_ms))
                    ok, frame = cap.read()
                    if not ok:
                        continue
                    text = _ocr_vertical_from_frame(frame, ocr, vw, vh)
                    if text and (_ocr_same(text, tx0) or _ocr_sim(text, tx0) >= 0.72):
                        extra.append((t_ms / 1000.0, text))
            hits.extend(extra)
            hits.sort(key=lambda x: x[0])
    finally:
        cap.release()

    if not hits:
        return []

    # Gom cụm liên tiếp (cùng chữ, gap ≤ 220ms) → 1 segment / cụm
    segs: list[dict[str, Any]] = []
    i = 0
    while i < len(hits):
        t0, tx0 = hits[i]
        window = [tx0]
        j = i + 1
        while j < len(hits):
            t1, tx1 = hits[j]
            if t1 - hits[j - 1][0] > 0.22:
                break
            if _ocr_same(tx0, tx1) or _ocr_same(window[-1], tx1):
                window.append(tx1)
                tx0 = _ocr_pick_best(window)
                j += 1
                continue
            break
        best = _ocr_pick_best(window)
        if best and sum(1 for c in best if _is_cjk(c)) >= 2:
            # end = last hit + 1 frame (ms); không pad 1.35s
            t_start = hits[i][0]
            t_end = hits[j - 1][0] + step_ms / 1000.0
            t_end = min(video_end, max(t_end, t_start + step_ms / 1000.0))
            segs.append(
                _ocr_seg(len(segs) + 1, t_start, t_end, best, layout="vertical")
            )
        i = j
    return segs


def _ocr_vertical_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> str:
    text, _bbox = _ocr_vertical_item_from_frame(frame_bgr, ocr, vw, vh)
    return text


def _ocr_vertical_item_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> tuple[str, dict[str, int] | None]:
    """Title dọc CJK + bbox cột sát ink."""
    import cv2

    y0, y1 = int(vh * 0.06), int(vh * 0.78)
    x0, x1 = int(vw * 0.02), int(vw * 0.98)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return "", None
    scale = 1.8
    img = cv2.resize(roi, (int(roi.shape[1] * scale), int(roi.shape[0] * scale)))
    result, _ = ocr(img)
    cands: list[tuple[float, str, float, float, float, float]] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if _is_cjk(c))
        if cjk < 2:
            continue
        if cjk < len(text) * 0.7:
            continue
        bw, bh = _ocr_box_wh(box)
        if bh < 8 or bw < 2:
            continue
        tall = bh > bw * 1.15
        short_stack = cjk <= 8 and bh >= bw * 0.85
        if not (tall or short_stack):
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bx0 = min(xs) / scale + x0
            by0 = min(ys) / scale + y0
            bx1 = max(xs) / scale + x0
            by1 = max(ys) / scale + y0
            xc = ((bx0 + bx1) * 0.5 - x0) / max(1.0, (x1 - x0))
        except (TypeError, ValueError, IndexError):
            continue
        edge = min(xc, 1.0 - xc)
        edge_bonus = 40.0 if edge < 0.22 else (15.0 if edge < 0.35 else -20.0)
        score = cjk * 10 + bh / max(1.0, bw) + edge_bonus
        cands.append((score, text, bx0, by0, bx1, by1))
    if not cands:
        return "", None
    cands.sort(key=lambda x: -x[0])
    parts = [cands[0][1]]
    boxes = [cands[0][2:]]
    for sc, tx, *xy in cands[1:3]:
        if sc >= cands[0][0] * 0.45 and not _ocr_same(parts[0], tx):
            if tx not in parts[0] and parts[0] not in tx:
                parts.append(tx)
                boxes.append(tuple(xy))  # type: ignore[arg-type]
    text = _ocr_join_lines(parts)
    bx0 = min(b[0] for b in boxes)
    by0 = min(b[1] for b in boxes)
    bx1 = max(b[2] for b in boxes)
    by1 = max(b[3] for b in boxes)
    return text, _xyxy_to_bbox(bx0, by0, bx1, by1, vw, vh, pad=3)


def _ocr_scan_stamps(
    video: Path,
    stamps: list[float],
    *,
    project_id: str | None,
    workers: int,
    reader: Any,
) -> list[tuple[float, str]]:
    """OCR song song theo mốc thời gian — seek cụm thưa, không walk full video."""
    if not stamps:
        return []
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0) or 25.0
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
    targets: dict[int, list[tuple[int, float]]] = {}
    for i, t in enumerate(stamps):
        targets.setdefault(max(0, int(round(t * fps))), []).append((i, t))

    w = min(_ocr_pool_workers(workers, cap=min(4, _cpu_budget(0.9))), len(stamps))
    out: list[tuple[float, str] | None] = [None] * len(stamps)
    _tls = threading.local()
    sem = _ocr_semaphore()

    def _job(idx: int, t: float, frame: Any) -> tuple[int, float, str]:
        check_cancel(project_id)
        with sem:
            check_cancel(project_id)
            if getattr(_tls, "ocr", None) is None:
                _tls.ocr = _rapidocr_labels()
            try:
                text = reader(frame, _tls.ocr, vw, vh)
            except Exception:
                # CUDA/CUDNN có thể lỗi sau nhiều phút với ROI lớn. Chỉ worker
                # đó chuyển sang CPU và thử lại, không làm mất toàn bộ pass.
                _tls.ocr = _rapidocr_labels(use_cuda=False)
                text = reader(frame, _tls.ocr, vw, vh)
            return idx, t, text or ""

    def _collect(done: Any) -> None:
        for fut in done:
            i, t, text = fut.result()
            if text:
                out[i] = (t, text)

    # Cụm frame gần nhau → đọc tuần tự; khoảng cách lớn → seek (tránh decode thừa).
    ordered = sorted(targets)
    runs: list[list[int]] = []
    for fi in ordered:
        if runs and fi - runs[-1][-1] <= 2:
            runs[-1].append(fi)
        else:
            runs.append([fi])

    pending: set[Any] = set()
    try:
        with ThreadPoolExecutor(max_workers=w, thread_name_prefix="ocr-scan") as pool:
            for run in runs:
                check_cancel(project_id)
                start, end = run[0], run[-1]
                # 1 khung / cách xa: seek theo ms (ổn định hơn POS_FRAMES trên mp4)
                if start == end:
                    cap.set(cv2.CAP_PROP_POS_MSEC, (start / fps) * 1000.0)
                    ok, frame = cap.read()
                    if not ok:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, float(start))
                        ok, frame = cap.read()
                    if not ok:
                        continue
                    snap = frame.copy()
                    for i, t in targets[start]:
                        pending.add(pool.submit(_job, i, t, snap))
                else:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, float(start))
                    got = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or -1)
                    if got >= 0 and abs(got - start) > 3:
                        cap.set(cv2.CAP_PROP_POS_MSEC, (start / fps) * 1000.0)
                    for expect in range(start, end + 1):
                        ok, frame = cap.read()
                        if not ok:
                            break
                        hits = targets.get(expect)
                        if not hits:
                            continue
                        snap = frame.copy()
                        for i, t in hits:
                            pending.add(pool.submit(_job, i, t, snap))
                        if len(pending) >= w * 2:
                            done, pending = wait(pending, return_when=FIRST_COMPLETED)
                            _collect(done)
                if len(pending) >= w * 2:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    _collect(done)
            _collect(as_completed(pending))
    finally:
        cap.release()
    return [x for x in out if x is not None]


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


def _ocr_cluster_hits(
    timed: list[Any],
    *,
    video_end: float,
    step: float,
    layout: str,
    gap: float = 0.45,
    min_hold: float = 0.2,
) -> list[dict[str, Any]]:
    """timed: (t, text) hoặc (t, text, bbox|None)."""
    normed: list[tuple[float, str, dict[str, int] | None]] = []
    for row in timed:
        if not row:
            continue
        if len(row) >= 3:
            t, tx, box = float(row[0]), str(row[1] or ""), row[2]
            bb = box if isinstance(box, dict) else None
        else:
            t, tx = float(row[0]), str(row[1] or "")
            bb = None
        if tx:
            normed.append((t, tx, bb))

    segs: list[dict[str, Any]] = []
    i = 0
    while i < len(normed):
        t0, tx0, box0 = normed[i]
        window = [tx0]
        boxes = [box0] if box0 else []
        j = i + 1
        while j < len(normed):
            t1, tx1, box1 = normed[j]
            if t1 - normed[j - 1][0] > gap:
                break
            same = (
                _ocr_same(tx0, tx1)
                or _ocr_same(window[-1], tx1)
                or _ocr_sim(tx0, tx1) >= 0.72
            )
            # nhãn: cột nguyên liệu OCR nhấp nháy → gộp nếu token chồng ≥50%
            if not same and layout == "label":
                same = (
                    _ocr_label_overlap(tx0, tx1) >= 0.5
                    or _ocr_label_overlap(window[-1], tx1) >= 0.5
                )
            if same:
                window.append(tx1)
                if box1:
                    boxes.append(box1)
                tx0 = _ocr_pick_best(window)
                j += 1
                continue
            break
        best = _ocr_pick_best(window)
        if not best or sum(1 for c in best if _is_cjk(c)) < 1:
            i = j
            continue
        t_end = normed[j - 1][0] + step
        t_end = min(video_end, max(t_end, t0 + min_hold))
        # bbox: ưu tiên khớp text best; không thì union
        bb: dict[str, int] | None = None
        for k in range(i, j):
            if _ocr_same(normed[k][1], best) and normed[k][2]:
                bb = normed[k][2]
                break
        if bb is None and boxes:
            fw = max((b["x"] + b["w"] for b in boxes), default=1080)
            fh = max((b["y"] + b["h"] for b in boxes), default=1920)
            # union trong không gian pixel ước (fw/fh chỉ clamp)
            bb = _union_bbox(boxes, max(fw, 1080), max(fh, 1920))
        segs.append(_ocr_seg(len(segs) + 1, t0, t_end, best, layout=layout, bbox=bb))
        i = j

    # nhãn: gộp segment chồng thời gian / gần nhau (tránh 0.3s mảnh)
    if layout == "label" and len(segs) > 1:
        segs = _merge_label_segments(segs)
    return segs


def _merge_label_segments(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gộp nhãn cùng chỗ + gần thời gian; giữ bbox; không gộp 2 nhãn xa nhau."""
    ordered = sorted(segs, key=lambda s: float(s.get("start") or 0))
    out: list[dict[str, Any]] = []

    def _near(a: dict[str, Any], b: dict[str, Any]) -> bool:
        ba, bb = a.get("bbox"), b.get("bbox")
        if not isinstance(ba, dict) or not isinstance(bb, dict):
            return True
        acx = float(ba["x"]) + float(ba["w"]) * 0.5
        acy = float(ba["y"]) + float(ba["h"]) * 0.5
        bcx = float(bb["x"]) + float(bb["w"]) * 0.5
        bcy = float(bb["y"]) + float(bb["h"]) * 0.5
        # cùng chỗ (màn 1080≈) — nhãn khác cột không gộp
        return abs(acx - bcx) < 80 and abs(acy - bcy) < 90

    for s in ordered:
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        gap = float(s["start"]) - float(prev["end"])
        ov = _ocr_label_overlap(prev.get("source") or "", s.get("source") or "")
        if gap <= 0.55 and ov >= 0.55 and _near(prev, s):
            prev["end"] = max(float(prev["end"]), float(s["end"]))
            prev["source"] = _ocr_pick_best(
                [prev.get("source") or "", s.get("source") or ""]
            )
            if not prev.get("bbox") and s.get("bbox"):
                prev["bbox"] = s["bbox"]
            continue
        if gap < 0 and ov >= 0.45 and _near(prev, s):
            prev["end"] = max(float(prev["end"]), float(s["end"]))
            prev["source"] = _ocr_pick_best(
                [prev.get("source") or "", s.get("source") or ""]
            )
            if not prev.get("bbox") and s.get("bbox"):
                prev["bbox"] = s["bbox"]
            continue
        out.append(s)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def _ocr_edge_stamps(
    hits: list[tuple[float, str]],
    video_end: float,
    coarse_step: float,
    refine_step: float,
    *,
    layout: str,
) -> list[float]:
    """Refine only cluster edges; coarse hits preserve the stable interior."""
    clusters: list[list[tuple[float, str]]] = []
    for hit in hits:
        if clusters:
            prev = clusters[-1][-1]
            same = _ocr_same(prev[1], hit[1]) or _ocr_sim(prev[1], hit[1]) >= 0.72
            if layout == "label" and not same:
                same = _ocr_label_overlap(prev[1], hit[1]) >= 0.5
            if hit[0] - prev[0] <= coarse_step * 1.5 and same:
                clusters[-1].append(hit)
                continue
        clusters.append([hit])

    stamps: set[float] = set()
    pad = coarse_step
    for cluster in clusters:
        for edge in {cluster[0][0], cluster[-1][0]}:
            t = max(0.0, edge - pad)
            end = min(video_end, edge + pad)
            while t <= end + 1e-6:
                stamps.add(round(t, 3))
                t += refine_step
    return sorted(stamps)


def _ocr_mid_hardsubs(
    video: Path,
    *,
    project_id: str | None,
    video_end: float,
    workers: int = 2,
) -> list[dict[str, Any]]:
    """Hardsub ngắn giữa khung (1–4 CJK) — layout=horizontal, TTS bình thường.

    Coarse 2.5fps + refine 0.1s quanh hit (không full-video 10fps).
    """
    # coarse: ~2.5 fps — đủ bắt flash ≥0.4s; 1 chữ ngắn refine sau
    coarse = 0.5
    stamps = [i * coarse for i in range(int(video_end / coarse) + 1)]
    stamps = [t for t in stamps if t <= max(0.0, video_end - 0.02)]
    coarse_hits = _ocr_scan_stamps(
        video,
        stamps,
        project_id=project_id,
        workers=workers,
        reader=_ocr_mid_hardsub_from_frame,
    )
    if not coarse_hits:
        return []

    # refine ±0.35s quanh mỗi hit (0.1s) — gộp vùng trùng
    refine_step = 0.1
    refine_stamps = _ocr_edge_stamps(
        coarse_hits, video_end, coarse, refine_step, layout="horizontal"
    )
    timed = _ocr_scan_stamps(
        video,
        refine_stamps,
        project_id=project_id,
        workers=workers,
        reader=_ocr_mid_hardsub_from_frame,
    )
    return _ocr_cluster_hits(
        sorted(coarse_hits + timed),
        video_end=video_end,
        step=refine_step,
        layout="horizontal",
        gap=coarse * 1.25,
        min_hold=0.2,
    )


def _ocr_mid_hardsub_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> str:
    text, _bbox = _ocr_mid_item_from_frame(frame_bgr, ocr, vw, vh)
    return text


def _ocr_mid_item_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> tuple[str, dict[str, int] | None]:
    """Chữ CJK ngắn giữa khung + bbox sát ink (frame coords)."""
    import cv2

    y0, y1 = int(vh * 0.20), int(vh * 0.78)
    x0, x1 = int(vw * 0.10), int(vw * 0.90)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return "", None
    rh, rw = roi.shape[:2]
    sc = 1.0
    if max(rh, rw) > 900:
        sc = 900 / max(rh, rw)
        roi = cv2.resize(roi, (int(rw * sc), int(rh * sc)))
    result, _ = ocr(roi)
    # (score, cy, cx, text, x0,y0,x1,y1 frame)
    candidates: list[tuple[float, float, float, str, float, float, float, float]] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        cjk = sum(1 for c in compact if _is_cjk(c))
        confidence = float(row[2]) if len(row) > 2 else 1.0
        if cjk < 1 or cjk > 32:
            continue
        if confidence < (0.85 if cjk == 1 else 0.55):
            continue
        if cjk < len(compact) * 0.55:
            continue
        if len(compact) > 40:
            continue
        bw, bh = _ocr_box_wh(box)
        if bw < 6 or bh < 6:
            continue
        if bh > bw * 1.25:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bx0 = min(xs) / sc + x0
            by0 = min(ys) / sc + y0
            bx1 = max(xs) / sc + x0
            by1 = max(ys) / sc + y0
            cx = ((bx0 + bx1) * 0.5 - x0) / max(1.0, (x1 - x0))
            cy = ((by0 + by1) * 0.5 - y0) / max(1.0, (y1 - y0))
        except (TypeError, ValueError, IndexError):
            continue
        center = 1.0 - min(1.0, abs(cx - 0.5) * 1.2)
        score = cjk * 8 + center * 4 + min(bw, bh) / 15.0 + (bw * bh) / max(1, rw * rh) * 30
        candidates.append((score, cy, cx, compact, bx0, by0, bx1, by1))
    if not candidates:
        return "", None
    best = max(candidates, key=lambda item: item[0])
    nearby = [
        item
        for item in candidates
        if abs(item[1] - best[1]) <= 0.10 and abs(item[2] - best[2]) <= 0.32
    ]
    nearby.sort(key=lambda item: (item[1], item[2]))
    text = _ocr_join_lines([item[3] for item in nearby])
    bx0 = min(item[4] for item in nearby)
    by0 = min(item[5] for item in nearby)
    bx1 = max(item[6] for item in nearby)
    by1 = max(item[7] for item in nearby)
    return text, _xyxy_to_bbox(bx0, by0, bx1, by1, vw, vh, pad=3)


def _ocr_overlay_labels(
    video: Path,
    *,
    project_id: str | None,
    video_end: float,
    workers: int = 2,
) -> list[dict[str, Any]]:
    """Nhãn graphic / nguyên liệu cột bên — layout=label, không TTS.

    Coarse 0.35s + refine 0.15s quanh hit → timing ổn, không mảnh 0.3s.
    """
    coarse = 0.6
    stamps = [i * coarse for i in range(int(video_end / coarse) + 1)]
    stamps = [t for t in stamps if t <= max(0.0, video_end - 0.02)]
    coarse_hits = _ocr_scan_stamps(
        video,
        stamps,
        project_id=project_id,
        workers=workers,
        reader=_ocr_labels_from_frame,
    )
    if not coarse_hits:
        return []

    # refine ±0.4s quanh hit
    refine_step = 0.15
    refine_stamps = _ocr_edge_stamps(
        coarse_hits, video_end, coarse, refine_step, layout="label"
    )
    timed = _ocr_scan_stamps(
        video,
        sorted(set(refine_stamps)),
        project_id=project_id,
        workers=workers,
        reader=_ocr_labels_from_frame,
    )
    return _ocr_cluster_hits(
        sorted(coarse_hits + timed),
        video_end=video_end,
        step=refine_step,
        layout="label",
        gap=coarse * 1.25,
        min_hold=0.4,
    )


def _ocr_labels_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> str:
    items = _ocr_label_items_from_frame(frame_bgr, ocr, vw, vh)
    if not items:
        return ""
    if len(items) == 1:
        return items[0][0]
    # legacy path: nối tạm; dual-scan dùng items riêng
    return "·".join(t for t, _ in items[:6])


def _ocr_label_items_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> list[tuple[str, dict[str, int]]]:
    """Mỗi khối nhãn → (text, bbox) riêng — không gộp cả khung thành 1 chuỗi."""
    y1 = int(vh * 0.86)
    roi = frame_bgr[0:y1, :]
    if roi.size == 0:
        return []
    result, _ = ocr(roi)
    # (cy, cx, bw, bh, text, x0,y0,x1,y1)
    parts: list[tuple[float, float, float, float, str, float, float, float, float]] = []
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if _is_cjk(c))
        if cjk < 1:
            continue
        compact = re.sub(r"\s+", "", text)
        confidence = float(row[2]) if len(row) > 2 else 1.0
        if confidence < (0.75 if cjk == 1 else 0.45):
            continue
        if cjk < max(1, len(compact) * 0.5):
            continue
        bw, bh = _ocr_box_wh(box)
        if cjk == 1:
            if bw < max(8, vw * 0.01) or bh < max(8, vh * 0.01):
                continue
        elif bw < 4 or bh < 4:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bx0, by0 = min(xs), min(ys)
            bx1, by1 = max(xs), max(ys)
            cx = (bx0 + bx1) * 0.5
            cy = (by0 + by1) * 0.5
        except (TypeError, ValueError, IndexError):
            continue
        if cy > vh * 0.70 and bw > vw * 0.35 and bh < vh * 0.09:
            continue
        if bh > vh * 0.40 and bh > bw * 1.8 and vw * 0.35 < cx < vw * 0.65:
            continue
        if len(compact) > 28:
            continue
        side = cx < vw * 0.36 or cx > vw * 0.64
        tall_col = bh > bw * 1.2 and bw < vw * 0.28 and bh < vh * 0.45
        mid_graphic = (
            vh * 0.08 < cy < vh * 0.75
            and bw < vw * 0.55
            and bh < vh * 0.30
            and 1 <= cjk <= 14
            and not (bw > vw * 0.48 and bh < vh * 0.07)
        )
        multi_line_mid = (
            vh * 0.08 < cy < vh * 0.75
            and cjk >= 4
            and bw < vw * 0.85
            and bh < vh * 0.14
        )
        if not (side or tall_col or mid_graphic or multi_line_mid):
            continue
        parts.append((cy, cx, float(bw), float(bh), compact, bx0, by0, bx1, by1))
    if not parts:
        return []

    parts.sort(key=lambda x: (x[0], x[1]))
    groups: list[list[tuple[float, float, float, float, str, float, float, float, float]]] = []
    for p in parts:
        placed = False
        for g in groups:
            g_cx = sum(x[1] for x in g) / len(g)
            g_cy = max(x[0] for x in g)
            g_bw = max(x[2] for x in g)
            # chỉ gộp dòng chồng sát (cùng khối), không gộp 2 nhãn cạnh
            if abs(p[1] - g_cx) < max(vw * 0.08, g_bw * 0.45) and abs(p[0] - g_cy) < vh * 0.06:
                g.append(p)
                placed = True
                break
        if not placed:
            groups.append([p])

    out: list[tuple[str, dict[str, int]]] = []
    for g in groups:
        g.sort(key=lambda x: x[0])
        texts: list[str] = []
        for _cy, _cx, _bw, _bh, t, *_rest in g:
            if any(_ocr_same(t, u) or _ocr_sim(t, u) >= 0.8 for u in texts):
                continue
            texts.append(t)
        if not texts:
            continue
        if len(texts) >= 2 and all(len(t) <= 6 for t in texts):
            joined = "·".join(texts)
        elif len(texts) >= 2:
            joined = "".join(texts) if all(len(t) <= 8 for t in texts) else " ".join(texts)
        else:
            joined = texts[0]
        bx0 = min(p[5] for p in g)
        by0 = min(p[6] for p in g)
        bx1 = max(p[7] for p in g)
        by1 = max(p[8] for p in g)
        out.append((joined, _xyxy_to_bbox(bx0, by0, bx1, by1, vw, vh, pad=3)))
    return out


def _merge_horizontal_vertical(
    horiz: list[dict[str, Any]], vert: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ghép tiêu đề dọc / nhãn + hardsub đáy; tránh trùng chữ."""
    out = list(horiz)
    for v in vert:
        vs = v.get("source") or ""
        vlay = str(v.get("layout") or "vertical")
        matches: list[dict[str, Any]] = []
        for h in out:
            hs = h.get("source") or ""
            hlay = str(h.get("layout") or "horizontal")
            same = _ocr_same(vs, hs)
            # OCR dọc hay lệch đúng 1 glyph (紫/業). Nhãn quét toàn clip ổn
            # định hơn, nên coi chuỗi 3+ glyph cùng độ dài là một title.
            if not same and {vlay, hlay} <= {"vertical", "label"}:
                vn, hn = _ocr_norm(vs), _ocr_norm(hs)
                same = len(vn) == len(hn) >= 3 and _ocr_sim(vs, hs) >= 0.65
            if same:
                matches.append(h)
        if matches:
            # đã có trong hardsub — đánh dấu dọc nếu cùng chữ ở đầu
            for h in matches:
                hs = h.get("source") or ""
                hlay = str(h.get("layout") or "horizontal")
                if (
                    vlay == "label"
                    and hlay == "vertical"
                ):
                    # Label pass quét suốt video và cho text/timing đáng tin hơn.
                    h["source"] = vs
                    h["start"] = min(
                        float(h.get("start") or 0), float(v.get("start") or 0)
                    )
                    h["end"] = max(
                        float(h.get("end") or 0), float(v.get("end") or 0)
                    )
                elif vlay == "vertical" and hlay == "vertical":
                    h["start"] = min(
                        float(h.get("start") or 0), float(v.get("start") or 0)
                    )
                    h["end"] = max(
                        float(h.get("end") or 0), float(v.get("end") or 0)
                    )
                if (
                    vlay == "label"
                    and _ocr_same(vs, hs)
                    and len(_ocr_norm(vs)) > len(_ocr_norm(hs))
                ):
                    h["source"] = vs
                if (
                    _ocr_same(vs, h.get("source") or "")
                    and float(h.get("start") or 0) < 2.0
                    and vlay == "vertical"
                ):
                    h["layout"] = "vertical"
            continue
        out.append(v)
    # Bỏ mảnh title thiếu 1 glyph nằm trọn trong title dọc đầy đủ cùng thời gian.
    compacted: list[dict[str, Any]] = []
    for s in out:
        if str(s.get("layout") or "horizontal") != "vertical":
            compacted.append(s)
            continue
        sn = _ocr_norm(s.get("source") or "")
        merged_into: dict[str, Any] | None = None
        for prev in compacted:
            if str(prev.get("layout") or "horizontal") != "vertical":
                continue
            pn = _ocr_norm(prev.get("source") or "")
            overlap = min(
                float(s.get("end") or 0), float(prev.get("end") or 0)
            ) - max(float(s.get("start") or 0), float(prev.get("start") or 0))
            if overlap >= 0 and min(len(sn), len(pn)) >= 2 and (sn in pn or pn in sn):
                merged_into = prev
                break
        if merged_into is None:
            compacted.append(s)
            continue
        if len(sn) > len(_ocr_norm(merged_into.get("source") or "")):
            merged_into["source"] = s.get("source") or ""
        merged_into["start"] = min(
            float(merged_into.get("start") or 0), float(s.get("start") or 0)
        )
        merged_into["end"] = max(
            float(merged_into.get("end") or 0), float(s.get("end") or 0)
        )
    out = compacted
    out.sort(key=lambda s: float(s.get("start") or 0))
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


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
    # vertical/label/mid flash: cho phép ngắn
    min_dur = 0.04 if lay in ("vertical", "label", "mid") else 0.35
    # title dọc / nhãn: mặc định không lồng tiếng (UI có tích bật lại)
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
    if bbox and bbox.get("w", 0) >= 8 and bbox.get("h", 0) >= 8:
        seg["bbox"] = {
            "x": int(bbox["x"]),
            "y": int(bbox["y"]),
            "w": int(bbox["w"]),
            "h": int(bbox["h"]),
        }
    return seg

