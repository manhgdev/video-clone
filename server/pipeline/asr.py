"""Whisper ASR + on-screen OCR (RapidOCR)."""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .core.jobs import check_cancel, run_cmd
from .core.project import cache_frames, set_status

# 1 model / process; reload khi đổi cpu_threads (Luồng).
_whisper = None
_whisper_threads: int | None = None
_whisper_lock = threading.Lock()


def _resolve_asr_workers(workers: int | None) -> int:
    return max(1, min(16, int(workers or 0) or 2))


def get_whisper(workers: int = 2):
    """CPU: cpu_threads = Luồng (CTranslate2). CUDA: threads ít ảnh hưởng."""
    global _whisper, _whisper_threads
    from faster_whisper import WhisperModel

    thr = _resolve_asr_workers(workers)
    with _whisper_lock:
        if _whisper is not None and _whisper_threads == thr:
            return _whisper

        device = "cpu"
        compute = "int8"
        try:
            import torch

            if torch.cuda.is_available():
                device, compute = "cuda", "float16"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                # faster-whisper CTranslate2 has no MPS; stay on CPU int8
                device, compute = "cpu", "int8"
        except ImportError:
            pass

        # CPU: nhiều thread = nhanh hơn rõ. CUDA: 1–4 đủ (kernel GPU).
        cpu_threads = thr if device == "cpu" else min(4, thr)
        _whisper = WhisperModel(
            "base",
            device=device,
            compute_type=compute,
            cpu_threads=cpu_threads,
            num_workers=1,  # 1 file/job — không fan-out nhiều transcribe
        )
        _whisper_threads = thr
        return _whisper


