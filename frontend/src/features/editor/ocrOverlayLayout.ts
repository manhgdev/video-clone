/**
 * Layout che + chữ dịch cho overlay OCR (mid / dọc / nhãn).
 * Cover = bbox OCR đã định vị — chữ fit trong khung, không to/tràn ra ngoài.
 */
export type OcrCoverBox = { x: number; y: number; w: number; h: number }

export type OcrOverlayLayout = {
  cover: OcrCoverBox
  caption: OcrCoverBox
  lines: string[]
  mode: 'vertical' | 'label' | 'mid'
  fontPx: number
}

/** Fallback nhỏ — chỉ khi chưa có bbox OCR. */
export function ocrFallbackCover(
  frameW: number,
  frameH: number,
  layout: 'vertical' | 'label' | 'mid' | 'horizontal',
): OcrCoverBox {
  if (layout === 'vertical') {
    const w = Math.max(20, Math.round(frameW * 0.055))
    return {
      x: Math.round(frameW * 0.04),
      y: Math.round(frameH * 0.12),
      w,
      h: Math.round(frameH * 0.28),
    }
  }
  if (layout === 'label') {
    return {
      x: Math.round(frameW * 0.06),
      y: Math.round(frameH * 0.18),
      w: Math.round(frameW * 0.14),
      h: Math.round(frameH * 0.045),
    }
  }
  if (layout === 'mid') {
    const w = Math.round(frameW * 0.28)
    const h = Math.round(frameH * 0.045)
    return { x: Math.round((frameW - w) / 2), y: Math.round(frameH * 0.44), w, h }
  }
  const h = Math.round(frameH * 0.05)
  const w = Math.round(frameW * 0.36)
  return {
    x: Math.round((frameW - w) / 2),
    y: Math.round(frameH - h - Math.round(frameH * 0.06)),
    w,
    h,
  }
}

function clampBox(box: OcrCoverBox, frameW: number, frameH: number): OcrCoverBox {
  const w = Math.max(8, Math.min(box.w, frameW))
  const h = Math.max(8, Math.min(box.h, frameH))
  return {
    x: Math.max(0, Math.min(frameW - w, Math.round(box.x))),
    y: Math.max(0, Math.min(frameH - h, Math.round(box.y))),
    w: Math.round(w),
    h: Math.round(h),
  }
}

function estimateLineW(text: string, fontPx: number): number {
  let w = 0
  for (const c of text) {
    if (c >= '\u4e00' && c <= '\u9fff') w += fontPx
    else if (c === ' ') w += fontPx * 0.33
    else w += fontPx * 0.62
  }
  return Math.max(1, Math.ceil(w * 1.04))
}

/** Tách đơn vị xếp dọc: CJK = từng chữ; VI/Latin = từng từ (đứng, không xoay). */
export function verticalTextUnits(text: string): string[] {
  const raw = text.trim()
  if (!raw) return [' ']
  const cjk = [...raw].filter((c) => c >= '\u4e00' && c <= '\u9fff').length
  const compact = raw.replace(/\s+/g, '')
  if (cjk >= Math.max(2, compact.length * 0.5)) return [...compact]
  const words = raw.split(/[\s·・/|]+/).filter(Boolean)
  if (!words.length) return [raw]
  if (words.length === 1 && words[0].length > 10) {
    const w0 = words[0]
    const mid = Math.max(1, Math.floor(w0.length / 2))
    return [w0.slice(0, mid), w0.slice(mid)]
  }
  return words
}

