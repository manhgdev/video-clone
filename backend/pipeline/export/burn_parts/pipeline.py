"""Hardsub cover + caption burn — pipeline."""
from __future__ import annotations

"""Cover hardsubs + burn translated captions."""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pipeline.ocr.extract import _rapidocr_gpu_kwargs
from pipeline.core.jobs import _job_procs, check_cancel
from pipeline.core.media import ffprobe_duration, h264_encoder_args, nvdec_available, video_size
from pipeline.core.project import ensure_layout, set_status
from pipeline.core.resources import adaptive_workers
from pipeline.ocr.extract import _ocr_join_lines, _rapidocr_labels
from pipeline.ocr.cover_timing import resolve_cover_window
from pipeline.ocr.overlay_cover import mid_bottom_cutoff
from pipeline.ocr.labels import (
    clamp_label_box,
    cover_fit_label,
    is_tall_label,
    is_vertical_cjk_source,
    layout_label_caption,
    pick_label_box,
)
from pipeline.ocr.locate import (
    ocr_mid_hardsub_boxes,
    ocr_mid_labels,
    ocr_mid_vertical,
)
from pipeline.translate import _clean_burn_text

# aliases — giữ tên cũ cho call sites / tests
_clamp_label_box = clamp_label_box
_pick_label_box = pick_label_box
_ocr_mid_labels = ocr_mid_labels
_ocr_mid_vertical = ocr_mid_vertical
_ocr_mid_hardsub_boxes = ocr_mid_hardsub_boxes

from .ass_util import write_ass, _ass_time, _ass_text
from .layout_geo import *  # noqa: F403
from .layout_text import *  # noqa: F403
from .ocr_boxes import *  # noqa: F403
from pipeline.export.fonts import _font_for_preset, _subtitle_font, _subtitle_font_vertical
from pipeline.export.cover_mask import _apply_cover_mask


def _burn_frame_count_complete(written: int, expected: int, fps: float) -> bool:
    """Allow normal decoder rounding, never accept a materially truncated render."""
    tolerance = max(2, int(math.ceil(max(1.0, fps) * 0.5)))
    return written > 0 and (expected <= 0 or written >= max(1, expected - tolerance))


