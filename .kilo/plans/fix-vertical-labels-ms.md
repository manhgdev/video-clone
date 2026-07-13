# Fix tiêu đề dọc (ms) + nhãn nhỏ (không TTS)

## Vấn đề (video cà tím `2deb80b772b3`)

Từ ảnh + OCR thực tế:

1. **Tiêu đề dọc** `茄子的神仙吃法` → `Phép thuật cà tím` chỉ hiện **~50–80ms** (1–2 frame @25fps), nhưng code hardcode `t1 = 1.35s` và burn pad `+0.4s` → che bar giữa khung quá lâu.
2. **Cover quá to**: fallback/clamp cột dọc `w*0.07–0.11` + nới Y `0.12–0.80` → vệt đen/blur giữa khung (ảnh 1).
3. **Chữ burn xấu**: vertical Latin/VI tách từ → mỗi “dòng” 1 từ, font scale down, trông “như cút”.
4. **Chưa dịch nhãn nhỏ** (ảnh 2: 胡椒粉/蚝油/料酒/葱姜/盐…) — chữ graphic, **không lồng tiếng**.

## Root cause

| File | Chỗ | Bug |
|------|-----|-----|
| `server/pipeline/asr.py` `_ocr_vertical_titles` | `t1 = min(video_end, 1.35)` | Hardcode 1.35s thay vì đo ms |
| `server/pipeline/asr.py` | không có pass nhãn | Chỉ hardsub đáy + title dọc |
| `server/pipeline/export/burn.py` `cover_and_burn` | vertical `burn_end = max(e0, burn_start+0.4)` | Pad thêm 400ms |
| `server/pipeline/export/burn.py` | `half = max(w*0.07…)` + `y0/y1` full cột | Cover phình |
| `server/pipeline/export/burn.py` `_layout_caption_vertical` | words split + scale | Chữ Việt dọc xấu |
| `server/pipeline/run.py` `run_dub` | chỉ skip TTS `layout==vertical` | Cần skip `label` nữa |

## Plan

### 1. ASR — đo duration title dọc theo ms

File: `server/pipeline/asr.py`

- Viết lại `_ocr_vertical_titles`:
  - Quét đầu clip (~0–4s) + cuối (~3s cuối) với `step_ms = max(40, 1000/fps)` (~1 frame).
  - Mỗi hit = `(t_sec, text)` khi `_ocr_vertical_from_frame` có CJK dọc.
  - Gom cụm liên tiếp (gap ≤ 220ms, cùng chữ) → `start = first_hit`, `end = last_hit + step_ms/1000` (**không** pad 1.35s).
- `_ocr_seg`: `min_dur = 0.04` cho `vertical`/`label` (cho phép flash < 0.35s).

### 2. ASR — pass nhãn nhỏ (không TTS)

File: `server/pipeline/asr.py`

- Thêm `_ocr_overlay_labels` + `_ocr_labels_from_frame`:
  - Sample ~2 fps (`step=0.5s`) full video.
  - OCR bỏ 18% đáy (hardsub); giữ box: bên trái/phải, nhỏ, hoặc cột hẹp.
  - Bỏ title dọc full giữa + hardsub ngang đáy.
  - Ghép khung cùng text (gap ≤ 1.2s) → segment `layout="label"`.
  - Nhiều nhãn ngắn cùng frame: join `"·"` (vd. `盐·葱姜·料酒·蚝油·胡椒粉`).
- Gọi pass 3 sau vertical; merge qua `_merge_horizontal_vertical` (tránh trùng hardsub).
- `_ocr_seg` / merge: cho phép `layout in (horizontal, vertical, label)`.

### 3. Burn — timing + cover sát + chữ đẹp

File: `server/pipeline/export/burn.py`

**Timing (`cover_and_burn`):**
```python
if layout in ("vertical", "label"):
    burn_start = max(0.0, s0)
    burn_end = max(e0, burn_start + 0.04)  # bám ms, không +0.4
    cover_start, cover_end = burn_start, burn_end
```

**Cover vertical — bám OCR, không phình:**
- Bỏ clamp `half = max(w*0.07…)` và nới Y `0.12–0.80`.
- Dùng bbox OCR thật + pad nhỏ: `pad_x = max(4, w*0.008)`, `pad_y = max(4, h*0.004)`.
- Fallback (không OCR): cột hẹp `w*0.46–0.54`, `h*0.28–0.78` (không full bar).
- Cover fit = union OCR + text box, pad nhỏ — **không** force full-height column.

**Cover label:**
- Box OCR nhỏ quanh nhãn; layout text near box (over hoặc cạnh).
- Reuse `_layout_caption` horizontal nhỏ (fontsize ~0.55–0.7×) hoặc vertical hẹp nếu box cao/hẹp.

**Chữ dọc đẹp hơn (`_layout_caption_vertical` + `_caption_overlay`):**
- Việt/Latin: wrap 1–2 dòng **ngang** trong cột (không stack từng từ dọc) nếu text không CJK; CJK vẫn stack từng ký tự.
- Tăng stroke rõ: outline 2–3px, fill trắng; optional soft shadow nhẹ.
- Font size bám bề ngang OCR: `size ≈ ocr_w * 0.55–0.7`, clamp theo `subtitle_font_size`.
- Căn giữa trong cột hẹp; gap chữ CJK `size//12` (không quá thưa).

### 4. Dub — skip TTS cho label

File: `server/pipeline/run.py` `run_dub`:

```python
if str(seg.get("layout") or "") in ("vertical", "label"):
    # không TTS
    continue
```

### 5. Types (optional, UI)

File: `src/types.ts` — thêm `layout?: 'horizontal' | 'vertical' | 'label'` vào `Segment` (hiển thị badge “nhãn / không TTS” nếu UI đã có chỗ).

### 6. Verify

- Re-run ASR project `2deb80b772b3` (engine paddleocr/screen).
- Expect:
  - Segment vertical: `start≈0`, `end≈0.04–0.12` (ms-level), translation giữ.
  - Segments label: các cụm nguyên liệu/tên món, `layout=label`, có translation sau MT, **không** audio TTS.
  - Export: cover chỉ vài frame đầu, bar không phình; chữ dọc đọc được; nhãn nhỏ được che + đè bản dịch tại chỗ.

## Files chạm

1. `server/pipeline/asr.py` — timing vertical ms + OCR labels
2. `server/pipeline/export/burn.py` — no pad, tight cover, better vertical/label paint
3. `server/pipeline/run.py` — skip TTS `label`
4. `src/types.ts` — optional `layout` field

## Không làm

- Không đổi engine ASR/MT mặc định.
- Không TTS nhãn/title (đúng yêu cầu “ko lồng tiếng”).
- Không quét full-frame OCR mỗi frame cho labels (chỉ ~2fps).
