/**
 * Layout che + chữ dịch cho overlay OCR (mid / dọc / nhãn).
 * Cover = sát ink OCR; font fit khung — không phình như phụ đề đáy.
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
    else if (c === ' ') w += fontPx * 0.35
    else w += fontPx * 0.68 // VI/Latin bold hay rộng hơn 0.55
  }
  return Math.max(1, Math.ceil(w))
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

/** Font vừa khung; preferred>0 = giữ cỡ user (không auto đè). */
export function fitOverlayFontPx(
  layout: 'vertical' | 'label' | 'mid',
  cover: OcrCoverBox,
  text: string,
  preferred = 0,
): number {
  const raw = text.trim() || ' '
  if (preferred > 0) return Math.max(10, Math.min(120, Math.round(preferred)))

  if (layout === 'vertical') {
    const units = verticalTextUnits(raw)
    const n = Math.max(1, units.length)
    // ưu tiên đọc được: tối thiểu ~16; trần theo cột (không kẹp 28 cứng)
    let fs = Math.min(cover.w * 0.88, (cover.h * 0.9) / n)
    for (const u of units) {
      const need = estimateLineW(u, fs)
      if (need > cover.w * 0.95) fs = Math.min(fs, (cover.w * 0.95 * fs) / need)
    }
    const maxFs = Math.max(28, Math.min(72, Math.floor(cover.w * 0.95)))
    return Math.max(16, Math.min(maxFs, Math.floor(fs)))
  }
  if (layout === 'mid') {
    let fs = Math.min(cover.h * 0.7, cover.w * 0.28)
    while (fs > 10 && estimateLineW(raw, fs) > cover.w * 0.95) fs -= 1
    return Math.max(12, Math.min(48, Math.round(fs)))
  }
  // label
  let fs = Math.min(cover.h * 0.62, cover.w * 0.32)
  const innerW = Math.max(10, cover.w - 4)
  while (fs > 10 && estimateLineW(raw, fs) > innerW) fs -= 1
  return Math.max(12, Math.min(40, Math.round(fs)))
}

function wrapLines(text: string, innerW: number, fontPx: number): string[] {
  const raw = text.trim() || ' '
  const words = raw.split(/\s+/).filter(Boolean)
  const lines: string[] = []
  const pushWrapped = (token: string) => {
    if (estimateLineW(token, fontPx) <= innerW) {
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

/** Title dọc: xếp đứng; nới cột theo chữ (OCR hẹp → VI vẫn đọc được). */
export function layoutVerticalOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  const seed = clampBox(coverIn, frameW, frameH)
  const units = verticalTextUnits(text)
  const n = Math.max(1, units.length)

  // Auto trên cột OCR quá hẹp → nới tạm để đo font đọc được
  let measure = seed
  if (!(fontPxIn > 0) && seed.w < 36) {
    const w = Math.min(frameW, Math.max(36, Math.round(frameW * 0.07)))
    const cx = seed.x + seed.w / 2
    measure = clampBox({ ...seed, x: cx - w / 2, w }, frameW, frameH)
  }
  const fontPx = fitOverlayFontPx('vertical', measure, text, fontPxIn)

  const padX = Math.max(4, Math.round(fontPx * 0.18))
  const padY = Math.max(6, Math.round(fontPx * 0.28))
  const gap = Math.max(2, Math.round(fontPx * 0.12))
  const maxUnitW = Math.max(...units.map((u) => estimateLineW(u, fontPx)), fontPx)
  const needW = Math.ceil(maxUnitW + padX * 2)
  const needH = Math.ceil(n * fontPx + gap * Math.max(0, n - 1) + padY * 2)

  const cx = seed.x + seed.w / 2
  const cy = seed.y + seed.h / 2
  const w = Math.min(frameW, Math.max(seed.w, needW))
  const h = Math.min(frameH, Math.max(seed.h, needH))
  const cover = clampBox(
    {
      x: cx - w / 2,
      y: seed.h >= needH ? seed.y : cy - h / 2,
      w,
      h,
    },
    frameW,
    frameH,
  )

  return {
    cover,
    caption: { ...cover },
    lines: units,
    mode: 'vertical',
    fontPx,
  }
}

/** Nhãn: cover giữ nguyên ink; wrap trong khung; thu font nếu tràn. */
export function layoutLabelOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  const cover = clampBox(coverIn, frameW, frameH)
  let fontPx = fitOverlayFontPx('label', cover, text, fontPxIn)
  const pad = Math.max(2, Math.round(fontPx * 0.12))
  const innerW = Math.max(10, cover.w - pad * 2)
  let lines = wrapLines(text, innerW, fontPx)
  const lineH = Math.ceil(fontPx * 1.15)
  while (fontPx > 9 && lines.length * lineH > cover.h - pad) {
    fontPx -= 1
    lines = wrapLines(text, Math.max(10, cover.w - Math.max(2, Math.round(fontPx * 0.12)) * 2), fontPx)
  }
  return {
    cover,
    caption: { ...cover },
    lines,
    mode: 'label',
    fontPx,
  }
}

/** Mid flash: 1–2 dòng trong khung sát ink. */
export function layoutMidOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  const cover = clampBox(coverIn, frameW, frameH)
  let fontPx = fitOverlayFontPx('mid', cover, text, fontPxIn)
  const pad = Math.max(2, Math.round(fontPx * 0.1))
  const innerW = Math.max(12, cover.w - pad * 2)
  let lines = wrapLines(text, innerW, fontPx)
  if (lines.length > 2) {
    fontPx = Math.max(10, fontPx - 2)
    lines = wrapLines(text, innerW, fontPx).slice(0, 2)
  }
  return {
    cover,
    caption: { ...cover },
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
): OcrOverlayLayout {
  if (layout === 'vertical') return layoutVerticalOverlay(cover, text, fontPx, frameW, frameH)
  if (layout === 'mid') return layoutMidOverlay(cover, text, fontPx, frameW, frameH)
  return layoutLabelOverlay(cover, text, fontPx, frameW, frameH)
}

/** ponytail: self-check */
export function __checkOcrOverlayLayout() {
  const cover = { x: 100, y: 400, w: 160, h: 36 }
  const m = layoutMidOverlay(cover, 'Nấu sôi', 0, 1080, 1920)
  if (m.fontPx > 48 || m.cover.w > 180) throw new Error('mid must stay tight')
  const L = layoutLabelOverlay({ x: 50, y: 200, w: 90, h: 40 }, 'Đậu xanh', 0, 1080, 1920)
  if (L.cover.h !== 40 || L.fontPx > 40) throw new Error('label must not inflate cover')
  const v = layoutVerticalOverlay({ x: 40, y: 120, w: 48, h: 220 }, 'Cây màu tím', 0, 1080, 1920)
  if (v.lines.length < 3) throw new Error('vertical VI must stack words')
  if (v.fontPx < 16) throw new Error('vertical auto must stay readable')
  if (v.fontPx * v.lines.length > v.cover.h + 8) throw new Error('vertical stack must fit cover height')
  const manual = layoutVerticalOverlay({ x: 40, y: 120, w: 28, h: 180 }, 'Hoa và màu tím', 42, 1080, 1920)
  if (manual.fontPx !== 42) throw new Error('manual font must stick')
  if (manual.cover.w < 28) throw new Error('manual vertical must widen cover for text')
}