def cover_and_burn(
    video: Path,
    segments: list[dict[str, Any]],
    out: Path,
    *,
    cover: bool,
    burn: bool = True,
    subtitle_font_size: int = 0,
    subtitle_font_family: str = "",
    project_id: str | None = None,
    workers: int = 0,
    caption_placement: str = "below",
    cover_mask_style: str = "blur",
    cover_mask_color: str = "#4c1d95",
    cover_mask_opacity: int = 40,
    caption_text_color: str = "#ffffff",
    caption_bg_style: str = "none",
    caption_bg_color: str = "#000000",
    caption_bg_opacity: int = 55,
    caption_stroke: bool = True,
) -> Path:
    """cover = blur hardsub; burn = đè chữ dịch. placement: below|above khi không cover."""
    import cv2
    from PIL import ImageFont

    if not cover and not burn:
        shutil.copy2(video, out)
        return out

    from pipeline.ocr.extract import _drop_mid_in_watermark_column

    segments = _drop_mid_in_watermark_column(list(segments))

    w, h = video_size(video)
    auto_fontsize = int(subtitle_font_size or 0) <= 0
    fontsize = (
        _auto_subtitle_font_size(w, h)
        if auto_fontsize
        else max(16, min(120, int(subtitle_font_size)))
    )
    workers = _resolve_workers(workers)
    place = (caption_placement or "below").lower()
    if place not in ("below", "above"):
        place = "below"
    mask_style = (cover_mask_style or "blur").lower()
    if mask_style not in ("blur", "solid", "mosaic"):
        mask_style = "blur"
    mask_color = str(cover_mask_color or "#4c1d95")
    mask_opacity = max(0, min(100, int(cover_mask_opacity if cover_mask_opacity is not None else 40)))
    cap_fill_hex = str(caption_text_color or "#ffffff")
    cap_bg_style = str(caption_bg_style or "none").lower()
    if cap_bg_style not in ("none", "solid", "blur", "box"):
        cap_bg_style = "none"
    cap_bg_hex = str(caption_bg_color or "#000000")
    cap_bg_op = max(0, min(100, int(caption_bg_opacity if caption_bg_opacity is not None else 55)))
    cap_stroke = bool(caption_stroke if caption_stroke is not None else True)
    # cover → chữ đè đúng dải OCR; không cover → above/below hardsub (mid cũng below/above)
    layout_place = "over" if cover else place
    font = ImageFont.truetype(_font_for_preset(subtitle_font_family), fontsize)

    # (cover_start, cover_end, burn_start, burn_end, text, source, layout)
    # Cover nới rộng hơn burn: hardsub hay hiện trước/sau ASR; burn vẫn bám timecode.
    cues: list[tuple[float, float, float, float, str, str, str]] = []
    cue_segment_ids: list[str] = []
    for seg in segments:
        raw = (seg.get("translation") or "").strip()
        source = (seg.get("source") or "").strip()
        burn_text = _clean_burn_text(raw)
        mask_only = bool(seg.get("maskOnly"))
        # maskOnly: vùng hiệu ứng tự do (làm mờ) — không cần chữ
        if not burn_text and not mask_only:
            continue
        layout = str(seg.get("layout") or "horizontal")
        if layout not in ("horizontal", "vertical", "label", "mid"):
            layout = "horizontal"
        s0 = float(seg["start"])
        e0 = float(seg["end"])
        # Heuristic: title dọc flash đầu clip (layout bị mất khi UI save cũ)
        if layout == "horizontal":
            src = (seg.get("source") or "").strip()
            cjk = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
            dur = max(0.0, e0 - s0)
            if (
                s0 < 2.0
                and dur <= 0.55
                and cjk >= 3
                and cjk >= len(re.sub(r"\s+", "", src)) * 0.7
            ):
                layout = "vertical"
        if layout == "vertical":
            # Khớp preview: chữ + mask theo coverWindow
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start, burn_end = cover_start, max(cover_end, cover_start + 0.04)
        elif layout == "label":
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start, burn_end = cover_start, max(cover_end, cover_start + 0.04)
        elif layout == "mid":
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start, burn_end = cover_start, max(cover_end, cover_start + 0.04)
        else:
            # Cover nới để che hardsub; BURN chữ dịch = clip timeline [start,end)
            cover_start, cover_end = resolve_cover_window(seg)
            burn_start = max(0.0, s0)
            burn_end = max(e0, burn_start + 0.04)
        if mask_only:
            # Effect region: che theo [start,end), không burn chữ
            cover_start, cover_end = max(0.0, s0), max(e0, s0 + 0.04)
            burn_start, burn_end = cover_start, cover_start  # zero-length burn
            burn_text = ""
        cues.append(
            (cover_start, cover_end, burn_start, burn_end, burn_text, source, layout)
        )
        cue_segment_ids.append(str(seg.get("id") or ""))
    # Lấp khe cover nhỏ giữa 2 câu hardsub (không đụng tiêu đề dọc / nhãn).
    for i in range(len(cues) - 1):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[i]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[i + 1]
        if lay0 in ("vertical", "label") or lay1 in ("vertical", "label"):
            continue
        if lay0 == "mid" or lay1 == "mid":
            continue  # mid xử lý riêng — không lấp khe kiểu đáy
        gap = cs1 - ce0
        if 0.0 < gap < 0.45:
            mid = (ce0 + cs1) * 0.5
            cues[i] = (cs0, mid, bs0, be0, t0, src0, lay0)
            cues[i + 1] = (mid, ce1, bs1, be1, t1, src1, lay1)
    # Mid-mid: cắt cover/burn tại giữa khe — không đè «hoàn thiện» bằng câu sau
    mid_idx = [i for i, c in enumerate(cues) if (c[6] if len(c) > 6 else "") == "mid"]
    for a, b in zip(mid_idx, mid_idx[1:]):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[a]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[b]
        # dùng biên burn lõi (gần [start,end) segment) để chia
        core_end = min(be0, ce0)
        core_start = max(bs1, cs1)
        cut = (core_end + core_start) * 0.5
        if ce0 > cut:
            cues[a] = (cs0, min(ce0, cut), bs0, min(be0, cut), t0, src0, lay0)
        if cs1 < cut:
            cues[b] = (max(cs1, cut), ce1, max(bs1, cut), be1, t1, src1, lay1)
    # Horizontal-horizontal: cắt COVER chồng (tail câu trước đè bbox sau)
    for i in range(len(cues) - 1):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[i]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[i + 1]
        if lay0 != "horizontal" or lay1 != "horizontal":
            continue
        # cắt tại giữa [end0, start1] (clip timeline), không theo cover pad
        # lấy segment gốc từ ids
        sid0 = cue_segment_ids[i] if i < len(cue_segment_ids) else ""
        sid1 = cue_segment_ids[i + 1] if i + 1 < len(cue_segment_ids) else ""
        seg0 = next((s for s in segments if str(s.get("id") or "") == sid0), None)
        seg1 = next((s for s in segments if str(s.get("id") or "") == sid1), None)
        e0 = float(seg0["end"]) if seg0 else be0
        s1 = float(seg1["start"]) if seg1 else bs1
        cut = (e0 + s1) * 0.5
        if ce0 > cut:
            cues[i] = (cs0, min(ce0, cut), bs0, be0, t0, src0, lay0)
        if cs1 < cut:
            cues[i + 1] = (max(cs1, cut), ce1, bs1, be1, t1, src1, lay1)
    # Không cho cửa sổ burn chữ dịch chồng nhau — chỉ hardsub đáy (ngang).
    # vertical/label/mid khác vị trí → được phép overlap (watermark dọc xuyên clip).
    for i in range(len(cues) - 1):
        cs0, ce0, bs0, be0, t0, src0, lay0 = cues[i]
        cs1, ce1, bs1, be1, t1, src1, lay1 = cues[i + 1]
        if lay0 != "horizontal" or lay1 != "horizontal":
            continue
        if be0 > bs1:
            cut = (be0 + bs1) * 0.5
            cues[i] = (cs0, ce0, bs0, max(bs0 + 0.04, cut), t0, src0, lay0)
            cues[i + 1] = (cs1, ce1, min(be1 - 0.04, cut), be1, t1, src1, lay1)

    ocr = None
    segments_by_id = {str(seg.get("id") or ""): seg for seg in segments}
    manual_by_idx: list[tuple[int, int, int, int] | None] = []
    for sid in cue_segment_ids:
        seg = segments_by_id.get(sid, {})
        mb = _segment_bbox_override(seg, w, h)
        # Bbox đáy bake sẵn + source CJK → bỏ, OCR lại vị trí thật (giữa/đáy)
        manual_by_idx.append(mb)
    cue_boxes: list[list[tuple[int, int, int, int]]] = [[] for _ in cues]
    for i, mb in enumerate(manual_by_idx):
        if mb is not None:
            cue_boxes[i] = [mb]

    need_ocr_idx = [i for i, mb in enumerate(manual_by_idx) if mb is None]
    manual_n = len(cues) - len(need_ocr_idx)
    if need_ocr_idx and (cover or burn) and cues:
        try:
            ocr = _rapidocr_labels()
        except ImportError:
            ocr = None
        if ocr is not None:
            if project_id:
                set_status(
                    project_id,
                    step="export",
                    progress=10,
                    message=(
                        f"Định vị hardsub ({len(need_ocr_idx)} câu)…"
                        if manual_n
                        else "Định vị hardsub…"
                    ),
                    running=True,
                )
            if len(need_ocr_idx) == len(cues):
                from pipeline.core.resources import adaptive_workers, gpu_job_cap

                cuda = bool(_rapidocr_gpu_kwargs()["det_use_cuda"])
                ocr_w = adaptive_workers(
                    workers,
                    kind="gpu" if cuda else "cpu",
                    cap=gpu_job_cap() if cuda else max(1, workers or 8),
                    tasks=len(cues),
                )
                cue_boxes = _precompute_cue_boxes(
                    video,
                    cues,
                    ocr,
                    project_id=project_id,
                    workers=ocr_w,
                )
            else:
                import cv2 as _cv2

                probe = _cv2.VideoCapture(str(video))
                try:
                    fh = int(probe.get(_cv2.CAP_PROP_FRAME_HEIGHT) or h)
                    fw = int(probe.get(_cv2.CAP_PROP_FRAME_WIDTH) or w)
                finally:
                    probe.release()
                from pipeline.core.resources import adaptive_workers, gpu_job_cap

                cuda = bool(_rapidocr_gpu_kwargs()["det_use_cuda"])
                ocr_workers = adaptive_workers(
                    workers,
                    kind="gpu" if cuda else "cpu",
                    cap=gpu_job_cap() if cuda else 16,
                    tasks=len(need_ocr_idx),
                )
                with ThreadPoolExecutor(
                    max_workers=ocr_workers, thread_name_prefix="ocr"
                ) as pool:
                    for i, boxes in pool.map(
                        lambda idx: (idx, _ocr_cue_boxes(video, cues[idx], ocr, fw, fh)),
                        need_ocr_idx,
                    ):
                        cue_boxes[i] = boxes
            ocr = None
    elif manual_n and (cover or burn) and project_id:
        set_status(
            project_id,
            step="export",
            progress=15,
            message=f"Dùng vùng che đã chỉnh trong preview ({manual_n} câu)",
            running=True,
        )

    if project_id and (cover or burn) and cues:
        set_status(
            project_id,
            step="export",
            progress=16,
            message=f"Chuẩn bị caption / mask ({len(cues)} câu)…",
            running=True,
        )

    font_cache: dict[int, Any] = {fontsize: font}

    def _font_for_size(size: int):
        fs = max(8, min(120, int(size)))
        cached = font_cache.get(fs)
        if cached is not None:
            return cached
        cached = ImageFont.truetype(_font_for_preset(subtitle_font_family), fs)
        font_cache[fs] = cached
        return cached

    # frame mẫu giữa cue label/dọc — expand cover theo mực chữ thật
    label_probe_frames: dict[int, Any] = {}
    if any(
        _should_paint_cover_mask(cover, (c[6] if len(c) > 6 else ""))
        and (c[6] if len(c) > 6 else "") in ("label", "vertical")
        for c in cues
    ):
        import cv2 as _cv2

        _cap = _cv2.VideoCapture(str(video))
        try:
            for i, cue in enumerate(cues):
                lay_m = cue[6] if len(cue) > 6 else "horizontal"
                if lay_m not in ("label", "vertical"):
                    continue
                mid = (float(cue[0]) + float(cue[1])) * 0.5
                _cap.set(_cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
                ok, fr = _cap.read()
                if ok:
                    label_probe_frames[i] = fr
        finally:
            _cap.release()

    cue_overlays: list[tuple[Any, int, int] | None] = []
    # mỗi cue: 1+ vùng cover (nhãn multi-box)
    cue_fits: list[list[tuple[int, int, int, int]]] = []
    cue_need_mask: list[bool] = []
    for i, (_cs, _ce, _bs, _be, text, src, lay_mode) in enumerate(cues):
        segment_id = cue_segment_ids[i] if i < len(cue_segment_ids) else ""
        boxes = list(cue_boxes[i] if i < len(cue_boxes) else [])
        paint = _union_box(boxes) if boxes else None
        has_manual_bbox = manual_by_idx[i] is not None
        is_vert = lay_mode == "vertical"
        is_label = lay_mode == "label"
        is_mid = lay_mode == "mid"
        uses_bbox_caption = lay_mode in ("horizontal", "mid")
        src_s = (src or "").strip()
        use_label_style = is_label
        # Fallback paint: coverHardsubs hoặc overlay mid/dọc (preview vẫn mask khi burn)
        if paint is None and _should_paint_cover_mask(cover, lay_mode):
            from pipeline.ocr.overlay_cover import default_overlay_paint, is_mid_flash_source

            if is_vert:
                paint = default_overlay_paint("vertical", w, h)
            elif is_label:
                paint = default_overlay_paint("label", w, h)
            elif is_mid or is_mid_flash_source(src_s):
                paint = default_overlay_paint("mid", w, h)
            else:
                paint = (int(w * 0.08), int(h * 0.84), int(w * 0.92), int(h * 0.94))
            boxes = [paint]
        # OCR bbox giữa khung → che/chữ tại đó (không ép đáy dù layout=horizontal)
        paint_mid = False
        if paint is not None and not is_vert and not is_label:
            pcy = (paint[1] + paint[3]) * 0.5
            # Keep identical to FE effectiveOverlayLayout().
            paint_mid = h * 0.18 < pcy < h * mid_bottom_cutoff(w, h)
            if paint_mid:
                is_mid = True
        if is_vert and paint is not None:
            if has_manual_bbox:
                # bbox editor = khung mask/chữ — không expand ink lại
                boxes = [paint]
                layout_paint = paint
                ink = None
            else:
                x0, y0, x1, y1 = paint
                bw = x1 - x0
                if bw > int(w * 0.18):
                    half = max(12, min(int(w * 0.05), bw // 4 + 4))
                    if (x0 + x1) / 2 < w * 0.5:
                        x1 = min(w, x0 + half * 2)
                    else:
                        x0 = max(0, x1 - half * 2)
                cjk_paint = (x0, y0, x1, y1)
                # Cover = mực (CJK+HUAMUZI); caption cao theo cover, ngang bám CJK.
                ink = _expand_vertical_watermark_cover(
                    cjk_paint, w, h, frame_bgr=label_probe_frames.get(i)
                )
                paint = cjk_paint
                boxes = [ink]
                # cho layout: cùng cx CJK, cao đủ tới đáy ink
                layout_paint = (cjk_paint[0], ink[1], cjk_paint[2], ink[3])
        else:
            layout_paint = paint
            ink = None
        # nhãn: xử lý TỪNG box (không union to)
        label_tall = False
        cover_regions: list[tuple[int, int, int, int]] = []
        if use_label_style:
            if has_manual_bbox and paint is not None:
                cover_regions = [paint]
                boxes = [paint]
                label_tall = is_tall_label(paint)
            else:
                probe = label_probe_frames.get(i)
                raw_boxes = boxes if boxes else ([paint] if paint else [])
                refined: list[tuple[int, int, int, int]] = []
                for b in raw_boxes:
                    bb = b
                    if probe is not None:
                        try:
                            from pipeline.ocr.labels import expand_box_to_ink

                            bb = expand_box_to_ink(probe, bb, w, h)
                        except Exception:
                            pass
                    tall_b = is_tall_label(bb)
                    bb = clamp_label_box(bb, w, h, force_tall=tall_b)
                    refined.append(bb)
                boxes = refined or boxes
                # paint = box chính (lớn nhất) để đặt chữ
                if boxes:
                    paint = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                label_tall = bool(paint) and is_tall_label(paint)
                for b in boxes:
                    fit = cover_fit_label(
                        b,
                        None,
                        w,
                        h,
                        frame_bgr=probe,
                        force_tall=is_tall_label(b),
                    )
                    if fit:
                        cover_regions.append(fit)
        cover_box: tuple[int, int, int, int] | None = None
        cue_fs = fontsize
        used_preview_layout = False
        if burn and text:
            seg_meta = segments_by_id.get(segment_id, {})
            cue_font_getter = _font_for_size
            cue_font_family = str(seg_meta.get("fontFamily") or "").strip()
            if cue_font_family:
                cue_font_cache: dict[int, Any] = {}

                def cue_font_getter(size: int):
                    fs = max(8, min(120, int(size)))
                    cached = cue_font_cache.get(fs)
                    if cached is None:
                        try:
                            cached = ImageFont.truetype(_font_for_preset(cue_font_family), fs)
                        except OSError:
                            cached = _font_for_size(fs)
                        cue_font_cache[fs] = cached
                    return cached
            cue_fs = _resolve_segment_font_size(
                seg_meta, w, h,
                project_font_size=subtitle_font_size,
                default_font_size=fontsize,
                auto_fontsize=auto_fontsize,
            )
            cue_font = cue_font_getter(cue_fs)
            cue_font_path = str(
                getattr(cue_font, "path", "")
                or _font_for_preset(cue_font_family or subtitle_font_family)
            )
            # WYSIWYG: captionLayout từ editor (đã bake giống preview lúc Xuất)
            preview_lay = _preview_caption_layout(
                seg_meta, cue_fs, cue_font_getter, layout_mode=lay_mode,
            )
            editor_locked = _editor_layout_locked(seg_meta)
            lay: dict[str, Any] | None = None
            # below/above: không ép mid layout "over" — dùng placement above/below OCR
            force_below_above = (not cover) and layout_place in ("below", "above")
            # Caption + CAP-MID share the same bbox-fitting engine. Their tags
            # and timing lanes remain separate.
            if uses_bbox_caption and paint is not None and not force_below_above:
                if preview_lay is not None and editor_locked:
                    lay = preview_lay
                    cover_box = paint
                    used_preview_layout = True
                else:
                    cl = seg_meta.get("captionLayout") if isinstance(seg_meta.get("captionLayout"), dict) else {}
                    mid_pref = int(
                        seg_meta.get("fontSize")
                        or (cl.get("fontSize") if cl else 0)
                        or 0
                    )
                    lay = _layout_mid_caption(
                        text,
                        cue_font_getter,
                        paint,
                        w,
                        h,
                        preferred_fs=mid_pref,
                    )
                    cover_box = paint if lay else paint
                    used_preview_layout = cover_box is not None
            elif preview_lay is not None:
                use_preview = True
                if not editor_locked:
                    if paint is not None and paint_mid:
                        if _caption_layout_looks_bottom(seg_meta, h) or (
                            has_manual_bbox and _bbox_looks_bottom(paint, h)
                        ):
                            use_preview = False
                        elif _bbox_looks_bottom(preview_lay["box"], h) and paint_mid:
                            use_preview = False
                    if use_preview and not paint_mid:
                        if (
                            _caption_layout_looks_bottom(seg_meta, h)
                            and sum(1 for c in src_s if "\u4e00" <= c <= "\u9fff") >= 2
                            and lay_mode in ("horizontal", "mid")
                        ):
                            use_preview = False
                if use_preview:
                    lay = preview_lay
                    used_preview_layout = True
                    if editor_locked:
                        mb = _segment_bbox_override(seg_meta, w, h)
                        cover_box = mb if mb is not None else (paint if paint is not None else preview_lay["box"])
                    elif has_manual_bbox and paint is not None and not paint_mid:
                        cover_box = paint
                    elif paint_mid and paint is not None:
                        cover_box = paint
                    else:
                        mb = _segment_bbox_override(seg_meta, w, h)
                        if mb is not None and not _stored_cover_should_relocate(seg_meta, mb, h):
                            cover_box = mb
                        else:
                            cover_box = paint
            if lay is None and is_vert:
                lay = _layout_caption_vertical(
                    text, cue_font, cue_fs, layout_paint if layout_paint else paint, w, h
                )
            elif lay is None and uses_bbox_caption and paint is not None and not force_below_above:
                mid_pref = int(seg_meta.get("fontSize") or 0)
                lay = _layout_mid_caption(
                    text,
                    cue_font_getter,
                    paint,
                    w,
                    h,
                    preferred_fs=mid_pref,
                )
                cover_box = lay["box"] if lay else paint
            elif lay is None and use_label_style:
                lab_fs = max(18, int(cue_fs * (0.75 if is_label else 0.85)))
                lay = layout_label_caption(
                    text,
                    cue_font,
                    lab_fs,
                    paint,
                    w,
                    h,
                    font_path=cue_font_path,
                    force_vertical=label_tall,
                    source=src_s,
                )
            elif lay is None and (layout_place == "over" or has_manual_bbox) and paint is not None:
                # Overlay OCR không có captionLayout → classic / manual / auto-over
                from pipeline.ocr.overlay_cover import (
                    classic_cover_fit,
                    use_classic_overlay_cover,
                )

                if has_manual_bbox:
                    cover_box = paint
                if use_classic_overlay_cover(
                    layout=lay_mode,
                    source=src_s,
                    has_preview_layout=False,
                ):
                    # đường riêng overlay — layout chữ trong OCR box + cover fit c9
                    lay = _layout_caption(
                        text, cue_font, cue_fs, paint, w, h, placement="over"
                    )
                    cover_box = classic_cover_fit(
                        boxes if boxes else ([paint] if paint else []),
                        lay["box"] if lay else None,
                        w,
                        h,
                    )
                elif has_manual_bbox:
                    lay = _layout_caption_in_cover(
                        text, cue_fs, paint, w, cue_font_getter,
                    )
                    cover_box = paint
                else:
                    lay, cover_box = _layout_caption_over(
                        text, cue_fs, paint, w, h,
                        source_text=src_s,
                        font_path=cue_font_path,
                    )
            elif lay is None:
                lay = _layout_caption(
                    text, cue_font, cue_fs, paint, w, h, placement=layout_place
                )
        else:
            lay = None
        if lay is not None:
            # In cover mode the Editor renders text inside the full mask bbox;
            # captionLayout contributes only its committed lines/font size.
            if cover and used_preview_layout and cover_box is not None:
                lay = {
                    **lay,
                    "box": cover_box,
                    "css_cover_mode": (
                        "label" if is_label else "mid" if uses_bbox_caption else "horizontal"
                    ),
                }
            lay = {
                **lay,
                "fill_hex": str(seg_meta.get("textColor") or cap_fill_hex),
                "bg_style": cap_bg_style,
                "bg_hex": cap_bg_hex,
                "bg_opacity": cap_bg_op,
                "stroke": cap_stroke,
            }
        logo_asset = str(seg_meta.get("logoAssetPath") or "")
        cue_overlays.append(
            _image_overlay(logo_asset, tuple(map(int, lay["box"])))
            if logo_asset and lay else (_caption_overlay(lay) if lay else None)
        )
        # cover=True: che hardsub; below/above: chỉ dọc/nhãn (không che mid)
        # is_mid khi cover=False không được bật mask (tránh giống «che chữ + chèn»)
        need_mask = _should_paint_cover_mask(
            cover, lay_mode if not (is_mid and not cover) else ("mid" if cover else "horizontal")
        )
        if not cover and is_mid:
            need_mask = False
        if seg_meta.get("skipCoverMask"):
            need_mask = False
        if seg_meta.get("maskOnly"):
            # Effect region: luôn che đúng bbox editor
            need_mask = True
            paint = _segment_bbox_override(seg_meta, w, h) or paint
            cover_box = paint
        cue_need_mask.append(need_mask)
        if need_mask:
            if seg_meta.get("maskOnly") and paint is not None:
                cue_fits.append([paint])
            elif used_preview_layout and cover_box is not None:
                cue_fits.append([cover_box])
            elif used_preview_layout and paint is not None:
                cue_fits.append([paint])
            elif use_label_style:
                # chữ nằm trên paint — nới cover chính nếu cần, vẫn từng box
                if lay and paint is not None and not has_manual_bbox:
                    main = cover_fit_label(
                        paint,
                        lay["box"],
                        w,
                        h,
                        frame_bgr=label_probe_frames.get(i),
                        force_tall=label_tall,
                    )
                    if main:
                        # thay box chính trong regions
                        cover_regions = [
                            main
                            if (
                                abs(r[0] - paint[0]) < 30
                                and abs(r[1] - paint[1]) < 30
                            )
                            else r
                            for r in cover_regions
                        ]
                        if not any(
                            abs(r[0] - main[0]) < 20 and abs(r[1] - main[1]) < 20
                            for r in cover_regions
                        ):
                            cover_regions.append(main)
                cue_fits.append(cover_regions or ([paint] if paint else []))
            elif is_vert and paint is not None:
                # Cover đã tính ink ở boxes[0]
                cov = boxes[0] if boxes else paint
                cue_fits.append([cov])
            elif is_mid and paint is not None:
                cb = cover_box if cover_box is not None else paint
                # nới nhẹ X cho stroke; Y giữ sát (pad locate đã đủ)
                px = max(6, int(round(w * 0.008)))
                py = max(3, int(round(h * 0.002)))
                cue_fits.append(
                    [
                        (
                            max(0, cb[0] - px),
                            max(0, cb[1] - py),
                            min(w, cb[2] + px),
                            min(h, cb[3] + py),
                        )
                    ]
                )
            else:
                if layout_place == "over" and cover_box is not None:
                    # Đúng khung preview — không _cover_box_over (fit/phình lại)
                    cue_fits.append([cover_box])
                elif layout_place == "over" and has_manual_bbox and paint is not None:
                    cue_fits.append([paint])
                elif layout_place == "over" and lay:
                    cue_fits.append([lay["box"]])
                elif has_manual_bbox and paint is not None and layout_place != "over":
                    cue_fits.append([paint])
                else:
                    one = _cover_box_fit(
                        boxes,
                        lay["box"] if lay else None,
                        w,
                        h,
                        tight=layout_place == "over",
                    )
                    cue_fits.append([one] if one else [])
        else:
            cue_fits.append([])

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
            # Một write/batch — giảm syscall, nuôi NVENC đều hơn
            try:
                if proc.poll() is not None:
                    pipe_dead = True
                    break
                proc.stdin.write(b"".join(batch_raw))
                written_frames += len(batch_raw)
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
    expected_duration = frame_total / fps if frame_total > 0 else 0.0
    if (
        not _burn_frame_count_complete(written_frames, frame_total, fps)
        or (
            expected_duration > 0
            and output_duration + 0.5 < expected_duration
        )
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