/** Mid 1 glyph OCR nhầm trong cột watermark dọc — không hiện chữ dịch. */
export function midInsideVerticalWatermark(
  mid: {
    layout?: string
    source?: string
    start: number
    end: number
    bbox?: { x: number; y: number; w: number; h: number } | null
  },
  verticals: Array<{
    start: number
    end: number
    bbox?: { x: number; y: number; w: number; h: number } | null
  }>,
): boolean {
  if (mid.layout !== 'mid' || !mid.bbox) return false
  const src = (mid.source ?? '').replace(/\s+/g, '')
  const cjk = [...src].filter((c) => c >= '\u4e00' && c <= '\u9fff').length
  if (cjk !== 1 || cjk !== src.length) return false
  const mcx = mid.bbox.x + mid.bbox.w / 2
  const mcy = mid.bbox.y + mid.bbox.h / 2
  for (const v of verticals) {
    const vb = v.bbox
    if (!vb) continue
    if (mid.end <= v.start + 0.05 || mid.start >= v.end - 0.05) continue
    const padX = Math.max(12, vb.w * 0.35)
    const padYBot = Math.max(36, vb.h * 0.12)
    if (
      mcx >= vb.x - padX &&
      mcx <= vb.x + vb.w + padX &&
      mcy >= vb.y - 8 &&
      mcy <= vb.y + vb.h + padYBot
    ) {
      return true
    }
  }
  return false
}

/**
 * Font vừa khung. preferred = max gợi ý — vẫn shrink nếu tràn bbox.
 * (User yêu cầu: chữ chỉ trong bbox đã định vị.)
 */
export function fitOverlayFontPx(
  layout: 'vertical' | 'label' | 'mid',
  cover: OcrCoverBox,
  text: string,
  preferred = 0,
): number {
  const raw = text.trim() || ' '
  const laid =
    layout === 'vertical'
      ? layoutVerticalOverlay(cover, raw, preferred, 4096, 4096)
      : layout === 'mid'
        ? layoutMidOverlay(cover, raw, preferred, 4096, 4096)
        : layoutLabelOverlay(cover, raw, preferred, 4096, 4096)
  return laid.fontPx
}

function isCjkHeavyToken(token: string): boolean {
  const compact = token.replace(/\s+/g, '')
  if (!compact) return false
  const cjk = [...compact].filter((c) => c >= '\u4e00' && c <= '\u9fff').length
  return cjk >= Math.max(1, Math.ceil(compact.length * 0.5))
}

function wrapLines(text: string, innerW: number, fontPx: number): string[] {
  const raw = text.trim() || ' '
  const words = raw.split(/\s+/).filter(Boolean)
  const lines: string[] = []
  const pushWrapped = (token: string) => {
    if (estimateLineW(token, fontPx) <= innerW || !isCjkHeavyToken(token)) {
      lines.push(token)
      return
    }
    let cur = ''
    for (const ch of token) {
      const trial = cur + ch
      if (cur && estimateLineW(trial, fontPx) > innerW) {
        lines.push(cur)
        cur = ch
      } else cur = trial
    }
    if (cur) lines.push(cur)
  }
  if (!words.length) {
    pushWrapped(raw)
  } else {
    let cur = words[0]
    for (let i = 1; i < words.length; i++) {
      const trial = `${cur} ${words[i]}`
      if (estimateLineW(trial, fontPx) <= innerW) cur = trial
      else {
        pushWrapped(cur)
        cur = words[i]
      }
    }
    pushWrapped(cur)
  }
  return lines
}

/** Title dọc: cover = bbox OCR (không nới); thu font cho vừa cột. */
export function layoutVerticalOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  const cover = clampBox(coverIn, frameW, frameH)
  const units = verticalTextUnits(text)
  const n = Math.max(1, units.length)
  const padX = Math.max(1, Math.round(cover.w * 0.06))
  const padY = Math.max(2, Math.round(cover.h * 0.03))
  const gap0 = Math.max(1, Math.round(cover.h * 0.01))
  const innerW = Math.max(6, cover.w - padX * 2)
  const innerH = Math.max(8, cover.h - padY * 2)

  const seed =
    fontPxIn > 0
      ? Math.max(8, Math.min(48, Math.round(fontPxIn)))
      : Math.min(
          40,
          Math.floor(innerW * 0.9),
          Math.floor((innerH - gap0 * Math.max(0, n - 1)) / n),
        )

  let fontPx = Math.max(8, seed)
  const fits = (fs: number) => {
    const gap = Math.max(1, Math.round(fs * 0.1))
    const maxUnitW = Math.max(...units.map((u) => estimateLineW(u, fs)))
    const totalH = n * fs + gap * Math.max(0, n - 1)
    return maxUnitW <= innerW + 1 && totalH <= innerH + 0.5
  }
  while (fontPx > 8 && !fits(fontPx)) fontPx -= 1

  return {
    cover,
    caption: { ...cover },
    lines: units,
    mode: 'vertical',
    fontPx,
  }
}

