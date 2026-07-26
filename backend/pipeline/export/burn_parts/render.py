"""Burn render loop: decode (NVDEC/CPU) → mask+caption → pipe NVENC.

Tách từ burn_parts/pipeline.py — cover_and_burn chỉ còn chuẩn bị cue/layout.
"""
from __future__ import annotations

import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pipeline.core.jobs import _job_procs, check_cancel
from pipeline.core.media import ffprobe_duration, h264_encoder_args, nvdec_available
from pipeline.core.project import set_status
from pipeline.export.cover_mask import _apply_cover_mask

from .layout_text import _blit_overlay


def _burn_frame_count_complete(written: int, expected: int, fps: float) -> bool:
    """Allow normal decoder rounding, never accept a materially truncated render.

    OpenCV CAP_PROP_FRAME_COUNT often overshoots decodable frames (VFR/B-frames);
    tolerance scales with length (~0.75s or 0.75% of frames).
    """
    if written <= 0:
        return False
    if expected <= 0:
        return True
    fps_s = max(1.0, float(fps) if fps and fps > 0 else 25.0)
    tolerance = max(
        2,
        int(math.ceil(fps_s * 0.75)),
        int(math.ceil(expected * 0.0075)),
    )
    return written >= max(1, expected - tolerance)


def _burn_output_complete(
    written: int,
    expected_frames: int,
    fps: float,
    output_duration: float,
    expected_duration: float,
) -> bool:
    """Prefer duration when full; frame count alone is unreliable vs OpenCV totals."""
    if written <= 0:
        return False
    dur_ok = expected_duration <= 0 or output_duration + 0.5 >= expected_duration
    # Duration proves a full encode (audio/container); ignore inflated FRAME_COUNT.
    if dur_ok and expected_duration > 1.0 and output_duration + 0.25 >= expected_duration:
        return True
    frames_ok = _burn_frame_count_complete(written, expected_frames, fps)
    if not frames_ok:
        return False
    return dur_ok