def asr_whisper(
    wav: Path,
    source_lang: str,
    *,
    workers: int = 2,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Whisper 1 lần cả file; Luồng → cpu_threads CTranslate2."""
    thr = _resolve_asr_workers(workers)
    if project_id:
        set_status(
            project_id,
            step="asr",
            progress=22,
            message=f"Whisper ASR ({thr} luồng CPU)…",
            running=True,
        )
    model = get_whisper(thr)
    lang = None if source_lang in ("", "auto") else source_lang
    # beam=1 + VAD + condition_on_previous=False: nhanh hơn default ~2–3× trên CPU.
    segments, _info = model.transcribe(
        str(wav),
        language=lang,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=False,
    )
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(segments, start=1):
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append(
            {
                "id": str(uuid.uuid4()),
                "index": i,
                "start": float(seg.start),
                "end": float(seg.end),
                "source": text,
                "translation": "",
                "voice": "",
            }
        )
    return out


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
    )


def _rapidocr_labels() -> Any:
    """OCR lỏng hơn cho nhãn 1 chữ / graphic nhỏ (default min_height=30 bỏ sót 行)."""
    from rapidocr_onnxruntime import RapidOCR  # type: ignore

    return RapidOCR(
        box_thresh=0.3,
        thresh=0.2,
        text_score=0.3,
        unclip_ratio=2.0,
        min_height=8,
    )


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
    need_extract = (
        not reuse_frames
        or not any(frames.glob("*.jpg"))
        or not crop_mark.exists()
    )
    fps = 2.0
    if need_extract:
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

    jpgs = sorted(frames.glob("*.jpg"))
    total = max(1, len(jpgs))
    n = len(jpgs)
    w_req = max(1, min(16, int(workers or 2)))
    w = max(1, min(w_req, n if n else 1))

    # Mỗi worker 1 engine RapidOCR (ONNX không share session an toàn giữa thread).
    # Lỏng hơn default: 1 chữ CJK (行) không bị min_height=30 bỏ sót.
    _tls = threading.local()

    def _engine() -> Any:
        eng = getattr(_tls, "ocr", None)
        if eng is None:
            try:
                eng = _rapidocr_labels()
            except Exception:
                eng = RapidOCR()
            _tls.ocr = eng
        return eng

    # Hardsub đáy — luôn horizontal
    timed: list[tuple[float, str]] = [(-1.0, "")] * n
    done = 0
    done_lock = threading.Lock()

    def _ocr_one(i: int, img: Path) -> tuple[int, str]:
        check_cancel(project_id)
        result, _ = _engine()(str(img))
        lines: list[str] = []
        for row in result or []:
            text = str(row[1] or "").strip()
            if not text:
                continue
            # giữ 1 CJK; bỏ Latin/số nhiễu 1 ký tự
            cjk = sum(1 for c in text if _is_cjk(c))
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
    vend = video_end or 30.0
    # mỗi pass tự chia workers — tránh quá tải CPU
    sub_w = max(1, min(4, w_req // 2 or 1))

    def _job_mid() -> list[dict[str, Any]]:
        try:
            return _ocr_mid_hardsubs(
                video, project_id=project_id, video_end=vend, workers=sub_w
            )
        except Exception:
            return []

    def _job_vert() -> list[dict[str, Any]]:
        try:
            return _ocr_vertical_titles(
                video, project_id=project_id, video_end=vend
            )
        except Exception:
            return []

    def _job_lab() -> list[dict[str, Any]]:
        try:
            return _ocr_overlay_labels(
                video, project_id=project_id, video_end=vend, workers=sub_w
            )
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="ocr-pass") as pool:
        f_mid = pool.submit(_job_mid)
        f_vert = pool.submit(_job_vert)
        f_lab = pool.submit(_job_lab)
        mid = f_mid.result()
        vert = f_vert.result()
        labels = f_lab.result()
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
    return merged


def _ocr_vertical_titles(
    video: Path,
    *,
    project_id: str | None,
    video_end: float,
) -> list[dict[str, Any]]:
    """OCR tiêu đề dọc — đo start/end theo ms (bám khung thật, không hardcode 1.35s)."""
    import cv2

    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        ocr = RapidOCR()
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
        step_ms = 100
        windows: list[tuple[int, int]] = [(0, min(int(video_end * 1000), 3500))]
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
    """Chỉ lấy cột CJK cao/hẹp giữa khung — bỏ hardsub đáy."""
    import cv2

    # Bỏ 22% đáy (hardsub) + 8% đỉnh
    y0, y1 = int(vh * 0.10), int(vh * 0.78)
    x0, x1 = int(vw * 0.30), int(vw * 0.70)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return ""
    scale = 1.8
    img = cv2.resize(roi, (int(roi.shape[1] * scale), int(roi.shape[0] * scale)))
    result, _ = ocr(img)
    cands: list[tuple[float, str]] = []
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
        # chỉ CJK / punct — bỏ Latin nhiễu
        if cjk < len(text) * 0.7:
            continue
        bw, bh = _ocr_box_wh(box)
        if bh < 8 or bw < 2:
            continue
        # cột dọc: cao hơn rộng rõ
        if bh < bw * 1.2 and len(text) > 4:
            continue
        score = cjk * 10 + bh / max(1.0, bw)
        cands.append((score, text))
    if not cands:
        return ""
    cands.sort(key=lambda x: -x[0])
    # gộp top CJK (thường 1 tiêu đề)
    parts = [cands[0][1]]
    for sc, tx in cands[1:3]:
        if sc >= cands[0][0] * 0.45 and not _ocr_same(parts[0], tx):
            # cùng cột — nối theo thứ tự đọc
            if tx not in parts[0] and parts[0] not in tx:
                parts.append(tx)
    return _ocr_join_lines(parts)


def _ocr_scan_stamps(
    video: Path,
    stamps: list[float],
    *,
    project_id: str | None,
    workers: int,
    reader: Any,
) -> list[tuple[float, str]]:
    """OCR song song theo mốc thời gian — mỗi worker 1 VideoCapture + 1 engine."""
    if not stamps:
        return []
    w = max(1, min(8, int(workers or 2), len(stamps)))
    out: list[tuple[float, str] | None] = [None] * len(stamps)
    _tls = threading.local()

    def _job(idx: int, t: float) -> tuple[int, float, str]:
        check_cancel(project_id)
        import cv2

        cap = getattr(_tls, "cap", None)
        if cap is None:
            cap = cv2.VideoCapture(str(video))
            _tls.cap = cap
            _tls.ocr = _rapidocr_labels()
            _tls.vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
            _tls.vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            return idx, t, ""
        text = reader(frame, _tls.ocr, _tls.vw, _tls.vh)
        return idx, t, text or ""

    try:
        with ThreadPoolExecutor(max_workers=w, thread_name_prefix="ocr-scan") as pool:
            futs = [pool.submit(_job, i, t) for i, t in enumerate(stamps)]
            for fut in as_completed(futs):
                check_cancel(project_id)
                i, t, text = fut.result()
                if text:
                    out[i] = (t, text)
    finally:
        # VideoCapture per-thread — GC closes on thread end
        pass
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
    timed: list[tuple[float, str]],
    *,
    video_end: float,
    step: float,
    layout: str,
    gap: float = 0.45,
    min_hold: float = 0.2,
) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    i = 0
    while i < len(timed):
        t0, tx0 = timed[i]
        window = [tx0]
        j = i + 1
        while j < len(timed):
            t1, tx1 = timed[j]
            if t1 - timed[j - 1][0] > gap:
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
                tx0 = _ocr_pick_best(window)
                j += 1
                continue
            break
        best = _ocr_pick_best(window)
        if not best or sum(1 for c in best if _is_cjk(c)) < 1:
            i = j
            continue
        t_end = timed[j - 1][0] + step
        t_end = min(video_end, max(t_end, t0 + min_hold))
        segs.append(_ocr_seg(len(segs) + 1, t0, t_end, best, layout=layout))
        i = j

    # nhãn: gộp segment chồng thời gian / gần nhau (tránh 0.3s mảnh)
    if layout == "label" and len(segs) > 1:
        segs = _merge_label_segments(segs)
    return segs


def _merge_label_segments(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gộp nhãn chồng/gần (<0.55s) cùng nhóm nguyên liệu."""
    ordered = sorted(segs, key=lambda s: float(s.get("start") or 0))
    out: list[dict[str, Any]] = []
    for s in ordered:
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        gap = float(s["start"]) - float(prev["end"])
        ov = _ocr_label_overlap(prev.get("source") or "", s.get("source") or "")
        if gap <= 0.55 and ov >= 0.4:
            prev["end"] = max(float(prev["end"]), float(s["end"]))
            # giữ bản dài/ổn hơn
            prev["source"] = _ocr_pick_best(
                [prev.get("source") or "", s.get("source") or ""]
            )
            continue
        if gap < 0 and ov >= 0.3:
            # chồng thời gian
            prev["end"] = max(float(prev["end"]), float(s["end"]))
            prev["source"] = _ocr_pick_best(
                [prev.get("source") or "", s.get("source") or ""]
            )
            continue
        out.append(s)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


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
    try:
        _rapidocr_labels()
    except ImportError:
        return []

    # coarse: ~2.5 fps — đủ bắt flash ≥0.4s; 1 chữ ngắn refine sau
    coarse = 0.4
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
    windows: list[tuple[float, float]] = []
    for t, _ in coarse_hits:
        a, b = max(0.0, t - 0.35), min(video_end, t + 0.45)
        if windows and a <= windows[-1][1] + 0.05:
            windows[-1] = (windows[-1][0], max(windows[-1][1], b))
        else:
            windows.append((a, b))
    refine_stamps: list[float] = []
    for a, b in windows:
        t = a
        while t <= b + 1e-6:
            refine_stamps.append(round(t, 3))
            t += refine_step
    # unique sorted
    refine_stamps = sorted(set(refine_stamps))
    timed = _ocr_scan_stamps(
        video,
        refine_stamps,
        project_id=project_id,
        workers=workers,
        reader=_ocr_mid_hardsub_from_frame,
    )
    return _ocr_cluster_hits(
        timed,
        video_end=video_end,
        step=refine_step,
        layout="horizontal",
        gap=0.4,
        min_hold=0.2,
    )


def _ocr_mid_hardsub_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> str:
    """Chỉ chữ CJK ngắn giữa khung (pop-up hardsub), không đáy / không cột dọc."""
    import cv2

    # dải giữa (bỏ 12% đỉnh + 28% đáy hardsub dài)
    y0, y1 = int(vh * 0.28), int(vh * 0.72)
    x0, x1 = int(vw * 0.18), int(vw * 0.82)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return ""
    # downscale nhẹ — nhanh hơn full-res, 1 chữ vẫn đọc được
    rh, rw = roi.shape[:2]
    if max(rh, rw) > 720:
        sc = 720 / max(rh, rw)
        roi = cv2.resize(roi, (int(rw * sc), int(rh * sc)))
    result, _ = ocr(roi)
    best = ""
    best_score = -1.0
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        cjk = sum(1 for c in compact if _is_cjk(c))
        if cjk < 1 or cjk > 4:
            continue
        if cjk < len(compact) * 0.7:
            continue
        if len(compact) > 6:
            continue
        bw, bh = _ocr_box_wh(box)
        if bw < 6 or bh < 6:
            continue
        # bỏ cột dọc dài
        if bh > bw * 2.2 and bh > rh * 0.35:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            cx = (min(xs) + max(xs)) * 0.5 / max(1, roi.shape[1])
        except (TypeError, ValueError, IndexError):
            continue
        center = 1.0 - min(1.0, abs(cx - 0.5) * 1.5)
        score = cjk * 10 + center * 5 + min(bw, bh) / 20.0
        if score > best_score:
            best_score = score
            best = compact
    return best


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
    try:
        _rapidocr_labels()
    except ImportError:
        return []

    coarse = 0.35
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
    windows: list[tuple[float, float]] = []
    for t, _ in coarse_hits:
        a, b = max(0.0, t - 0.4), min(video_end, t + 0.5)
        if windows and a <= windows[-1][1] + 0.08:
            windows[-1] = (windows[-1][0], max(windows[-1][1], b))
        else:
            windows.append((a, b))
    refine_stamps: list[float] = []
    for a, b in windows:
        t = a
        while t <= b + 1e-6:
            refine_stamps.append(round(t, 3))
            t += refine_step
    timed = _ocr_scan_stamps(
        video,
        sorted(set(refine_stamps)),
        project_id=project_id,
        workers=workers,
        reader=_ocr_labels_from_frame,
    )
    return _ocr_cluster_hits(
        timed,
        video_end=video_end,
        step=refine_step,
        layout="label",
        gap=0.55,
        min_hold=0.4,
    )


def _ocr_labels_from_frame(
    frame_bgr: Any, ocr: Any, vw: int, vh: int
) -> str:
    """Gom nhãn nhỏ / cột bên (không hardsub đáy, không title dọc full giữa)."""
    import cv2

    # Bỏ 18% đáy (hardsub). Full-res: upscale 1.4× làm det miss glyph 1 chữ.
    y1 = int(vh * 0.82)
    roi = frame_bgr[0:y1, :]
    if roi.size == 0:
        return ""
    scale = 1.0
    img = roi
    result, _ = ocr(img)
    parts: list[tuple[float, float, str]] = []  # (cy, cx, text)
    for row in result or []:
        try:
            box, text = row[0], str(row[1] or "").strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        cjk = sum(1 for c in text if _is_cjk(c))
        # 1 CJK đủ (nhãn flash 1 chữ); Latin nhiễu vẫn bỏ
        if cjk < 1:
            continue
        compact = re.sub(r"\s+", "", text)
        if cjk < max(1, len(compact) * 0.55):
            continue
        bw, bh = _ocr_box_wh(box)
        # 1 chữ: box phải đủ lớn (không lấy noise chấm)
        if cjk == 1:
            if bw < max(10, vw * 0.012) or bh < max(10, vh * 0.012):
                continue
        elif bw < 4 or bh < 4:
            continue
        try:
            xs = [float(p[0]) / scale for p in box]
            ys = [float(p[1]) / scale for p in box]
            cx = (min(xs) + max(xs)) * 0.5
            cy = (min(ys) + max(ys)) * 0.5
        except (TypeError, ValueError, IndexError):
            continue
        # Bỏ hardsub ngang giữa đáy
        if cy > vh * 0.72 and bw > vw * 0.28 and bh < vh * 0.08:
            continue
        # Bỏ title dọc full giữa (pass vertical lo)
        if bh > vh * 0.28 and bh > bw * 1.8 and vw * 0.35 < cx < vw * 0.65:
            continue
        # Chỉ nhãn graphic: cột bên / nguyên liệu — không lấy 1 chữ giữa (hardsub)
        side = cx < vw * 0.32 or cx > vw * 0.68
        tall_col = bh > bw * 1.3 and bw < vw * 0.22 and bh < vh * 0.35
        multi_side = cjk >= 2 and side and bw < vw * 0.30
        if not (tall_col or multi_side or (side and cjk >= 2)):
            continue
        if len(text) > 24:
            continue
        # 1 chữ giữa khung = hardsub (pass mid), không phải label
        if cjk == 1 and 0.32 <= (cx / max(1, vw)) <= 0.68:
            continue
        parts.append((cy, cx, text))
    if not parts:
        return ""
    # Đọc trái→phải, trên→dưới; gộp cột dọc sát nhau
    parts.sort(key=lambda x: (round(x[1] / max(1, vw * 0.08)), x[0]))
    texts = [p[2] for p in parts]
    # dedupe gần giống
    out: list[str] = []
    for t in texts:
        if any(_ocr_same(t, u) for u in out):
            continue
        out.append(t)
    if not out:
        return ""
    # Nhiều cột nguyên liệu: nối bằng dấu ·
    if len(out) >= 2 and all(len(t) <= 6 for t in out):
        return "·".join(out)
    return _ocr_join_lines(out)


def _merge_horizontal_vertical(
    horiz: list[dict[str, Any]], vert: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ghép tiêu đề dọc / nhãn + hardsub đáy; tránh trùng chữ."""
    out = list(horiz)
    for v in vert:
        vs = v.get("source") or ""
        vlay = str(v.get("layout") or "vertical")
        if any(_ocr_same(vs, h.get("source") or "") for h in out):
            # đã có trong hardsub — đánh dấu dọc nếu cùng chữ ở đầu
            for h in out:
                if (
                    _ocr_same(vs, h.get("source") or "")
                    and float(h.get("start") or 0) < 2.0
                    and vlay == "vertical"
                ):
                    h["layout"] = "vertical"
            continue
        out.append(v)
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
) -> dict[str, Any]:
    lay = layout if layout in ("horizontal", "vertical", "label") else "horizontal"
    # vertical/label flash: cho phép < 0.35s (ms-accurate)
    min_dur = 0.04 if lay in ("vertical", "label") else 0.35
    # title dọc / nhãn: mặc định không lồng tiếng (UI có tích bật lại)
    dub_default = False if lay in ("vertical", "label") else True
    return {
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

