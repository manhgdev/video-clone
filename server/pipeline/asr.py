"""Whisper ASR + on-screen OCR (RapidOCR)."""
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

from .core.jobs import check_cancel, run_cmd
from .core.project import cache_frames, set_status
from .core.resources import adaptive_workers

# 1 model / process; reload khi đổi cpu_threads (Luồng).
_whisper = None
_whisper_threads: int | None = None
_whisper_lock = threading.Lock()
# giới hạn tổng luồng OCR phụ — tránh 100% CPU (để UI/OS ~5–10%)
_ocr_sem: threading.Semaphore | None = None
_ocr_sem_n: int = 0


def _resolve_asr_workers(workers: int | None) -> int:
    return adaptive_workers(workers, kind="cpu", cap=16)


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


def _prepare_cuda_dlls() -> None:
    if os.name != "nt":
        return
    import sysconfig

    root = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    path = os.environ.get("PATH", "")
    bins = [str(p) for p in root.glob("*/bin") if str(p) not in path]
    if bins:
        # ponytail: pip's CUDA sub-libraries are loaded by name at runtime.
        os.environ["PATH"] = os.pathsep.join(bins + [path])


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
            _prepare_cuda_dlls()
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                device, compute = "cuda", "float16"
        except (ImportError, RuntimeError):
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
    model = get_whisper(thr)
    if project_id:
        device = getattr(getattr(model, "model", None), "device", "cpu")
        set_status(
            project_id,
            step="asr",
            progress=22,
            message=(
                "Whisper ASR (CUDA)…"
                if device == "cuda"
                else f"Whisper ASR ({thr} luồng CPU)…"
            ),
            running=True,
        )
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