/** Nhãn: cover = bbox; wrap + shrink trong khung. */
export function layoutLabelOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  const cover = clampBox(coverIn, frameW, frameH)
  const raw = text.trim() || ' '
  const LINE = 1.12
  const pad = Math.max(2, Math.round(Math.min(cover.w, cover.h) * 0.06))
  const innerW = Math.max(8, cover.w - pad * 2)
  const innerH = Math.max(8, cover.h - pad * 2)
  const compactLen = raw.replace(/\s+/g, '').length

  let fontPx =
    fontPxIn > 0
      ? Math.max(8, Math.min(40, Math.round(fontPxIn)))
      : Math.min(
          36,
          Math.floor(innerH * 0.7),
          Math.floor(innerW / Math.max(2, compactLen * 0.55)),
        )

  let lines = wrapLines(raw, innerW, fontPx)
  while (
    fontPx > 8 &&
    (lines.some((ln) => estimateLineW(ln, fontPx) > innerW + 1) ||
      lines.length * fontPx * LINE > innerH)
  ) {
    fontPx -= 1
    lines = wrapLines(raw, innerW, fontPx)
  }

  return {
    cover,
    caption: { ...cover },
    lines,
    mode: 'label',
    fontPx,
  }
}

/**
 * Mid = caption ngang trong bbox OCR (không nới 72% frame, không xếp dọc).
 * Cover giữ nguyên; thu font + wrap ≤3 dòng cho vừa.
 */
export function layoutMidOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  const cover = clampBox(coverIn, frameW, frameH)
  const raw = text.trim() || ' '
  const LINE = 1.1
  const MAX_LINES = 3
  const padX = Math.max(2, Math.round(cover.w * 0.03))
  const padY = Math.max(2, Math.round(cover.h * 0.05))
  const innerW = Math.max(10, cover.w - padX * 2)
  const innerH = Math.max(10, cover.h - padY * 2)
  const norm = (s: string) => s.replace(/\s+/g, ' ').trim()
  const allWordsKept = (ls: string[]) => norm(ls.join(' ')) === norm(raw)
  const words = raw.split(/\s+/).filter(Boolean)
  const compactLen = raw.replace(/\s+/g, '').length

  const pack = (fs: number): string[] => {
    if (estimateLineW(raw, fs) <= innerW) return [raw]
    return wrapLines(raw, innerW, fs)
  }

  const fits = (fs: number, lines: string[]) => {
    if (!lines.length || !allWordsKept(lines)) return false
    if (lines.length > MAX_LINES) return false
    if (lines.some((ln) => estimateLineW(ln, fs) > innerW + 1)) return false
    return lines.length * fs * LINE <= innerH + 0.5
  }

  let fontPx =
    fontPxIn > 0
      ? Math.max(8, Math.min(48, Math.round(fontPxIn)))
      : Math.min(
          40,
          Math.max(8, Math.floor(innerW / Math.max(3, compactLen * 0.58))),
          Math.floor(innerH / LINE),
          Math.floor(innerH * 0.55),
        )

  let lines = pack(fontPx)
  while (fontPx > 8 && !fits(fontPx, lines)) {
    fontPx -= 1
    lines = pack(fontPx)
  }
  // Ưu tiên 1–2 dòng ngang (không 1 từ/dòng)
  if (words.length <= 8 && lines.length > 2) {
    for (let fs = fontPx; fs >= 8; fs -= 1) {
      const trial = pack(fs)
      if (trial.length <= 2 && fits(fs, trial)) {
        fontPx = fs
        lines = trial
        break
      }
    }
  }
  if (lines.length > 1 && words.length <= 6) {
    for (let fs = Math.min(fontPx, Math.floor(innerH / LINE)); fs >= 8; fs -= 1) {
      if (estimateLineW(raw, fs) <= innerW && fs * LINE <= innerH) {
        fontPx = fs
        lines = [raw]
        break
      }
    }
  }
  while (fontPx > 8 && lines.some((ln) => estimateLineW(ln, fontPx) > innerW)) {
    fontPx -= 1
    lines = pack(fontPx)
  }
  while (fontPx > 8 && lines.length * fontPx * LINE > innerH) {
    fontPx -= 1
    lines = pack(fontPx)
  }

  // caption = inset trong cover — không phình ra ngoài bbox
  const caption = {
    x: cover.x + padX,
    y: cover.y + padY,
    w: Math.max(6, cover.w - padX * 2),
    h: Math.max(6, cover.h - padY * 2),
  }

  return { cover, caption, lines, mode: 'mid', fontPx }
}

