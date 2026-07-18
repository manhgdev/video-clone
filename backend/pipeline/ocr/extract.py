"""OCR extract facade — re-exports split parts for backward compatibility."""
from __future__ import annotations

from .extract_parts.api import asr_paddleocr
from .extract_parts.merge import (  # noqa: F401
    _drop_mid_in_watermark_column,
    _fold_duplicate_watermark_labels,
    _fold_vertical_column_flickers,
    _merge_horizontal_vertical,
    _merge_label_segments,
    _merge_mid_segments,
    _merge_whisper_hardsub_fragments,
    _ocr_cluster_hits,
    _ocr_pad_hardsub_windows,
    _ocr_segments_from_timeline,
    _trim_vertical_ocr_tail,
)
from .extract_parts.runtime import (  # noqa: F401
    _cpu_budget,
    _limit_onnx_threads,
    _nvidia_bin_dirs,
    _ocr_pool_workers,
    _ocr_semaphore,
    _rapidocr_gpu_kwargs,
    _rapidocr_labels,
    prepare_cuda_dlls,
)
from .extract_parts.scan import (  # noqa: F401
    _classify_overlay_detections,
    _ocr_edge_stamps,
    _ocr_label_items_from_frame,
    _ocr_labels_from_frame,
    _ocr_mid_hardsub_from_frame,
    _ocr_mid_hardsubs,
    _ocr_mid_item_from_frame,
    _ocr_overlay_boxes_from_frame,
    _ocr_overlay_labels,
    _ocr_scan_stamps,
    _ocr_seg,
    _ocr_vertical_from_frame,
    _ocr_vertical_item_from_frame,
    _ocr_vertical_titles,
)
from .extract_parts.textutil import (  # noqa: F401
    _cjk_len,
    _hardsub_line_keep,
    _is_cjk,
    _join_whisper_sources,
    _ocr_box_wh,
    _ocr_fix_zh,
    _ocr_join_lines,
    _ocr_label_overlap,
    _ocr_norm,
    _ocr_pick_best,
    _ocr_same,
    _ocr_sim,
    _union_bbox,
    _xyxy_to_bbox,
)

__all__ = [
    "asr_paddleocr",
    "prepare_cuda_dlls",
    "_rapidocr_gpu_kwargs",
    "_rapidocr_labels",
    "_ocr_join_lines",
    "_merge_label_segments",
    "_merge_mid_segments",
    "_ocr_cluster_hits",
    "_ocr_label_overlap",
    "_ocr_overlay_boxes_from_frame",
    "_ocr_pick_best",
    "_ocr_pool_workers",
    "_ocr_same",
    "_ocr_seg",
    "_ocr_semaphore",
    "_ocr_sim",
    "_drop_mid_in_watermark_column",
]
