"""Whisper ASR. RapidOCR sống ở pipeline.ocr.extract — re-export để tương thích."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from ..core.jobs import Cancelled, check_cancel
from ..core.project import set_status
from ..core.resources import adaptive_workers, progress_msg
from ..ocr.extract import (  # noqa: F401 — public re-exports for run.py / burn / tests
    asr_paddleocr,
    _ocr_join_lines,
    _rapidocr_gpu_kwargs,
    _rapidocr_labels,
    prepare_cuda_dlls as _prepare_cuda_dlls,
)

# 1 model / process; reload khi đổi cpu_threads (Luồng).
_whisper = None
_whisper_threads: int | None = None
_whisper_lock = threading.Lock()

# Siết biên segment theo word timestamps — KHÔNG tách text (tránh 1 câu → nhiều mảnh).
_WORD_PAD_START = 0.08
_WORD_PAD_END = 0.18
_WORD_MIN_PROB = 0.01  # giữ gần hết words — tránh mất chữ Trung 1 ký tự
_MAX_SEG_DUR = 14.0  # trần nếu không có words / words lỗi
_MIN_SEG_DUR = 0.12


def _resolve_asr_workers(workers: int | None) -> int:
    return adaptive_workers(workers, kind="cpu", cap=16)


def whisper_loaded() -> bool:
    """True nếu model đã nạp trong process (không cần tải lại)."""
    return _whisper is not None


def get_whisper(workers: int = 2):
    """Load 1 lần / process — không reload khi đổi workers."""
    global _whisper, _whisper_threads
    thr = _resolve_asr_workers(workers)
    with _whisper_lock:
        if _whisper is not None:
            return _whisper

        device = "cpu"
        compute = "int8"
        try:
            _prepare_cuda_dlls()
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                device, compute = "cuda", "float16"
        except (ImportError, RuntimeError, OSError):
            pass
        # Fallback: torch CUDA sẵn nhưng ctranslate2 chưa thấy GPU
        if device == "cpu":
            try:
                from pipeline.core.accel import preferred_torch_device

                if preferred_torch_device() == "cuda":
                    try:
                        import ctranslate2 as _ct2

                        if _ct2.get_cuda_device_count() > 0:
                            device, compute = "cuda", "float16"
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            from faster_whisper import WhisperModel
        except OSError:
            # torch không load được CUDA DLL (cudnn v.v.) — ép CPU mode
            import os as _osenv
            _osenv.environ["CUDA_VISIBLE_DEVICES"] = "-1"
            device, compute = "cpu", "int8"
            from faster_whisper import WhisperModel  # noqa: F811

        # CUDA: ít CPU thread + num_workers>1 (batch decode). CPU: thr theo auto.
        if device == "cuda":
            import os as _os

            cpu_threads = max(2, min(4, (_os.cpu_count() or 4) // 3))
            # 2–4 worker CTranslate2 trên GPU (không = thr CPU)
            num_workers = max(1, min(4, thr if thr > 0 else 2))
        else:
            import os as _os

            cpu_threads = thr if thr > 0 else max(1, (_os.cpu_count() or 4) // 2)
            cpu_threads = max(1, min(cpu_threads, max(1, int((_os.cpu_count() or 4) * 0.85))))
            num_workers = 1
        _whisper = WhisperModel(
            "base",
            device=device,
            compute_type=compute,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
        )
        # Gắn meta để progress hiển thị đúng
        try:
            _whisper._vc_device = device  # type: ignore[attr-defined]
            _whisper._vc_threads = thr  # type: ignore[attr-defined]
            _whisper._vc_num_workers = num_workers  # type: ignore[attr-defined]
        except Exception:
            pass
        _whisper_threads = thr
        return _whisper


def warm_whisper(workers: int = 0) -> str:
    """Nạp model nền (startup)."""
    get_whisper(workers or 2)
    return "ok"


def reset_whisper() -> None:
    """Unload Whisper after cancellation so CPU/GPU memory is returned."""
    global _whisper, _whisper_threads
    with _whisper_lock:
        model, _whisper = _whisper, None
        _whisper_threads = None
    try:
        inner = getattr(model, "model", None)
        unload = getattr(inner, "unload_model", None)
        if callable(unload):
            unload()
    except Exception:
        pass
    import gc

    gc.collect()


def _word_parts(seg: Any) -> list[tuple[float, float, str, float]]:
    """[(start, end, word, prob), ...] — bỏ token rỗng / xác suất quá thấp."""
    raw = getattr(seg, "words", None) or []
    out: list[tuple[float, float, str, float]] = []
    for w in raw:
        text = (getattr(w, "word", None) or "").strip()
        if not text:
            continue
        try:
            s = float(getattr(w, "start", 0) or 0)
            e = float(getattr(w, "end", s) or s)
            p = float(getattr(w, "probability", 1.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if e < s:
            s, e = e, s
        if p < _WORD_MIN_PROB and len(text) <= 1:
            continue
        out.append((s, e, text, p))
    return out


def _tighten_bounds(
    seg_start: float,
    seg_end: float,
    parts: list[tuple[float, float, str, float]],
) -> tuple[float, float]:
    """Chỉ siết start/end theo từ đầu–cuối; giữ nguyên 1 câu (không tách text)."""
    s0 = max(0.0, float(seg_start))
    e0 = max(s0 + _MIN_SEG_DUR, float(seg_end))
    if not parts:
        if e0 - s0 > _MAX_SEG_DUR:
            e0 = s0 + _MAX_SEG_DUR
        return s0, e0

    # Bỏ tail/head word xác suất thấp nếu kéo biên vô lý
    usable = [p for p in parts if p[3] >= _WORD_MIN_PROB or len(p[2]) > 1]
    if not usable:
        usable = parts
    ws = usable[0][0]
    we = usable[-1][1]
    start = max(0.0, ws - _WORD_PAD_START)
    end = max(start + _MIN_SEG_DUR, we + _WORD_PAD_END)
    # Không nới quá biên Whisper (trừ pad nhỏ)
    start = max(start, s0 - 0.05)
    end = min(end, e0 + 0.05)
    # Chỉ co khi words gọn hơn segment thô (cắt silence 2 đầu)
    if end - start < e0 - s0:
        return start, max(start + _MIN_SEG_DUR, end)
    if e0 - s0 > _MAX_SEG_DUR:
        return s0, s0 + _MAX_SEG_DUR
    return s0, e0


def _segments_from_whisper(seg: Any) -> list[dict[str, Any]]:
    """1 segment Whisper → 1+ segment: tách khi có khoảng im > _SPLIT_GAP giữa words."""
    text = (getattr(seg, "text", None) or "").strip()
    if not text:
        return []
    seg_start = float(getattr(seg, "start", 0) or 0)
    seg_end = float(getattr(seg, "end", seg_start) or seg_start)
    parts = _word_parts(seg)

    # Không có word timestamps → giữ nguyên 1 segment
    if not parts:
        start, end = _tighten_bounds(seg_start, seg_end, parts)
        return [
            {
                "id": str(uuid.uuid4()),
                "index": 0,
                "start": start,
                "end": end,
                "source": text,
                "translation": "",
                "voice": "",
            }
        ]

    # Tìm điểm cắt: gap giữa word[i].end → word[i+1].start > threshold
    _SPLIT_GAP = 0.7  # giây — khoảng im đủ lâu để tách câu
    groups: list[list[tuple[float, float, str, float]]] = [[]]
    for i, wp in enumerate(parts):
        groups[-1].append(wp)
        if i < len(parts) - 1:
            gap = parts[i + 1][0] - wp[1]
            if gap >= _SPLIT_GAP:
                groups.append([])

    # Mỗi group → 1 segment
    out: list[dict[str, Any]] = []
    for group in groups:
        if not group:
            continue
        g_text = "".join(w[2] for w in group).strip()
        if not g_text:
            continue
        g_start = max(0.0, group[0][0] - _WORD_PAD_START)
        g_end = max(g_start + _MIN_SEG_DUR, group[-1][1] + _WORD_PAD_END)
        out.append(
            {
                "id": str(uuid.uuid4()),
                "index": 0,
                "start": g_start,
                "end": g_end,
                "source": g_text,
                "translation": "",
                "voice": "",
            }
        )
    return out if out else [
        {
            "id": str(uuid.uuid4()),
            "index": 0,
            "start": seg_start,
            "end": seg_end,
            "source": text,
            "translation": "",
            "voice": "",
        }
    ]


def asr_whisper(
    wav: Path,
    source_lang: str,
    *,
    workers: int = 2,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Whisper 1 lần cả file; siết start/end theo word timestamps."""
    import time

    thr = _resolve_asr_workers(workers)
    cached = whisper_loaded()
    if project_id:
        set_status(
            project_id,
            step="asr",
            progress=18 if not cached else 22,
            message=progress_msg(
                "Whisper",
                workers=thr,
                extra="tải model" if not cached else "nhận dạng",
            ),
            running=True,
        )
    model = get_whisper(thr)
    if project_id:
        device = getattr(getattr(model, "model", None), "device", "cpu")
        dev = str(getattr(model, "_vc_device", None) or device or "cpu")
        nw = int(getattr(model, "_vc_num_workers", 1) or 1)
        set_status(
            project_id,
            step="asr",
            progress=22,
            message=progress_msg(
                "Whisper",
                workers=thr,
                extra=("CUDA" if dev == "cuda" else "CPU")
                + (f" · {nw} worker" if dev == "cuda" and nw > 1 else "")
                + (" · cache" if cached else ""),
            ),
            running=True,
        )
    lang = None if source_lang in ("", "auto") else source_lang
    # beam=1 + VAD + word_timestamps: siết biên, tránh caption ôm silence 20–40s.
    segments, _info = model.transcribe(
        str(wav),
        language=lang,
        # VAD OFF: video anime có nhạc nền → VAD bỏ sót câu ngắn.
        # Word-split (_SPLIT_GAP) tách segment dài thay VAD.
        vad_filter=False,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=False,
        word_timestamps=True,
    )
    out: list[dict[str, Any]] = []
    last_report = 0.0
    try:
        for seg in segments:
            check_cancel(project_id)
            rows = _segments_from_whisper(seg)
            if not rows:
                continue
            out.extend(rows)
            # heartbeat — Whisper hay đứng % ở 22; message đổi để UI không tưởng đơ
            if project_id:
                now = time.monotonic()
                if len(out) == 1 or now - last_report >= 1.5:
                    last_report = now
                    t_end = float(rows[-1]["end"])
                    pct = min(48, 22 + len(out))
                    set_status(
                        project_id,
                        step="asr",
                        progress=pct,
                        message=progress_msg("Whisper", len(out), workers=thr, extra=f"~{t_end:.0f}s"),
                        running=True,
                    )
    except Cancelled:
        close = getattr(segments, "close", None)
        if callable(close):
            close()
        reset_whisper()
        raise
    # re-index + sort (sau split gap)
    out.sort(key=lambda s: (float(s.get("start") or 0), float(s.get("end") or 0)))
    for i, row in enumerate(out, start=1):
        row["index"] = i
    # Không chồng end lên start câu sau (TTS/caption sạch)
    for i in range(len(out) - 1):
        nxt = float(out[i + 1]["start"])
        cur_end = float(out[i]["end"])
        if cur_end > nxt - 0.02:
            out[i]["end"] = max(float(out[i]["start"]) + _MIN_SEG_DUR, nxt - 0.02)
    if project_id and out:
        set_status(
            project_id,
            step="asr",
            progress=50,
            message=progress_msg("Whisper xong", len(out), workers=thr),
            running=True,
        )
    return out



