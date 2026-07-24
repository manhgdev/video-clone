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
  // Khớp system-ui bold preview (~0.52em Latin, không 0.62 → wrap sớm CAP-MID)
  let w = 0
  for (const c of text) {
    if (c >= '\u4e00' && c <= '\u9fff') w += fontPx * 1.0
    else if (c === ' ') w += fontPx * 0.28
    else if (/[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i.test(c))
      w += fontPx * 0.52
    else w += fontPx * 0.52
  }
  return Math.max(1, Math.ceil(w * 1.12))
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
  // ponytail: all-caps single word (brand/abbreviation) → 1 char per line
  if (words.length === 1 && /^[A-Z]+$/.test(words[0])) return [...words[0]]
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
 * Mid = chữ + mask trong bbox OCR vàng.
 * Font co đến khi text+pad ≤ seed; cover ôm chữ (≤ seed) — không tràn khung vàng.
 */
export function layoutMidOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
  allowExpand = true,
): OcrOverlayLayout {
  let seed = clampBox(coverIn, frameW, frameH)
  const raw = text.trim() || ' '
  const LINE = 1.12
  const MAX_LINES = 2
  const words = raw.split(/\s+/).filter(Boolean)

  const pads = (fs: number) => ({
    x: Math.max(4, Math.round(fs * 0.14)),
    top: Math.max(3, Math.round(fs * 0.1)),
    bot: Math.max(5, Math.round(fs * 0.2)),
  })

  const blockSize = (fs: number, lines: string[]) => {
    const p = pads(fs)
    const textW = Math.max(...lines.map((ln) => estimateLineW(ln, fs)), 1)
    const textH = Math.ceil(lines.length * fs * LINE)
    return {
      p,
      needW: Math.ceil(textW + p.x * 2),
      needH: Math.ceil(textH + p.top + p.bot),
    }
  }

  const fitsSeed = (fs: number, lines: string[]) => {
    if (!lines.length || fs < 8) return false
    const { needW, needH } = blockSize(fs, lines)
    const maxLineW = seed.w - pads(fs).x * 2
    return (
      needW <= seed.w
      && needH <= seed.h
      && lines.every((ln) => estimateLineW(ln, fs) <= maxLineW)
    )
  }

  const pack2 = (f: number): string[] => {
    const maxW = Math.max(8, seed.w - pads(f).x * 2)
    if (estimateLineW(raw, f) <= maxW) return [raw]
    if (words.length < 2) return [raw]
    // ponytail: merge overflow lines into last line — never drop text
    const wrapped = wrapLines(raw, maxW, f)
    let best: string[] = wrapped.length <= MAX_LINES
      ? wrapped
      : [...wrapped.slice(0, MAX_LINES - 1), wrapped.slice(MAX_LINES - 1).join(' ')]
    let bestScore = Infinity
    for (let i = 1; i < words.length; i++) {
      const a = words.slice(0, i).join(' ')
      const b = words.slice(i).join(' ')
      if (estimateLineW(a, f) > maxW + 1 || estimateLineW(b, f) > maxW + 1) continue
      const score = Math.abs(estimateLineW(a, f) - estimateLineW(b, f))
      if (score < bestScore) {
        bestScore = score
        best = [a, b]
      }
    }
    return best
  }

  // Keep a little more breathing room than the raw mask height; the bbox
  // includes source stroke/cover slack, not just the translated glyphs.
  const hCap = Math.max(10, Math.floor((seed.h / LINE) * 0.72))
  if (allowExpand && raw.trim()) {
    const oneLineFont = Math.min(fontPxIn > 0 ? Math.round(fontPxIn) : hCap, hCap, 56)
    const oneLineW = blockSize(oneLineFont, [raw]).needW
    if (oneLineW > seed.w && oneLineW <= frameW) {
      const cx = seed.x + seed.w / 2
      const x = Math.max(0, Math.min(frameW - oneLineW, Math.round(cx - oneLineW / 2)))
      seed = { ...seed, x, w: oneLineW }
    }
  }
  let font1 = Math.min(fontPxIn > 0 ? Math.round(fontPxIn) : hCap, hCap, 56)
  while (font1 > 8 && !fitsSeed(font1, [raw])) font1 -= 1

  const hCap2 = Math.floor((seed.h / (LINE * 2)) * 0.82)
  let font2 = Math.min(fontPxIn > 0 ? Math.round(fontPxIn) : hCap2, hCap2, 44)
  let lines2 = pack2(font2)
  while (font2 > 8 && !fitsSeed(font2, lines2)) {
    font2 -= 1
    lines2 = pack2(font2)
  }

  let fontPx: number
  let lines: string[]
  if (font2 > font1) {
    fontPx = font2
    lines = lines2
  } else {
    fontPx = font1
    lines = [raw]
  }

  // Co thêm nếu block vẫn > seed (an toàn)
  while (fontPx > 8 && !fitsSeed(fontPx, lines)) {
    fontPx -= 1
    if (lines.length > 1) lines = pack2(fontPx)
  }

  // Cover = seed OCR vàng — nhưng cho phép nới ngang nếu chữ dịch dài hơn
  let cover = { ...seed }
  const p2 = pads(fontPx)
  // Co font lần cuối nếu block > cover chiều cao
  while (
    fontPx > 8
    && lines.length * fontPx * LINE + p2.top + p2.bot > cover.h + 0.5
  ) {
    fontPx -= 1
  }

  // ponytail: nới rộng cover ngang nếu chữ dịch tràn bbox gốc
  const p2b = pads(fontPx)
  const maxLineW = Math.max(...lines.map((ln) => estimateLineW(ln, fontPx)), 0)
  const needW = Math.ceil(maxLineW + p2b.x * 2)
  if (allowExpand && needW > cover.w) {
    const cx = cover.x + cover.w / 2
    const newW = Math.min(frameW, needW)
    const newX = Math.max(0, Math.min(frameW - newW, Math.round(cx - newW / 2)))
    cover = { ...cover, x: newX, w: newW }
  }

  const p3 = pads(fontPx)
  return {
    cover,
    caption: {
      x: cover.x + p3.x,
      y: cover.y + p3.top,
      w: Math.max(6, cover.w - p3.x * 2),
      h: Math.max(6, cover.h - p3.top - p3.bot),
    },
    lines,
    mode: 'mid',
    fontPx,
  }
}
export function layoutOcrOverlay(
  layout: 'vertical' | 'label' | 'mid',
  cover: OcrCoverBox,
  text: string,
  fontPx: number,
  frameW: number,
  frameH: number,
  allowExpand = true,
): OcrOverlayLayout {
  if (layout === 'vertical') return layoutVerticalOverlay(cover, text, fontPx, frameW, frameH)
  if (layout === 'mid') return layoutMidOverlay(cover, text, fontPx, frameW, frameH, allowExpand)
  return layoutLabelOverlay(cover, text, fontPx, frameW, frameH)
}

