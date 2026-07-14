/**
 * Default cover cho overlay OCR (title dọc / nhãn / flash giữa khung).
 *
 * Tách khỏi đường dịch + phụ đề đáy (LivePreviewEditor / burn layout).
 * Đừng import vào caption path — file đó đang chuẩn, không sửa theo OCR.
 */
export type OcrCoverBox = { x: number; y: number; w: number; h: number }

export function ocrFallbackCover(
  frameW: number,
  frameH: number,
  layout: 'vertical' | 'label' | 'mid' | 'horizontal',
): OcrCoverBox {
  if (layout === 'vertical') {
    const w = Math.max(14, Math.round(frameW * 0.08))
    return { x: Math.round((frameW - w) / 2), y: Math.round(frameH * 0.28), w, h: Math.round(frameH * 0.5) }
  }
  if (layout === 'label') {
    return {
      x: Math.round(frameW * 0.06),
      y: Math.round(frameH * 0.12),
      w: Math.round(frameW * 0.22),
      h: Math.round(frameH * 0.3),
    }
  }
  if (layout === 'mid') {
    const w = Math.round(frameW * 0.55)
    const h = Math.round(frameH * 0.07)
    return { x: Math.round((frameW - w) / 2), y: Math.round(frameH * 0.4), w, h }
  }
  const h = Math.round(frameH * 0.06)
  const w = Math.round(frameW * 0.4)
  return {
    x: Math.round((frameW - w) / 2),
    y: Math.round(frameH - h - Math.round(frameH * 0.06)),
    w,
    h,
  }
}
