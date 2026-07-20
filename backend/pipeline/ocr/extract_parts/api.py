"""Paddle/RapidOCR hardsub extract — api."""
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


from .runtime import *  # noqa: F403
from .textutil import *  # noqa: F403
from .merge import *  # noqa: F403
from .scan import *  # noqa: F403

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
    crop_mark = frames / ".crop_v5"
    fps_mark = frames / ".fps"
    need_extract = (
        not reuse_frames
        or not any(frames.glob("*.jpg"))
        or not crop_mark.exists()
    )
    # Độ dài ước lượng để chọn fps (video vài tiếng không quét 2fps)
    dur_hint = 0.0
    try:
        from pipeline.core.media import ffprobe_duration

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
        # Đáy hẹp — portrait 40% nuốt mid (灰尘/橘酿…) thành hardsub giả
        band = 0.18 if portrait else 0.22
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
    # Auto GPU: gần full VRAM (gpu_job_cap); không còn trần cứng 1–4.
    gpu_ocr = _rapidocr_gpu_kwargs()["det_use_cuda"]
    from pipeline.core.resources import gpu_job_cap

    gpu_cap = gpu_job_cap() if gpu_ocr else min(6, _cpu_budget(0.9))
    w = _ocr_pool_workers(w_req, cap=gpu_cap, gpu=gpu_ocr)
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
            if not _hardsub_line_keep(text, source_lang):
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
    # Overlay OCR: đường riêng — auto GPU gần full, không kẹp 1 luồng.
    sub_req = 0 if w_req <= 0 else max(1, w_req // 2)
    sub_cap = max(2, gpu_cap) if gpu_ocr else 2
    sub_w = _ocr_pool_workers(sub_req, cap=sub_cap, gpu=gpu_ocr)
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
            # Vertical = watermark cột: giữ nguyên (đừng strip 花木紫 rồi còn rác 工).
            seg["source"] = src
        # fix_zh sau merge → label mảnh (花水業→花木紫) trùng watermark dọc;
        # gộp vào vertical kẻo burn ẩn chữ dọc khi has_label.
        segs = _fold_duplicate_watermark_labels(segs)
        segs = _fold_vertical_column_flickers(segs)
        segs = _drop_mid_in_watermark_column(segs)
    return segs