/** ponytail: self-check — chữ trong bbox */
export function __checkOcrOverlayLayout() {
  const midBox = { x: 200, y: 700, w: 240, h: 56 }
  const mid = layoutMidOverlay(midBox, 'Đào hoa quả', 0, 1080, 1920)
  // Height locked to seed
  if (mid.cover.h > midBox.h + 1) {
    throw new Error('mid must not grow past OCR cover height')
  }
  // Font fit trong cover (caption inset có pad đáy)
  if (mid.fontPx * 1.12 > mid.cover.h - 4) {
    throw new Error('mid font must fit cover height, got ' + mid.fontPx)
  }
  if (mid.lines.some((ln) => estimateLineW(ln, mid.fontPx) > mid.cover.w - 4)) {
    throw new Error('mid line wider than cover')
  }
  const forced = layoutMidOverlay(midBox, 'Đào hoa quả', 64, 1080, 1920)
  if (forced.fontPx > Math.floor(forced.cover.h / 1.12) + 4) {
    throw new Error('preferred mid must shrink into bbox')
  }
  const longMid = layoutMidOverlay(
    { x: 100, y: 800, w: 420, h: 90 },
    'Tôi mang theo một mảnh da trơn',
    0,
    1080,
    1920,
  )
  if (longMid.cover.h > 90 + 1) throw new Error('long mid must not grow past seed h')
  if (longMid.lines.length > 2) throw new Error('mid max 2 lines')
  if (longMid.lines.length * longMid.fontPx * 1.12 > longMid.cover.h - 2) {
    throw new Error('long mid block taller than cover')
  }
  // Câu dài 2 dòng trong box hẹp: font*lines*lh + pad ≤ cover h
  const overflowMid = layoutMidOverlay(
    { x: 200, y: 1100, w: 380, h: 72 },
    'Tôi đã hạ từ trên núi xuống',
    0,
    1080,
    1920,
  )
  if (overflowMid.cover.h > 72 + 1) {
    throw new Error('overflow mid cover height must stay ≤ seed')
  }
  if (overflowMid.lines.length * overflowMid.fontPx * 1.12 > overflowMid.cover.h + 1) {
    throw new Error('overflow mid text taller than cover')
  }
  if (overflowMid.lines.some((ln) => estimateLineW(ln, overflowMid.fontPx) > overflowMid.cover.w - 4)) {
    throw new Error('overflow mid line wider than cover')
  }
  // Câu ngắn CAP-MID: 1 dòng ngang (không xếp 2 dòng dọc)
  const shortNice = layoutMidOverlay(
    { x: 400, y: 500, w: 200, h: 120 },
    'Ngoài ra còn có tre',
    0,
    1080,
    1920,
  )
  if (shortNice.lines.length > 2) {
    throw new Error('short mid max 2 lines, got ' + shortNice.lines.join('|'))
  }
  // Bbox OCR cao/rộng: snug ôm chữ (cover nhỏ hơn seed)
  const tall = layoutMidOverlay(
    { x: 309, y: 1111, w: 463, h: 136 },
    'Ngoài ra còn có tre',
    0,
    1080,
    1920,
  )
  if (tall.lines.length > 2) throw new Error('project mid max 2 lines')
  if (tall.fontPx < 28) throw new Error('mid font too small: ' + tall.fontPx)
  // Pad đáy đủ che stroke; không dải thừa lớn
  if (tall.cover.h < tall.fontPx * 1.2) {
    throw new Error('mid cover too short for glyph bottom: ' + tall.cover.h)
  }
  // Cover height = seed height (locked)

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
  if (short.lines.length > 2) throw new Error('short mid max 2 lines')
  // 1 từ Latin
  const name = layoutMidOverlay({ x: 400, y: 900, w: 88, h: 64 }, 'Shaqin', 0, 1080, 1920)
  if (name.lines[0] !== 'Shaqin') throw new Error('latin word intact')
}
