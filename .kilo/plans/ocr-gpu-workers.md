# OCR dùng hết GPU — bỏ kẹp 2 luồng

## Hiện trạng (vì sao «2 luồng» + GPU rảnh)

Progress `OCR · 100/837 · 2 luồng` đến từ:

| Chỗ | Vấn đề |
|-----|--------|
| `overlay_scan.py` `_OverlayProbe` | **`min(..., 2)` kẹp cứng 2 worker** |
| `extract_parts/api.py` | `sub_cap = 2` khi CPU; overlay `sub_req = w_req//2` |
| `resources.adaptive_workers` kind=gpu | `per_job_mb=900` (OCR frame thực tế ~400–500MB) → pack ít job |
| `asr_translate` locate | `kind="cpu", cap=12` dù RapidOCR có CUDA |
| `overlay_scan` `_scan_overlay_stamps` | **for tuần tự** 1 frame — worker count gần như không dùng |

## Sửa (4 file)

### 1. `backend/pipeline/ocr/overlay_scan.py`
- Bỏ `min(..., 2)` → `self._w = max(1, workers)`.
- `_scan_overlay_stamps`: pool song song `ThreadPoolExecutor(max_workers=w)` + mỗi worker `VideoCapture` riêng (hoặc queue stamp + TLS capture) — **không share 1 cap seek**.
- Progress: `progress_msg(..., workers=w)`.

### 2. `backend/pipeline/ocr/extract_parts/api.py`
- GPU: `pack_gpu_workers(per_job_mb=450, reserve_mb=350, hard_max=20)`.
- CPU: cap `min(16, budget 0.92)`.
- Overlay: `sub_req = w_req or 0`, `sub_cap = max(4, gpu_cap)` — **không `//2` / không cap=2**.

### 3. `backend/pipeline/ocr/extract_parts/runtime.py`
- `_ocr_pool_workers` / `_ocr_semaphore`: `per_job_mb=450`, `hard_max=20`.

### 4. `backend/pipeline/core/resources.py`
- `adaptive_workers` kind=`gpu`: OCR `per_mb=450` (tts giữ 1500).

### 5. `backend/pipeline/orchestrate/asr_translate.py`
- Locate: nếu CUDA → `kind="gpu"` + pack 450MB; else cpu cap 14.

## UI gợi ý
- Sidebar **Luồng**: `0 = Tự động` (pack GPU). Muốn ép: 8/12/16.
- Sau fix, progress kỳ vọng khi card rảnh: **6–12+ luồng** (tùy VRAM), không kẹt 2.

## Verify
```text
# khi OCR chạy, Task Manager / nvidia-smi:
# GPU util ↑, nhiều process/thread OCR
# UI: «OCR · n/N · K luồng» với K >> 2
```

## Rủi ro
- Nhiều session ONNX GPU → OOM: `pack_gpu_workers` + semaphore vẫn chặn; nếu OOM hạ `per_job_mb` hoặc hard_max.
- Parallel VideoCapture: mỗi worker 1 handle (không share).

## Ngoài phạm vi
- Không đổi fps extract / logic merge OCR.
- Không đổi TTS worker.