export function layoutOcrOverlay(
  layout: 'vertical' | 'label' | 'mid',
  cover: OcrCoverBox,
  text: string,
  fontPx: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  if (layout === 'vertical') return layoutVerticalOverlay(cover, text, fontPx, frameW, frameH)
  if (layout === 'mid') return layoutMidOverlay(cover, text, fontPx, frameW, frameH)
  return layoutLabelOverlay(cover, text, fontPx, frameW, frameH)
}

/** ponytail: self-check — chữ trong bbox */
export function __checkOcrOverlayLayout() {
  const midBox = { x: 200, y: 700, w: 240, h: 56 }
  const mid = layoutMidOverlay(midBox, 'Đào hoa quả', 0, 1080, 1920)
  if (mid.cover.w !== midBox.w || mid.cover.h !== midBox.h) {
    throw new Error('mid must keep OCR cover size')
  }
  if (mid.fontPx * 1.1 > mid.caption.h + 1) {
    throw new Error('mid font must fit caption height, got ' + mid.fontPx)
  }
  if (mid.lines.some((ln) => estimateLineW(ln, mid.fontPx) > mid.caption.w + 2)) {
    throw new Error('mid line wider than caption')
  }
  // preferred to vẫn shrink
  const forced = layoutMidOverlay(midBox, 'Đào hoa quả', 64, 1080, 1920)
  if (forced.fontPx > Math.floor(forced.caption.h / 1.05)) {
    throw new Error('preferred mid must shrink into bbox')
  }
  const longMid = layoutMidOverlay(
    { x: 100, y: 800, w: 420, h: 90 },
    'Tôi mang theo một mảnh da trơn',
    0,
    1080,
    1920,
  )
  if (longMid.cover.w !== 420) throw new Error('long mid must not widen cover')
  if (longMid.lines.length > 3) throw new Error('mid max 3 lines')
  if (longMid.lines.length * longMid.fontPx * 1.1 > longMid.caption.h + 2) {
    throw new Error('long mid block taller than caption')
  }

  const vBox = { x: 40, y: 120, w: 48, h: 220 }
  const v = layoutVerticalOverlay(vBox, 'Cây màu tím', 0, 1080, 1920)
  if (v.cover.w !== vBox.w || v.cover.h !== vBox.h) {
    throw new Error('vertical must keep OCR cover')
  }
  if (v.fontPx > vBox.w) throw new Error('vertical font wider than column')
  if (v.lines.length * v.fontPx > vBox.h) {
    throw new Error('vertical stack taller than column')
  }
  // không nới cột
  const vWide = layoutVerticalOverlay(vBox, 'Hoa và màu tím rất đẹp', 40, 1080, 1920)
  if (vWide.cover.w > vBox.w) throw new Error('vertical must not widen for VI')
  if (vWide.fontPx > 40) throw new Error('vertical preferred must not grow')

  const L = layoutLabelOverlay({ x: 50, y: 200, w: 90, h: 40 }, 'Đậu xanh', 0, 1080, 1920)
  if (L.cover.h !== 40 || L.cover.w !== 90) throw new Error('label must keep cover')
  if (L.fontPx > 40) throw new Error('label font must fit height')

  const short = layoutMidOverlay({ x: 196, y: 912, w: 136, h: 72 }, 'Đào hoa quả', 0, 1080, 1920)
  if (short.cover.w !== 136) throw new Error('short mid cover fixed')
  // 1 từ Latin
  const name = layoutMidOverlay({ x: 400, y: 900, w: 88, h: 64 }, 'Shaqin', 0, 1080, 1920)
  if (name.lines[0] !== 'Shaqin') throw new Error('latin word intact')
}