def render_burned_video(
    video: Path,
    out: Path,
    *,
    cues: list[tuple[float, float, float, float, str, str, str]],
    cue_need_mask: list[bool],
    cue_fits: list[list[tuple[int, int, int, int]]],
    cue_overlays: list[tuple[Any, int, int] | None],
    cue_segment_ids: list[str],
    segments_by_id: dict[str, dict[str, Any]],
    mask_style: str,
    mask_color: str,
    mask_opacity: int,
    burn: bool,
    w: int,
    h: int,
    workers: int,
    project_id: str | None,
) -> Path:
    """Đọc frame, đắp mask + chữ đã layout, ghi NVENC — trả file out."""
    import cv2

    if project_id:
        set_status(
            project_id,
            step="export",
            progress=18,
            message="Mở video / khởi tạo encode…",
            running=True,
        )

    probe = cv2.VideoCapture(str(video))
    if not probe.isOpened():
        raise RuntimeError(f"Không mở được video: {video}")
    fps = float(probe.get(cv2.CAP_PROP_FPS) or 25.0)
    if not (1.0 <= fps <= 120.0):
        fps = 25.0
    frame_total = int(probe.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    probe.release()
    # h264 yêu cầu chẵn
    ew = int(w) - (int(w) % 2)
    eh = int(h) - (int(h) % 2)
    if ew < 2 or eh < 2:
        raise RuntimeError(f"Kích thước frame không hợp lệ: {w}x{h}")
    import tempfile
    from pathlib import Path as _P

    err_path = _P(tempfile.gettempdir()) / f"vc_burn_{project_id or 'x'}.log"
    err_f = open(err_path, "w", encoding="utf-8", errors="replace")
    _ff_kw: dict = dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=err_f,
    )
    if sys.platform == "win32":
        _ff_kw["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    # Ưu tiên NVENC throughput (bản trung gian); fail → caller thấy stderr.
    v_args = list(h264_encoder_args(throughput=True))
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{ew}x{eh}",
            "-r",
            f"{fps:.4f}",
            "-i",
            "-",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            *v_args,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-shortest",
            str(out),
        ],
        **_ff_kw,
    )
    assert proc.stdin is not None
    if project_id:
        _job_procs.setdefault(project_id, []).append(proc)

    # Decode bằng NVDEC; chỉ download frame về RAM vì blur/Pillow vẫn cần CPU.
    # ponytail: fallback VideoCapture giữ tương thích codec/driver không có CUDA.
    decoder = None
    cap = None
    if nvdec_available(video):
        decode_kw: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            decode_kw["creationflags"] = int(
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
        decoder = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
                "-i", str(video), "-an", "-sn", "-dn",
                "-vf", "hwdownload,format=nv12",
                # Nguồn VFR (clip -c copy): không CFR-hoá theo tbr — tbr 30 trên
                # clip 23.9fps từng bơm 153/122 frame → video slow-motion + ffmpeg
                # -shortest thoát sớm giữa write (written=0, báo truncated).
                "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
            ],
            **decode_kw,
        )
        if project_id:
            _job_procs.setdefault(project_id, []).append(decoder)
    else:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise RuntimeError(f"Không mở được video: {video}")
    decoder_fallback = False
    # Nếu frame lẻ: crop khi ghi (ghi ew×eh)
    _frame_ew, _frame_eh = ew, eh
    _src_w, _src_h = int(w), int(h)

    # Cue indices theo frame — sparse (chỉ frame có cue), tránh cấp phát N×list rỗng
    # (trước đây list[frame_total] khiến video dài đứng lâu ở «Dùng vùng che…»).
    from collections import defaultdict

    if frame_total <= 0:
        # CAP_PROP_FRAME_COUNT đôi khi 0 — ước lượng; không cấp phát 10M list rỗng
        dur_est = float(ffprobe_duration(video) or 0.0)
        frame_total = max(1, int(dur_est * fps) + 1) if dur_est > 0 else int(fps * 60)

    cover_idx: dict[int, list[int]] = defaultdict(list)
    burn_idx: dict[int, list[int]] = defaultdict(list)
    for ci, cue in enumerate(cues):
        if ci < len(cue_need_mask) and cue_need_mask[ci]:
            f0 = max(0, int(float(cue[0]) * fps))
            f1 = min(frame_total, int(math.ceil(float(cue[1]) * fps)))
            for fi in range(f0, f1):
                cover_idx[fi].append(ci)
        if burn:
            f0 = max(0, int(float(cue[2]) * fps))
            f1 = min(frame_total, int(math.ceil(float(cue[3]) * fps)))
            for fi in range(f0, f1):
                burn_idx[fi].append(ci)

    if project_id:
        set_status(
            project_id,
            step="export",
            progress=20,
            message=f"Xuất khung 0/{frame_total} ({workers} luồng)",
            running=True,
        )

    def _paint_one(item: tuple[int, Any]) -> tuple[int, bytes]:
        fi, fr = item
        # Pad/crop về kích thước chẵn ffmpeg
        fh, fw = fr.shape[:2]
        if fw != _frame_ew or fh != _frame_eh:
            import numpy as np

            canvas = np.zeros((_frame_eh, _frame_ew, 3), dtype=fr.dtype)
            cw = min(fw, _frame_ew)
            ch = min(fh, _frame_eh)
            canvas[:ch, :cw] = fr[:ch, :cw]
            fr = canvas
        cis = cover_idx.get(fi) or []
        bis = burn_idx.get(fi) or []
        for ci in cis:
            fits = cue_fits[ci] if ci < len(cue_fits) else []
            # Per-cue mask style (effect overlay) hoặc global cover mask
            sid = cue_segment_ids[ci] if ci < len(cue_segment_ids) else ""
            sm = segments_by_id.get(sid, {}) if sid else {}
            st_cue = str(sm.get("coverMaskStyle") or mask_style)
            col_cue = str(sm.get("coverMaskColor") or mask_color)
            op_cue = int(sm.get("coverMaskOpacity") if sm.get("coverMaskOpacity") is not None else mask_opacity)
            for fit in fits:
                if fit is not None:
                    fr = _apply_cover_mask(
                        fr,
                        fit,
                        style=st_cue,
                        color_hex=col_cue,
                        opacity_pct=op_cue,
                    )
        for bi in bis:
            # Không ẩn watermark dọc khi label trùng nguồn (OCR flicker cùng cột) —
            # chỉ tạm ẩn nếu nhãn thật khác chữ đang đè cùng frame.
            if (cues[bi][6] if len(cues[bi]) > 6 else "") == "vertical":
                vsrc = (cues[bi][5] if len(cues[bi]) > 5 else "") or ""
                conflict = False
                for bj in bis:
                    if bj == bi:
                        continue
                    if (cues[bj][6] if len(cues[bj]) > 6 else "") != "label":
                        continue
                    lsrc = (cues[bj][5] if len(cues[bj]) > 5 else "") or ""
                    # cùng watermark / gần giống → không conflict
                    if lsrc and vsrc and (
                        lsrc == vsrc
                        or lsrc in vsrc
                        or vsrc in lsrc
                        or abs(len(lsrc) - len(vsrc)) <= 1
                    ):
                        continue
                    conflict = True
                    break
                if conflict:
                    continue
            ov = cue_overlays[bi]
            if ov is not None:
                sid = cue_segment_ids[bi] if bi < len(cue_segment_ids) else ""
                sm = segments_by_id.get(sid, {}) if sid else {}
                alpha = max(0.0, min(1.0, float(sm.get("logoOpacity", 1.0))))
                now = fi / fps
                start = float(sm.get("start") or 0)
                end = float(sm.get("end") or now)
                fade_in_end = float(sm.get("logoFadeInEnd") or start)
                fade_out_start = float(sm.get("logoFadeOutStart") or end)
                if now < fade_in_end:
                    alpha *= max(0.0, (now - start) / max(1e-6, fade_in_end - start))
                if now > fade_out_start:
                    alpha *= max(0.0, (end - now) / max(1e-6, end - fade_out_start))
                fr = _blit_overlay(fr, ov, alpha)
        return fi, fr.tobytes()

    # Prefetch đọc + pool blur/blit; ghi ffmpeg theo thứ tự frame.
    import threading
    from queue import Empty, Queue

    batch_n = max(48, workers * 20)
    # Queue sâu hơn → NVENC ít bị đói khi paint/CPU chậm hơn encode
    painted_q: Queue[list[bytes] | None] = Queue(maxsize=max(4, min(16, workers * 2)))
    read_err: list[BaseException] = []
    # ~50 cập nhật / video (theo batch, không theo frame — tránh status_every = frame_total//N
    # khiến gần như không bao giờ % khớp và UI kẹt «Dùng vùng che…»).
    n_batches_est = max(1, (frame_total + batch_n - 1) // batch_n) if frame_total > 0 else 8
    status_every = max(1, n_batches_est // 50)

    def _reader_painter() -> None:
        import numpy as np

        frame_i = 0
        batch_i = 0
        frame_bytes = _src_w * _src_h * 3

        def read_frame() -> tuple[bool, Any]:
            nonlocal cap, decoder_fallback
            if decoder is None or decoder_fallback:
                assert cap is not None
                return cap.read()
            assert decoder.stdout is not None
            raw = bytearray()
            while len(raw) < frame_bytes:
                chunk = decoder.stdout.read(frame_bytes - len(raw))
                if not chunk:
                    if frame_i >= frame_total:
                        return False, None
                    # ponytail: NVDEC can die transiently after its one-frame
                    # capability probe; resume at the same frame on CPU.
                    decoder_fallback = True
                    cap = cv2.VideoCapture(str(video))
                    if not cap.isOpened() or (
                        frame_i > 0
                        and not cap.set(cv2.CAP_PROP_POS_FRAMES, frame_i)
                    ):
                        raise RuntimeError(
                            f"Decoder GPU dừng ở frame {frame_i}/{frame_total} "
                            "và không thể tiếp tục bằng CPU"
                        )
                    return cap.read()
                raw.extend(chunk)
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((_src_h, _src_w, 3))
            return True, frame

        try:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="burn"
            ) as pool:
                while True:
                    check_cancel(project_id)
                    batch: list[tuple[int, Any]] = []
                    for _ in range(batch_n):
                        ok, frame = read_frame()
                        if not ok:
                            break
                        batch.append((frame_i, frame))
                        frame_i += 1
                    if not batch:
                        break
                    # map giữ thứ tự → ghi ffmpeg tuần tự không reorder
                    raws = [raw for _fi, raw in pool.map(_paint_one, batch)]
                    painted_q.put(raws)
                    batch_i += 1
                    if project_id and (
                        batch_i == 1
                        or batch_i % status_every == 0
                        or (frame_total > 0 and frame_i >= frame_total)
                    ):
                        pct = 20 + int(
                            50 * min(1.0, frame_i / max(1, frame_total))
                        )
                        set_status(
                            project_id,
                            step="export",
                            progress=pct,
                            message=f"Xuất khung {frame_i}/{frame_total} ({workers} luồng)",
                            running=True,
                        )
        except BaseException as e:
            read_err.append(e)
        finally:
            painted_q.put(None)

    t = threading.Thread(target=_reader_painter, name="burn-read", daemon=True)
    written_frames = 0
    try:
        t.start()
        pipe_dead = False
        while True:
            check_cancel(project_id)
            try:
                batch_raw = painted_q.get(timeout=0.5)
            except Empty:
                if not t.is_alive() and painted_q.empty():
                    break
                continue
            if batch_raw is None:
                break
            # Ghi từng frame: BrokenPipeError giữa batch vẫn đếm đúng số frame
            # đã vào encoder (write cả batch từng làm written=0 dù ffmpeg đã
            # encode gần đủ → báo «truncated» sai).
            try:
                if proc.poll() is not None:
                    pipe_dead = True
                    break
                for raw in batch_raw:
                    proc.stdin.write(raw)
                    written_frames += 1
            except BrokenPipeError:
                pipe_dead = True
                break
            except OSError as e:
                if e.errno in (32, 22) or getattr(e, "winerror", None) in (232, 109):
                    pipe_dead = True
                    break
                raise
            if pipe_dead:
                break
        t.join(timeout=5)
        if read_err:
            raise read_err[0]
    finally:
        if cap is not None:
            cap.release()
        if decoder is not None:
            try:
                if decoder.stdout:
                    decoder.stdout.close()
            except OSError:
                pass
            if decoder.poll() is None:
                decoder.terminate()
            decoder.wait()
            if project_id and project_id in _job_procs:
                _job_procs[project_id] = [
                    x for x in _job_procs[project_id] if x is not decoder
                ]
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except BrokenPipeError:
            pass
        except OSError:
            pass
        rc = proc.wait()
        if project_id and project_id in _job_procs:
            _job_procs[project_id] = [x for x in _job_procs[project_id] if x is not proc]
        try:
            err_f.close()
        except Exception:
            pass
    check_cancel(project_id)
    if rc != 0 or not out.exists() or out.stat().st_size < 1024:
        tail = ""
        try:
            if err_path.is_file():
                lines = [
                    ln.strip()
                    for ln in err_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip()
                ]
                tail = " | ".join(lines[-8:])[:500]
        except OSError:
            pass
        code = rc if rc is not None else -1
        if isinstance(code, int) and code > 2_000_000_000:
            code = code - 4_294_967_296
        raise RuntimeError(
            f"cover_and_burn ffmpeg failed (code={code})"
            + (f" — {tail}" if tail else " — broken pipe (ffmpeg thoát sớm)")
        )
    output_duration = float(ffprobe_duration(out) or 0.0)
    # Prefer container duration over OpenCV frame_count/fps (often inflated).
    src_dur = float(ffprobe_duration(video) or 0.0)
    expected_duration = (
        src_dur
        if src_dur > 0
        else (frame_total / fps if frame_total > 0 and fps > 0 else 0.0)
    )
    if not _burn_output_complete(
        written_frames, frame_total, fps, output_duration, expected_duration
    ):
        raise RuntimeError(
            "cover_and_burn produced a truncated video "
            f"({written_frames}/{frame_total} frames, "
            f"{output_duration:.3f}/{expected_duration:.3f}s)"
        )
    try:
        err_path.unlink(missing_ok=True)
    except OSError:
        pass
    return out
