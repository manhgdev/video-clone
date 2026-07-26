/**
 * Đo chữ caption trên canvas + wrap dòng — nguồn sự thật cho mọi layout
 * (preview + bake xuất). Tách từ coverLayout.ts để tái sử dụng độc lập.
 */
import { setOcrMeasureFontFamily } from '@/features/editor/ocrOverlayLayout'

/** Khoảng thở ngang trong toạ độ video; tương đương ~6px ở preview điện thoại. */
export const CAP_PAD_X = 16

let _measureCtx: CanvasRenderingContext2D | null = null
let _measureFontFamily = '"VC Noto Sans", sans-serif'

/** ponytail: sync measurement font with the CSS render font */
export function setMeasureFontFamily(css: string) {
  if (css && css !== _measureFontFamily) {
    _measureFontFamily = css
    setOcrMeasureFontFamily(css)
  }
}

export function measureLineWidth(text: string, fontSizePx: number) {
  if (typeof document !== 'undefined') {
    if (!_measureCtx) {
      const c = document.createElement('canvas')
      _measureCtx = c.getContext('2d')
    }
    if (_measureCtx) {
      _measureCtx.font = `700 ${fontSizePx}px ${_measureFontFamily}`
      const m = _measureCtx.measureText(text)
      const left = Number.isFinite(m.actualBoundingBoxLeft) ? Math.abs(m.actualBoundingBoxLeft) : 0
      const right = Number.isFinite(m.actualBoundingBoxRight) ? Math.abs(m.actualBoundingBoxRight) : 0
      const raw = Math.max(m.width || 0, left + right)
      // Slack nhỏ cố định — tránh *1.08 làm cover phình lệch mép frame
      return Math.ceil(raw + Math.max(6, fontSizePx * 0.12))
    }
  }
  return Math.ceil(text.length * fontSizePx * 0.42)
}

/** Bề ngang cover cần cho 1 dòng (mực đã measure + pad 2 bên). */
export function lineNeedWidth(text: string, fontSizePx: number) {
  return Math.ceil(measureLineWidth(text, fontSizePx) + CAP_PAD_X * 2)
}

/** Xuống dòng — đổ ngang tối đa trước, rồi mới cân 2–3 dòng */
export function wrapCaptionText(text: string, maxInnerW: number, fontSizePx: number, maxLines = 3): string[] {
  const trimmed = text.trim()
  if (!trimmed) return ['']
  if (maxLines <= 1) return [trimmed]

  const fits = (s: string) => measureLineWidth(s, fontSizePx) <= maxInnerW
  if (fits(trimmed)) return [trimmed]

  const words = trimmed.split(/\s+/).filter(Boolean)
  if (words.length <= 1) return [trimmed]

  const lineWidth = (s: string) => measureLineWidth(s, fontSizePx)

  const lines: string[] = []
  let cur = words[0]
  for (let i = 1; i < words.length; i++) {
    const trial = `${cur} ${words[i]}`
    if (fits(trial)) cur = trial
    else {
      lines.push(cur)
      cur = words[i]
      if (lines.length >= maxLines - 1) {
        lines.push([cur, ...words.slice(i + 1)].join(' '))
        break
      }
    }
  }
  if (lines.length < maxLines || !lines.length) lines.push(cur)
  let out = lines.slice(0, maxLines)

  while (out.length > 1) {
    const last = out[out.length - 1]
    const prev = out[out.length - 2]
    const merged = `${prev} ${last}`
    if (fits(merged)) out.splice(-2, 2, merged)
    else break
  }

  // Tránh dòng cuối mồ côi — chỉ cân 2 dòng khi bắt buộc wrap (không ép 2 dòng khi 1 dòng đủ)
  if (out.length >= 2 && maxLines >= 2 && !fits(trimmed)) {
    const last = out[out.length - 1]
    const lastW = lineWidth(last)
    const orphan = last.split(/\s+/).length <= 2 && lastW < maxInnerW * 0.28
    if (orphan || out.length >= 3) {
      let best: string[] | null = null
      let bestScore = Infinity
      for (let i = 1; i < words.length; i++) {
        const a = words.slice(0, i).join(' ')
        const b = words.slice(i).join(' ')
        if (!fits(a) || !fits(b)) continue
        const wa = lineWidth(a)
        const wb = lineWidth(b)
        const score = Math.abs(wa - wb) + (wb < maxInnerW * 0.22 ? 80 : 0)
        if (score < bestScore) {
          bestScore = score
          best = [a, b]
        }
      }
      if (best) out = best
    }
  }

  return out
}

/**
 * Ưu tiên 1 dòng (co font) → mới 2 dòng. maxLines mặc định 2.
 */
export function fitCaptionLines(
  text: string,
  maxInnerW: number,
  fontSizePx: number,
  opts?: { preferOneLine?: boolean; minFont?: number; maxLines?: number },
): { lines: string[]; fontPx: number } {
  const preferOneLine = opts?.preferOneLine !== false
  const minFont = opts?.minFont ?? 12
  const maxLines = opts?.maxLines ?? 2
  const trimmed = text.trim()
  if (!trimmed) return { lines: [''], fontPx: fontSizePx }
  let fontPx = Math.max(minFont, Math.round(fontSizePx))
  const inner = Math.max(4, maxInnerW)
  if (preferOneLine) {
    // One line is worthwhile only while it stays visually close to the lane
    // font. Below 90%, use two readable lines instead of a tiny single line.
    const minOneLineFont = Math.max(minFont, Math.round(fontSizePx * 0.9))
    while (fontPx > minOneLineFont && measureLineWidth(trimmed, fontPx) > inner) {
      fontPx -= 1
    }
    if (measureLineWidth(trimmed, fontPx) <= inner) {
      return { lines: [trimmed], fontPx }
    }
  }

  fontPx = Math.max(minFont, Math.round(fontSizePx))
  let lines = wrapCaptionText(trimmed, inner, fontPx, maxLines)
  while (
    fontPx > minFont
    && lines.some((line) => measureLineWidth(line, fontPx) > inner)
  ) {
    fontPx -= 1
    lines = wrapCaptionText(trimmed, inner, fontPx, maxLines)
  }
  return { lines, fontPx }
}

/** Đo bề ngang mực chữ nguồn (CJK hardsub + outline) — không theo VI. */
export function measureSourceInkWidth(sourceText: string, fontSizePx: number, anchorH: number) {
  const trimmed = sourceText.trim()
  if (!trimmed) return 0
  // Hardsub on-screen thường to hơn font burn; ưu tiên H OCR
  const sourceFontPx = Math.max(Math.round(fontSizePx * 1.12), Math.round(anchorH * 0.92), 28)
  const raw = measureLineWidth(trimmed, sourceFontPx)
  const cjk = [...trimmed].filter((c) => c >= '一' && c <= '鿿').length
  // CJK hardsub ~1.15em/glyph + viền dày hai bên
  const cjkFloor = cjk > 0 ? Math.ceil(cjk * sourceFontPx * 1.15) : 0
  const outline = Math.ceil(sourceFontPx * 0.5)
  return Math.max(Math.ceil(raw * 1.2), cjkFloor) + outline
}

export function isCjkHardsubSource(src: string | undefined): boolean {
  let cjk = 0
  for (const c of src ?? '') {
    if (c >= '一' && c <= '鿿') cjk += 1
  }
  return cjk >= 2
}