def _rapidocr_labels(*, use_cuda: bool | None = None) -> Any:
    """OCR lỏng hơn cho nhãn 1 chữ / graphic nhỏ (default min_height=30 bỏ sót 行)."""
    from rapidocr_onnxruntime import RapidOCR  # type: ignore

    _limit_onnx_threads()
    if use_cuda:
        _prepare_cuda_dlls()
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
        _prepare_cuda_dlls()
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
    vend = video_end or 30.0
    # tuần tự 3 pass + pool nhỏ (≤90% core) — trước: 3 pool song song → 100% CPU
    sub_req = 0 if w_req <= 0 else max(1, w_req // 2)
    # ROI giữa/full-frame lớn: 2 CUDA session đồng thời gây CUDNN execution
    # failure trên video dài; một session GPU vẫn nhanh và ổn định hơn fallback.
    sub_w = _ocr_pool_workers(sub_req, cap=1 if gpu_ocr else 2, gpu=gpu_ocr)
    _limit_onnx_threads()
    try:
        mid = _ocr_mid_hardsubs(
            video, project_id=project_id, video_end=vend, workers=sub_w
        )
    except Exception:
        mid = []
    try:
        vert = _ocr_vertical_titles(
            video, project_id=project_id, video_end=vend
        )
    except Exception:
        vert = []
    try:
        labels = _ocr_overlay_labels(
            video, project_id=project_id, video_end=vend, workers=sub_w
        )
    except Exception:
        labels = []
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
    """Chỉ lấy cột CJK cao/hẹp giữa khung — bỏ hardsub đáy."""
    import cv2

    # Bỏ 22% đáy (hardsub) + 8% đỉnh
    y0, y1 = int(vh * 0.10), int(vh * 0.78)
    x0, x1 = int(vw * 0.05), int(vw * 0.75)
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
        if bh <= bw * 1.3:
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

    pending: set[Any] = set()
    try:
        with ThreadPoolExecutor(max_workers=w, thread_name_prefix="ocr-scan") as pool:
            last = max(targets)
            frame_i = 0
            while frame_i <= last:
                check_cancel(project_id)
                ok, frame = cap.read()
                if not ok:
                    break
                for i, t in targets.get(frame_i, []):
                    pending.add(pool.submit(_job, i, t, frame))
                if len(pending) >= w * 2:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    _collect(done)
                frame_i += 1
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
    """Chữ CJK ngắn / nhãn giữa khung (pop-up), không dải hardsub đáy dài."""
    import cv2

    # dải giữa rộng hơn (bỏ ~20% đỉnh + 22% đáy)
    y0, y1 = int(vh * 0.20), int(vh * 0.78)
    x0, x1 = int(vw * 0.10), int(vw * 0.90)
    roi = frame_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return ""
    rh, rw = roi.shape[:2]
    if max(rh, rw) > 900:
        sc = 900 / max(rh, rw)
        roi = cv2.resize(roi, (int(rw * sc), int(rh * sc)))
    result, _ = ocr(roi)
    # (score, cy, cx, text) — giữ các dòng ngang gần nhau để ghép subtitle 2 dòng.
    candidates: list[tuple[float, float, float, str]] = []
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
        if confidence < (0.9 if cjk == 1 else 0.6):
            continue
        if cjk < len(compact) * 0.55:
            continue
        if len(compact) > 40:
            continue
        bw, bh = _ocr_box_wh(box)
        if bw < 6 or bh < 6:
            continue
        # Pass này chỉ lấy chữ ngang; watermark/title dọc do pass riêng xử lý.
        if bh > bw * 1.25:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            cx = (min(xs) + max(xs)) * 0.5 / max(1, roi.shape[1])
            cy = (min(ys) + max(ys)) * 0.5 / max(1, roi.shape[0])
        except (TypeError, ValueError, IndexError):
            continue
        center = 1.0 - min(1.0, abs(cx - 0.5) * 1.2)
        # ưu tiên box to (nhãn graphic)
        score = cjk * 8 + center * 4 + min(bw, bh) / 15.0 + (bw * bh) / max(1, rw * rh) * 30
        candidates.append((score, cy, cx, compact))
    if not candidates:
        return ""
    best = max(candidates, key=lambda item: item[0])
    nearby = [
        item
        for item in candidates
        if abs(item[1] - best[1]) <= 0.10 and abs(item[2] - best[2]) <= 0.32
    ]
    nearby.sort(key=lambda item: (item[1], item[2]))
    return _ocr_join_lines([item[3] for item in nearby])


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
    """Gom nhãn graphic giữa khung / cột bên (không hardsub đáy full-width)."""
    # Full frame trừ dải hardsub đáy hẹp
    y1 = int(vh * 0.86)
    roi = frame_bgr[0:y1, :]
    if roi.size == 0:
        return ""
    result, _ = ocr(roi)
    # (cy, cx, bw, bh, text)
    parts: list[tuple[float, float, float, float, str]] = []
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
        if confidence < (0.9 if cjk == 1 else 0.6):
            continue
        if cjk < max(1, len(compact) * 0.5):
            continue
        bw, bh = _ocr_box_wh(box)
        if cjk == 1:
            if bw < max(10, vw * 0.012) or bh < max(10, vh * 0.012):
                continue
        elif bw < 4 or bh < 4:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            cx = (min(xs) + max(xs)) * 0.5
            cy = (min(ys) + max(ys)) * 0.5
        except (TypeError, ValueError, IndexError):
            continue
        # hardsub đáy ngang rộng
        if cy > vh * 0.70 and bw > vw * 0.35 and bh < vh * 0.09:
            continue
        # title dọc full giữa
        if bh > vh * 0.40 and bh > bw * 1.8 and vw * 0.35 < cx < vw * 0.65:
            continue
        if len(compact) > 28:
            continue
        side = cx < vw * 0.36 or cx > vw * 0.64
        tall_col = bh > bw * 1.2 and bw < vw * 0.28 and bh < vh * 0.45
        # nhãn graphic giữa (抽藕丝 / 煮开 / 麻油 / 白芷) — không bắt side
        mid_graphic = (
            vh * 0.10 < cy < vh * 0.72
            and bw < vw * 0.55
            and bh < vh * 0.28
            and 1 <= cjk <= 14
            and not (bw > vw * 0.48 and bh < vh * 0.07)  # dải ngang dài
        )
        multi_line_mid = (
            vh * 0.10 < cy < vh * 0.72
            and cjk >= 4
            and bw < vw * 0.85
            and bh < vh * 0.12
        )
        if not (side or tall_col or mid_graphic or multi_line_mid):
            continue
        parts.append((cy, cx, float(bw), float(bh), compact))
    if not parts:
        return ""

    # Gộp dòng chồng dọc (cùng khối nhãn 2 dòng)
    parts.sort(key=lambda x: (x[0], x[1]))
    groups: list[list[tuple[float, float, float, float, str]]] = []
    for p in parts:
        placed = False
        for g in groups:
            # cùng cột / chồng ngang + gần theo Y
            g_cx = sum(x[1] for x in g) / len(g)
            g_cy = max(x[0] for x in g)
            g_bw = max(x[2] for x in g)
            if abs(p[1] - g_cx) < max(vw * 0.12, g_bw * 0.55) and abs(p[0] - g_cy) < vh * 0.10:
                g.append(p)
                placed = True
                break
        if not placed:
            groups.append([p])

    out_blocks: list[str] = []
    for g in groups:
        g.sort(key=lambda x: x[0])
        texts: list[str] = []
        for _cy, _cx, _bw, _bh, t in g:
            if any(_ocr_same(t, u) or _ocr_sim(t, u) >= 0.8 for u in texts):
                continue
            texts.append(t)
        if not texts:
            continue
        if len(texts) >= 2:
            # 2 dòng cùng khối: nối space (hoặc · nếu item ngắn)
            if all(len(t) <= 6 for t in texts):
                out_blocks.append("·".join(texts))
            else:
                out_blocks.append("".join(texts) if all(len(t) <= 8 for t in texts) else " ".join(texts))
        else:
            out_blocks.append(texts[0])

    # nhiều khối ngang (cột nguyên liệu)
    if len(out_blocks) >= 2 and all(len(t) <= 10 for t in out_blocks):
        # sort left→right by first part cx of group — approximate by text order already cy
        uniq: list[str] = []
        for t in out_blocks:
            if any(_ocr_same(t, u) or _ocr_sim(t, u) >= 0.75 for u in uniq):
                continue
            uniq.append(t)
        if len(uniq) >= 2:
            return "·".join(uniq)
        return uniq[0] if uniq else ""
    if not out_blocks:
        return ""
    # 1 khối tốt nhất (nhiều CJK / dài hơn)
    out_blocks.sort(key=lambda t: (sum(1 for c in t if _is_cjk(c)), len(t)), reverse=True)
    return out_blocks[0]


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

