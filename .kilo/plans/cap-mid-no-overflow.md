# CAP-MID: chữ không tràn khung vàng OCR

## Phân tích ảnh

Chữ VI 2 dòng («Tôi đã hạ… / từ trên núi xuống») **tràn ra ngoài** khung vàng OCR:

| Nguyên nhân | Chi tiết |
|-------------|----------|
| Font fit seed rồi snug cover nhỏ hơn | `snugH = min(seed.h, needH)` trong khi font đã tính theo seed lớn → block chữ > cover |
| CSS `max(fontPx/h, 0.72/…)` | Sàn 0.72 cqh **phình** chữ khi cover thấp |
| `translateY(-0.06em)` | Đẩy chữ xuống → tràn đáy khung vàng |

## Hướng sửa (3 file)

### 1. `ocrOverlayLayout.ts` — `layoutMidOverlay`

- Fit font **trong seed gồm pad** (`needW/needH ≤ seed.w/h`) trước khi snug.
- 1 dòng ưu tiên; tràn → 2 dòng + co font đến `fitsSeed`.
- Cover = ôm chữ ≤ seed (cắt thừa); **không** để font lớn hơn cover sau snug.
- Pad đáy ~0.2em đủ che stroke (vẫn trong seed).

### 2. `coverLayout.ts` — `overlayDisplayFontStyle` (mid/label)

- Bỏ `Math.max(..., 0.72)` sàn.
- Chỉ `min(fontPx/h, 0.88/(n*lh))` + `min(fontPx/w, 0.94)`.

### 3. `LivePreviewEditor.tsx` — render mid

- Bỏ `transform: translateY(-0.06em)`.
- `padding: 0`; giữ `overflow-hidden` trên dòng.

## Verify

- Self-check: câu dài 2 dòng trong box OCR hẹp → cover ≤ seed, font*lines*1.12 ≤ cover.h.
- Project `8597d106ea29`: mid «Tôi đã hạ…» chữ nằm trong khung vàng.

## Ngoài phạm vi

- Không đổi OCR locate / Áp Y / caption đáy horizontal (trừ khi dùng chung `overlayDisplayFontStyle`).
