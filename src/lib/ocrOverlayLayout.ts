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
    else if (c === ' ') w += fontPx * 0.32
    else w += fontPx * 0.52 // VI/Latin — thiên hẹp để wrap lấp ngang (bold CSS vẫn vừa)
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

/** Mid 1 glyph OCR nhầm trong cột watermark dọc — không hiện chữ dịch (vd. 尔 → Bạn). */
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

/** Font vừa khung; preferred>0 = giữ cỡ user (không auto đè). Khung lớn → chữ lớn. */
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
    // ~78% chiều cao cột chia đều cho các đơn vị; rộng tới ~88% cột
    let fs = Math.min(cover.w * 0.88, (cover.h * 0.78) / n)
    for (const u of units) {
      const need = estimateLineW(u, fs)
      if (need > cover.w * 0.92) fs = Math.min(fs, (cover.w * 0.92 * fs) / need)
    }
    const maxFs = Math.max(
      22,
      Math.min(72, Math.floor(cover.w * 0.95), Math.floor(cover.h * 0.45)),
    )
    return Math.max(18, Math.min(maxFs, Math.floor(fs)))
  }
  if (layout === 'mid') {
    // Ước lượng thô — layoutMidOverlay sẽ max theo ngang thật
    const short = raw.replace(/\s+/g, '').length <= 8
    const fs = short ? cover.h * 0.52 : cover.h * 0.38
    const maxFs = Math.max(28, Math.min(68, Math.floor(cover.h * 0.62)))
    return Math.max(14, Math.min(maxFs, Math.round(fs)))
  }
  // label
  let fs = Math.min(cover.h * 0.7, cover.w * 0.38)
  const innerW = Math.max(10, cover.w - 4)
  while (fs > 10 && estimateLineW(raw, fs) > innerW) fs -= 1
  const maxLab = Math.max(28, Math.min(56, Math.floor(cover.h * 0.85)))
  return Math.max(12, Math.min(maxLab, Math.round(fs)))
}

/** CJK dày → được cắt theo chữ; Latin/VI giữ nguyên từ (thu font, không Shaqi|n). */
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
      // Latin/VI: không bao giờ cắt giữa từ — caller thu font cho vừa
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

/** Title dọc: xếp đứng; font theo khung che lớn; căn giữa đều. */
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

  // Cover ≈ cột ink (CJK + Latin trái/đuôi)
  const leftPad = Math.max(18, Math.min(Math.round(seed.w * 0.5), Math.round(frameW * 0.04)))
  const botPad = Math.max(24, Math.min(Math.round(seed.h * 0.35), Math.round(frameH * 0.03)))
  const rightPad = Math.max(3, Math.round(seed.w * 0.08))
  let cover = clampBox(
    {
      x: seed.x - leftPad,
      y: seed.y - Math.max(2, Math.round(seed.h * 0.02)),
      w: seed.w + leftPad + rightPad,
      h: seed.h + botPad,
    },
    frameW,
    frameH,
  )

  // Mục tiêu font: ~78% chiều cao chia đều — khung cao → chữ to
  const targetFs =
    fontPxIn > 0
      ? Math.max(10, Math.min(120, Math.round(fontPxIn)))
      : Math.max(22, Math.min(72, Math.floor((cover.h * 0.78) / n)))

  // VI từ dài: nới ngang cột để đạt targetFs (không kẹt chữ bé vì cột OCR hẹp)
  if (!(fontPxIn > 0)) {
    const needW = Math.max(
      cover.w,
      ...units.map((u) => estimateLineW(u, targetFs) + Math.round(targetFs * 0.28)),
    )
    if (needW > cover.w) {
      const w = Math.min(frameW, Math.max(cover.w, Math.min(needW, Math.round(frameW * 0.18))))
      const cx = cover.x + cover.w / 2
      cover = clampBox({ ...cover, x: cx - w / 2, w }, frameW, frameH)
    }
  }

  let fontPx = fitOverlayFontPx('vertical', cover, text, fontPxIn)
  if (!(fontPxIn > 0)) {
    fontPx = Math.max(fontPx, Math.min(targetFs, fitOverlayFontPx('vertical', cover, text, 0)))
    const padX = Math.max(2, Math.round(fontPx * 0.1))
    const padY = Math.max(4, Math.round(fontPx * 0.12))
    const gap = Math.max(3, Math.round(fontPx * 0.14))
    while (
      fontPx > 16 &&
      (n * fontPx + gap * Math.max(0, n - 1) + padY * 2 > cover.h * 0.95 ||
        Math.max(...units.map((u) => estimateLineW(u, fontPx))) + padX * 2 > cover.w * 0.98)
    ) {
      fontPx -= 1
    }
  }

  // Caption ≈ full cover — chữ căn giữa trong khung
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

/** Mid: mọi chữ phải nằm trong cover — thu font tới khi wrap đủ; không cắt/không bỏ từ. */
export function layoutMidOverlay(
  coverIn: OcrCoverBox,
  text: string,
  fontPxIn: number,
  frameW: number,
  frameH: number,
): OcrOverlayLayout {
  const cover = clampBox(coverIn, frameW, frameH)
  const raw = text.trim() || ' '
  const LINE = 1.2
  // pad trong cover — chữ không sát rìa tím
  const padX = Math.max(4, Math.round(cover.w * 0.03))
  const padY = Math.max(6, Math.round(cover.h * 0.1))
  const innerW = Math.max(12, cover.w - padX * 2)
  const innerH = Math.max(12, cover.h - padY * 2)
  const norm = (s: string) => s.replace(/\s+/g, ' ').trim()
  const allWordsKept = (ls: string[]) => norm(ls.join(' ')) === norm(raw)

  // cho phép nhiều dòng — thu font đến khi vừa chiều cao (không bỏ chữ)
  const pack = (fs: number): string[] => {
    if (estimateLineW(raw, fs) <= innerW) return [raw]
    return wrapLines(raw, innerW, fs)
  }

  const fits = (fs: number, lines: string[]) => {
    if (!lines.length || !allWordsKept(lines)) return false
    if (lines.some((ln) => estimateLineW(ln, fs) > innerW + 2)) return false
    return lines.length * fs * LINE <= innerH
  }

  let fontPx =
    fontPxIn > 0
      ? Math.max(10, Math.min(120, Math.round(fontPxIn)))
      : Math.min(56, Math.floor(innerH / LINE))
  let lines = pack(fontPx)

  while (fontPx > 10 && !fits(fontPx, lines)) {
    fontPx -= 1
    lines = pack(fontPx)
  }
  // vẫn không vừa (cover cực thấp): buộc thu tới khi số dòng * line vừa H
  while (fontPx > 8 && lines.length * fontPx * LINE > innerH) {
    fontPx -= 1
    lines = pack(fontPx)
  }
  // 1 từ Latin/VI hoặc câu ngắn: luôn 1 dòng — thu font, không cắt giữa chữ
  const compactLen = raw.replace(/\s+/g, '').length
  const wordCount = raw.split(/\s+/).filter(Boolean).length
  if (lines.length > 1 && (wordCount === 1 || compactLen <= 14) && !isCjkHeavyToken(raw)) {
    for (let fs = Math.min(fontPx, Math.floor(innerH / LINE)); fs >= 8; fs -= 1) {
      if (estimateLineW(raw, fs) <= innerW && fs * LINE <= innerH) {
        fontPx = fs
        lines = [raw]
        break
      }
    }
    if (lines.length > 1) {
      // vẫn hẹp: giữ 1 dòng, font nhỏ nhất còn đọc được
      fontPx = Math.max(8, Math.min(fontPx, Math.floor(innerH / LINE)))
      while (fontPx > 8 && estimateLineW(raw, fontPx) > innerW) fontPx -= 1
      lines = [raw]
    }
  }

  // caption = vùng chữ trong cover (có pad) — dùng full bề ngang còn lại
  const caption = {
    x: cover.x + padX,
    y: cover.y + padY,
    w: Math.max(8, cover.w - padX * 2),
    h: Math.max(8, cover.h - padY * 2),
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

/** ponytail: self-check */
export function __checkOcrOverlayLayout() {
  const cover = { x: 100, y: 400, w: 160, h: 36 }
  const m = layoutMidOverlay(cover, 'Nấu sôi', 0, 1080, 1920)
  if (m.fontPx > 48 || m.cover.w > 180) throw new Error('mid must stay tight')
  const long = layoutMidOverlay(
    { x: 120, y: 900, w: 527, h: 119 },
    'Đổ đậu xanh giúp vỏ cam khô và co lại mà không bị biến dạng',
    0,
    1080,
    1920,
  )
  if (long.lines.length < 2) throw new Error('long mid must wrap 2+ lines')
  if (long.fontPx < 16) throw new Error('long mid font must stay readable')
  // caption inset — không sát rìa cover
  if (long.caption.y < long.cover.y + 6) throw new Error('mid caption must leave top inset')
  if (long.caption.y + long.caption.h > long.cover.y + long.cover.h - 6) {
    throw new Error('mid caption must leave bottom inset')
  }
  if (long.caption.w > long.cover.w - 4) throw new Error('mid caption must leave side inset')
  const short = layoutMidOverlay({ x: 196, y: 912, w: 136, h: 72 }, 'Đào hoa quả', 0, 1080, 1920)
  if (short.lines.length !== 1) throw new Error('short mid should stay 1 line')
  if (short.caption.y < short.cover.y + 6) throw new Error('short mid must leave top inset')
  // 1 từ Latin hẹp — không Shaqi|n
  const name = layoutMidOverlay({ x: 400, y: 900, w: 88, h: 64 }, 'Shaqin', 0, 1080, 1920)
  if (name.lines.length !== 1 || name.lines[0] !== 'Shaqin') {
    throw new Error('single Latin word must stay one line, got ' + JSON.stringify(name.lines))
  }
  const flush = layoutMidOverlay(
    { x: 80, y: 700, w: 480, h: 110 },
    'Bẻ nhỏ các sợi Pueraria lobata để dễ dàng rửa sạch Pueraria lobata',
    0,
    1080,
    1920,
  )
  if (flush.caption.y <= flush.cover.y + 4) throw new Error('3-line mid must not hug top edge')
  if (flush.caption.w < flush.cover.w * 0.88) throw new Error('mid caption must span nearly full width')
  const flushJoined = flush.lines.join(' ').replace(/\s+/g, ' ').trim()
  if (flushJoined !== 'Bẻ nhỏ các sợi Pueraria lobata để dễ dàng rửa sạch Pueraria lobata') {
    throw new Error('mid must keep all words — got ' + flushJoined)
  }
  if (flush.lines.length * flush.fontPx * 1.2 > flush.caption.h + 1) {
    throw new Error('mid text block must fit caption height')
  }
  const L = layoutLabelOverlay({ x: 50, y: 200, w: 90, h: 40 }, 'Đậu xanh', 0, 1080, 1920)
  if (L.cover.h !== 40 || L.fontPx > 48) throw new Error('label must not inflate cover')
  const v = layoutVerticalOverlay({ x: 40, y: 120, w: 48, h: 220 }, 'Cây màu tím', 0, 1080, 1920)
  if (v.lines.length < 3) throw new Error('vertical VI must stack words')
  if (v.fontPx < 28) throw new Error('vertical tall cover must use larger font')
  if (v.caption.h > v.cover.h + 2) throw new Error('vertical caption must fit cover height')
  if (v.caption.y < v.cover.y - 1 || v.caption.y + v.caption.h > v.cover.y + v.cover.h + 1) {
    throw new Error('vertical caption must sit inside cover')
  }
  const v2 = layoutVerticalOverlay({ x: 40, y: 100, w: 60, h: 400 }, 'Màu tím', 0, 1080, 1920)
  if (v2.fontPx < 40) throw new Error('very tall cover must get bigger type')
  const manual = layoutVerticalOverlay({ x: 40, y: 120, w: 28, h: 180 }, 'Hoa và màu tím', 42, 1080, 1920)
  if (manual.fontPx !== 42) throw new Error('manual font must stick')
  if (manual.cover.w < 28) throw new Error('manual vertical must widen cover for text')
}
