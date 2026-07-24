import type { ProjectSettings, Segment } from '@/features/project/project.types'
import {
  layoutOcrOverlay,
} from '@/features/editor/ocrOverlayLayout'
import { resolveCropRect, captionFontCss, type PixelBox, type CropRect } from './previewStyles'
import { isOcrOverlayLayout, effectiveOverlayLayout } from './segmentQuery'

export const AUTO_SUBTITLE_FONT = 48
/** Khớp burn._cover_max_h — đủ 1–3 dòng theo font */
export const COVER_MAX_H_FRAME_RATIO = 0.065

export const COVER_SHADOW_BOT = 4

export function coverPad(fontSizePx = AUTO_SUBTITLE_FONT, frameW = 1080) {
  return {
    x: Math.max(3, Math.round(frameW * 0.003)),
    // Chỉ chừa đủ viền/stroke; tránh chữ lọt thỏm giữa bbox.
    top: Math.max(2, Math.round(fontSizePx * 0.04)),
    // Match export: leave enough room for CJK descenders, outline, and shadow.
    bottom: Math.max(18, Math.round(fontSizePx * 0.55)),
  }
}

/** Căn giữa khối chữ trong cover (đúng giữa khung tím). */
export function captionCenterInCover(coverY: number, coverH: number, textBlockH: number) {
  return Math.round(coverY + Math.max(0, (coverH - textBlockH) / 2))
}

export const CAP_PAD_X = 1

export function coverInnerWidth(coverW: number, fontSizePx: number, frameW: number) {
  const pad = coverPad(fontSizePx, frameW)
  return Math.max(4, coverW - pad.x * 2 - CAP_PAD_X * 2)
}

export function frameMaxInnerWidth(fontSizePx: number, frameW: number) {
  // Full ngang video (trừ pad mép) — bbox được full width
  const maxCoverW = Math.max(12, frameW - 4)
  return coverInnerWidth(maxCoverW, fontSizePx, frameW)
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

export function coverBleedX(contentW: number, frameW = 1080) {
  // Bleed vừa đủ stroke CJK — không nới xa
  return Math.max(4, Math.round(contentW * 0.012), Math.round(frameW * 0.003))
}

/** Đo bề ngang mực chữ nguồn (CJK hardsub + outline) — không theo VI. */
export function measureSourceInkWidth(sourceText: string, fontSizePx: number, anchorH: number) {
  const trimmed = sourceText.trim()
  if (!trimmed) return 0
  // Hardsub on-screen thường to hơn font burn; ưu tiên H OCR
  const sourceFontPx = Math.max(Math.round(fontSizePx * 1.12), Math.round(anchorH * 0.92), 28)
  const raw = measureLineWidth(trimmed, sourceFontPx)
  const cjk = [...trimmed].filter((c) => c >= '\u4e00' && c <= '\u9fff').length
  // CJK hardsub ~1.15em/glyph + viền dày hai bên
  const cjkFloor = cjk > 0 ? Math.ceil(cjk * sourceFontPx * 1.15) : 0
  const outline = Math.ceil(sourceFontPx * 0.5)
  return Math.max(Math.ceil(raw * 1.2), cjkFloor) + outline
}

/**
 * Cover ngang (chuẩn):
 * 1) Che FULL chữ cũ (OCR seed + đo source + bleed)
 * 2) Fit chữ dịch nếu dài hơn
 * Caption frame nằm trong cover — không co cover theo VI.
 */
export function fitHardsubCover(
  seed: PixelBox,
  autoW: number,
  fontPx: number,
  frameW: number,
  frameH: number,
  sourceText: string,
): PixelBox {
  const pad = coverPad(fontPx, frameW)
  const srcInk = measureSourceInkWidth(sourceText, fontPx, Math.max(seed.h, fontPx))
  // (1) chữ cũ: seed OCR hoặc đo source — luôn tối thiểu đủ che full
  const oldW = Math.max(seed.w, srcInk > 0 ? coverBoxWidth(srcInk, frameW) : 0)
  // (2) chữ dịch chỉ nới thêm khi dài hơn chữ cũ
  const w = Math.min(frameW, Math.max(oldW, autoW))
  const cx = seed.x + seed.w / 2

  // Dọc: thu trống trên OCR (ít hơn — tránh kéo phụ đề xuống), nới đáy che stroke
  const topSlack = Math.round(seed.h * 0.14)
  const y = Math.max(0, seed.y + topSlack - pad.top)
  const botExtra = Math.max(pad.bottom, Math.round(seed.h * 0.15), Math.round(fontPx * 0.22))
  const bottom = seed.y + seed.h + botExtra
  const h = Math.max(12, Math.min(frameH - y, bottom - y))

  return clampCoverBox(
    {
      x: Math.round(Math.max(0, Math.min(frameW - w, cx - w / 2))),
      y: Math.round(y),
      w: Math.round(w),
      h: Math.round(h),
    },
    frameW,
    frameH,
  )
}

/** Chiều ngang ink chữ cũ: max(OCR anchor, đo source, cover đã lưu). */
export function resolveInkWidth(
  anchor: PixelBox,
  coverBox: PixelBox | null,
  hasSource: boolean,
  sourceW: number,
  frameW = 1080,
): number {
  let w = hasSource ? Math.max(sourceW, anchor.w) : anchor.w
  if (coverBox) {
    w = Math.max(w, coverBox.w - coverBleedX(coverBox.w, frameW) * 2)
  }
  return w
}

export function coverContentWidth(origW: number, transW: number) {
  return Math.max(origW, transW)
}

export function coverBoxWidth(contentW: number, frameW: number) {
  const bleed = coverBleedX(contentW, frameW)
  return Math.min(frameW, Math.ceil(contentW + bleed * 2))
}

export type OverLayout = { cover: PixelBox; caption: PixelBox; lines: string[]; fontPx?: number }

let _measureCtx: CanvasRenderingContext2D | null = null
let _measureFontFamily = 'system-ui, -apple-system, "Segoe UI", sans-serif'

/** ponytail: sync measurement font with the CSS render font */
export function setMeasureFontFamily(css: string) {
  if (css && css !== _measureFontFamily) {
    _measureFontFamily = css
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
      // ponytail: 1.05 safety — small margin for sub-pixel rounding
      return _measureCtx.measureText(text).width * 1.05
    }
  }
  return text.length * fontSizePx * 0.40
}

/** Xuống dòng — đổ ngang tối đa trước, rồi mới cân 2–3 dòng */
export function wrapCaptionText(text: string, maxInnerW: number, fontSizePx: number, maxLines = 3): string[] {
  const trimmed = text.trim()
  if (!trimmed) return ['']
  if (maxLines <= 1) return [trimmed]

  // ponytail: measureLineWidth already has 1.15x safety — no extra tolerance needed
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

/** Layout over: cover full ngang nếu cần; 1 dòng (co font) rồi mới 2 dòng. */
export function layoutOverMode(
  anchor: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  sourceText = '',
  inkW?: number,
): OverLayout {
  const pad = coverPad(fontSizePx, frameW)
  const cx = anchor.x + anchor.w / 2
  const trimmed = text.trim()
  // Full frame inner — xếp chữ Việt full ngang
  const maxInnerW = frameMaxInnerWidth(fontSizePx, frameW)
  const { lines, fontPx } = fitCaptionLines(trimmed, maxInnerW, fontSizePx, {
    preferOneLine: true,
    maxLines: 2,
  })

  const lineH = fontPx * 1.12
  const textBlockH = Math.ceil(lines.length * lineH + 4)
  const textW = Math.max(...lines.map((l) => measureLineWidth(l, fontPx)), 1)

  const sourceTrim = sourceText.trim()
  const sourceW = sourceTrim ? measureSourceInkWidth(sourceTrim, fontPx, anchor.h) : 0
  const origW = inkW ?? (sourceTrim ? Math.max(sourceW, anchor.w) : anchor.w)
  const contentW = coverContentWidth(origW, textW)
  const capPadX = 2
  const captionW = Math.ceil(textW + capPadX * 2)
  // Cover: max(OCR, chữ VI) — được full frameW
  const coverW = Math.min(frameW, Math.max(coverBoxWidth(contentW, frameW), captionW + pad.x * 2))
  const coverX = Math.round(Math.max(0, Math.min(frameW - coverW, cx - coverW / 2)))
  const coverY = Math.max(0, anchor.y - pad.top)
  const coverH = Math.min(
    frameH - coverY,
    Math.max(anchor.h, textBlockH) + pad.top + pad.bottom + COVER_SHADOW_BOT,
  )

  const captionX = Math.round(Math.max(0, Math.min(frameW - captionW, cx - captionW / 2)))
  const captionY = captionCenterInCover(coverY, coverH, textBlockH)

  return {
    cover: { x: Math.round(coverX), y: Math.round(coverY), w: Math.round(coverW), h: Math.round(coverH) },
    caption: { x: captionX, y: captionY, w: captionW, h: textBlockH },
    lines,
    fontPx,
  }
}

/** Thu bbox cũ bị kế thừa quá rộng; giữ nguyên tâm/Y/H của vùng OCR. */
export function tightenStoredBbox(
  seg: Pick<Segment, 'source' | 'bboxInherited'>,
  box: PixelBox,
  frameW: number,
): PixelBox {
  // Only an explicit false means the user dragged this box. Legacy OCR
  // payloads omitted bboxInherited, so null still follows conservative
  // horizontal tightening; Y/H remain exactly as located by the backend.
  if (seg.bboxInherited === false) return box
  const cjk = [...(seg.source ?? '')].filter((c) => c >= '\u4e00' && c <= '\u9fff').length
  if (cjk < 1) return box
  const glyphW = Math.max(18, box.h * 0.68)
  const expectedW = Math.max(box.h * 1.15, cjk * glyphW + 12)
  if (expectedW >= box.w * 0.94) return box
  // Tighten conservatively: at most 10%, and never below the estimated old text.
  const w = Math.max(48, Math.min(box.w, Math.round(Math.max(expectedW, box.w * 0.9))))
  const cx = box.x + box.w / 2
  const x = Math.max(0, Math.min(frameW - w, Math.round(cx - w / 2)))
  return { ...box, x, w }
}

/** Cover hiển thị / xuất — bbox lưu trực tiếp khung này (mode over). */
export function resolveSegmentCover(
  seg: Segment | undefined,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
): PixelBox | null {
  if (!seg) return null
  const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)
  const over = settings.coverHardsubs && settings.burnSubs && seg.translation.trim()
  if (isOcrOverlayLayout(seg.layout)) {
    return overlayCoverSeed(seg, frameW, frameH)
  }
  if (!over) {
    const seed = seg.bbox
      ? tightenStoredBbox(seg, clampCoverBox(seg.bbox, frameW, frameH), frameW)
      : seedCoverBox(seg, frameW, frameH, fontPx)
    if (!seed) return null
    return normalizeCoverBox(seed, frameW, frameH, fontPx)
  }
  if (seg.bbox) {
    return tightenStoredBbox(seg, clampCoverBox(seg.bbox, frameW, frameH), frameW)
  }
  const seed = seedCoverBox(seg, frameW, frameH, fontPx)
  if (!seed) return null
  const anchor = normalizeCoverBox(seed, frameW, frameH, fontPx)
  return fitCoverBoxOver(anchor, seg.translation, fontPx, frameW, frameH, seg.source ?? '')
}

/** Seed khung che overlay: chỉ fallback đúng layout; không bbox CJK → null (đừng bịa cột dọc). */
export function overlayCoverSeed(seg: Segment, frameW: number, frameH: number): PixelBox | null {
  if (!seg.bbox) {
    return null
  }
  const box = clampCoverBox(seg.bbox, frameW, frameH)
  // mid: chỉ bỏ khung gần full-frame (lưới đáy nhầm). 2 dòng hardsub giữa/đáy vẫn giữ.
  return box
}

export function isBadOverlayStoredCover(seg: Segment, cover: PixelBox, _frameW = 1080, frameH = 1920): boolean {
  if (seg.layout === 'vertical' && cover.w > cover.h * 0.85) return true
  // Caption đáy full ngang OK; mid/label chỉ chặn H bất thường
  if (seg.layout === 'mid' && cover.h > frameH * 0.28) return true
  if (seg.layout === 'label' && cover.h > frameH * 0.35) return true
  return false
}

export function toCaptionLayout(caption: PixelBox, lines: string[], fontSize: number): NonNullable<Segment['captionLayout']> {
  return { x: caption.x, y: caption.y, w: caption.w, h: caption.h, lines, fontSize }
}

/** User đã kéo tay / lưu layout — giữ nguyên bbox (không adaptive reset). */
export function hasStoredLayout(seg: Segment | undefined, fontPx?: number): boolean {
  const cl = seg?.captionLayout
  const b = seg?.bbox
  if (!(b && cl?.lines?.length && cl.w > 0 && cl.h > 0)) return false
  if (fontPx != null && fontPx > 0 && cl.fontSize > 0 && fontPx !== cl.fontSize) return false
  return true
}

/** Đọc đúng bbox + captionLayout đã lưu — không tính lại (preview = xuất). */
export function storedOverLayout(seg: Segment, frameW: number, frameH: number): OverLayout | null {
  const cl = seg.captionLayout
  const b = seg.bbox
  if (!b || !cl?.lines?.length || cl.w <= 0 || cl.h <= 0) return null
  return {
    cover: clampCoverBox(b, frameW, frameH),
    caption: {
      x: Math.round(cl.x),
      y: Math.round(cl.y),
      w: Math.max(1, Math.round(cl.w)),
      h: Math.max(1, Math.round(cl.h)),
    },
    lines: cl.lines.map(String),
  }
}

/** Chỉ gọi khi chưa có layout lưu hoặc user vừa chỉnh cover/chữ. */
export function resolveOverLayout(
  seg: Segment | undefined,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  coverOverride?: PixelBox,
): OverLayout | null {
  if (!seg?.translation.trim()) return null
  if (!settings.burnSubs) return null
  // ponytail: sync measurement font with CSS render font — prevents text overflow
  setMeasureFontFamily(captionFontCss(settings.subtitleFontFamily || 'system'))
  const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)

  // Overlay OCR mid / dọc / nhãn — hoặc horizontal có bbox giữa khung
  // (không phụ thuộc coverHardsubs: chữ vẫn đúng chỗ; mask mới cần cover)
  const overlayLay = effectiveOverlayLayout(seg, frameH)
  if (overlayLay) {
    const preferred = resolveOverlayFontPreferred(seg)
    if (coverOverride) {
      // Kéo tay: fit theo khung draft (preferred=0 trừ khi user khóa fontSize trên đoạn)
      // Không khóa captionLayout.fontSize cũ — không thì thả chuột chữ tụt bé lại.
      const lockFs = resolveOverlayFontPreferred(seg)
      const laid = layoutOcrOverlay(overlayLay, coverOverride, seg.translation, lockFs, frameW, frameH, false)
      return {
        cover: clampCoverBox(coverOverride, frameW, frameH),
        caption: laid.caption,
        lines: laid.lines,
        fontPx: laid.fontPx,
      }
    }
    // Persisted captionLayout is authoritative only after an explicit drag.
    // Auto layouts from older cache versions must be recomputed on reopen.
    if (seg.bboxInherited === false && hasStoredLayout(seg, undefined)) {
      const stored = storedOverLayout(seg, frameW, frameH)
      if (stored && !isBadOverlayStoredCover(seg, stored.cover, frameW, frameH)) {
        // Tin bbox/mask đã lưu; chữ xếp lại trong cover (tránh captionLayout x/y lệch → chữ sai chỗ)
        const cover = stored.cover
        // captionLayout.fontSize là kết quả auto cũ, không phải lựa chọn khóa
        // của người dùng. Bỏ nó để bbox dài tự tính lại font lớn nhất có thể.
        // 0 = auto fit bbox; preferred chỉ khi user set fontSize đoạn
        const want = preferred > 0 ? preferred : 0
        const laid = layoutOcrOverlay(overlayLay, cover, seg.translation, want, frameW, frameH)
        return {
          cover: clampCoverBox(laid.cover, frameW, frameH),
          caption: laid.caption,
          lines: laid.lines,
          fontPx: laid.fontPx,
        }
      }
    }
    // mid/dọc/nhãn: cover = bbox OCR (không nới theo VI); chưa OCR → không bịa khung
    const seed = overlayCoverSeed(seg, frameW, frameH)
    if (!seed) return null
    // Mid captions use the shared caption font first; bbox fitting may shrink
    // only when that size cannot fit. Vertical/label keep their own auto-fit.
    const want = preferred > 0
      ? preferred
      : overlayLay === 'mid'
        ? fontPx
        : 0
    const laid = layoutOcrOverlay(overlayLay, seed, seg.translation, want, frameW, frameH)
    // CAP-MID/mid: cover tu layoutMidOverlay (trong seed OCR) — khong doi caption day
    return {
      cover: clampCoverBox(laid.cover, frameW, frameH),
      caption: laid.caption,
      lines: laid.lines,
      fontPx: laid.fontPx,
    }
  }

  // Caption đáy/over horizontal — cần chế độ che chữ
  if (!(settings.coverHardsubs && settings.burnSubs)) return null

  // Đang kéo: bám đúng draft (user chỉnh tay)
  if (coverOverride) {
    const dragFont = Math.max(
      10,
      Math.floor(autoFontFromBbox(coverOverride, seg.translation, fontPx) * 0.86),
    )
    return manualCoverLayout(coverOverride, seg.translation, dragFont, frameW, frameH, true, false)
  }

  // Đã lưu từ editor (kéo tay) — giữ đúng bbox; chỉ xếp chữ trong cover (như mid)
  // A dragged bbox stores the fitted font, which is usually smaller than the
  // project default. Do not reject that layout merely because the default
  // font changed; doing so re-runs the path with 48px and overflows the box.
  if (seg.bboxInherited === false && hasStoredLayout(seg, undefined)) {
    const stored = storedOverLayout(seg, frameW, frameH)
    if (!stored) return null
    // The editor already fit this exact bbox before commit. Re-fitting here
    // changes wrapping/font after mouse-up, so the released preview differs
    // from the live drag preview. Translation edits clear captionLayout first.
    return {
      ...stored,
      fontPx: Number(seg.captionLayout?.fontSize) > 0
        ? Number(seg.captionLayout?.fontSize)
        : fontPx,
    }
  }

  const seedRaw = seg.bbox
    ? tightenStoredBbox(seg, clampCoverBox(seg.bbox, frameW, frameH), frameW)
    : seedCoverBox(seg, frameW, frameH, fontPx)
      // Whisper can provide a translated horizontal caption without an OCR bbox.
      // In cover mode, use the same bottom fallback shown by the editor handles so
      // the mask and translated text are rendered instead of silently disappearing.
      ?? fallbackCoverBox(frameW, frameH, fontPx)
  // Bbox OCR is the coverage contract: never crop an edge after detection.
  const seed = normalizeCoverBox(seedRaw, frameW, frameH, fontPx)

  // Bbox OCR / user: cover cố định như mid — fit chữ trong box, không phình sau drag
  const anchor = coverToAnchor(seed, fontPx, frameW)
  if (seg.bbox) {
    if (seg.bboxInherited === false) {
      const fixedFont = Number(seg.captionLayout?.fontSize) > 0
        ? Number(seg.captionLayout?.fontSize)
        : autoFontFromBbox(seed, seg.translation, 0)
      const laid = manualCoverLayout(seed, seg.translation, fixedFont, frameW, frameH, true)
      return { ...laid, fontPx: laid.fontPx ?? fixedFont }
    }
    // Auto OCR boxes are tight around source glyphs; leave room for the
    // translated glyph ascenders/descenders and shadow before first drag.
    // ponytail: keep this margin only on inherited OCR, while user-dragged
    // layouts retain their exact stored fit.
    const autoFontPx = fontPx
    const laid = manualCoverLayout(seed, seg.translation, autoFontPx, frameW, frameH, true)
    return { ...laid, fontPx: autoFontPx }
  }

  const sourceTrim = (seg.source ?? '').trim()
  const sourceW = sourceTrim ? measureSourceInkWidth(sourceTrim, fontPx, anchor.h) : 0
  const inkW = resolveInkWidth(anchor, seed, !!sourceTrim, sourceW, frameW)
  const auto = layoutOverMode(anchor, seg.translation, fontPx, frameW, frameH, seg.source ?? '', inkW)
  const cover = fitHardsubCover(seed, auto.cover.w, fontPx, frameW, frameH, seg.source ?? '')
  const laid = layoutCaptionInCover(cover, seg.translation, fontPx, frameW)
  return { cover, ...laid, fontPx }
}

export function cropCoversFull(crop: CropRect, frameW: number, frameH: number): boolean {
  return crop.x <= 1 && crop.y <= 1 && crop.w >= frameW - 2 && crop.h >= frameH - 2
}

export function intersectBox(a: PixelBox, crop: CropRect): PixelBox | null {
  const x = Math.max(a.x, crop.x)
  const y = Math.max(a.y, crop.y)
  const x2 = Math.min(a.x + a.w, crop.x + crop.w)
  const y2 = Math.min(a.y + a.h, crop.y + crop.h)
  if (x2 - x < 4 || y2 - y < 4) return null
  return { x: Math.round(x), y: Math.round(y), w: Math.round(x2 - x), h: Math.round(y2 - y) }
}

function fitBoxToCrop(box: PixelBox, crop: CropRect): PixelBox {
  const scale = Math.min(1, crop.w / Math.max(1, box.w), crop.h / Math.max(1, box.h))
  const w = Math.max(4, Math.round(box.w * scale))
  const h = Math.max(4, Math.round(box.h * scale))
  const centerX = box.x + box.w / 2
  const centerY = box.y + box.h / 2
  return {
    x: Math.round(Math.max(crop.x, Math.min(crop.x + crop.w - w, centerX - w / 2))),
    y: Math.round(Math.max(crop.y, Math.min(crop.y + crop.h - h, centerY - h / 2))),
    w,
    h,
  }
}

export function unionBox(a: PixelBox, b: PixelBox): PixelBox {
  const x = Math.min(a.x, b.x)
  const y = Math.min(a.y, b.y)
  const x2 = Math.max(a.x + a.w, b.x + b.w)
  const y2 = Math.max(a.y + a.h, b.y + b.h)
  return { x: Math.round(x), y: Math.round(y), w: Math.round(x2 - x), h: Math.round(y2 - y) }
}

export type PreviewOverLayout = OverLayout & { mask: PixelBox }

/** Mask che chữ gốc — không cần bản dịch (mid/label/dọc OCR). */
export function resolveCoverMaskOnly(
  seg: Segment,
  frameW: number,
  frameH: number,
  crop: CropRect,
  coverOverride?: PixelBox,
): PixelBox | null {
  const seed = coverOverride ?? overlayCoverSeed(seg, frameW, frameH)
  if (!seed) return null
  const cover = clampCoverBox(seed, frameW, frameH)
  if (cropCoversFull(crop, frameW, frameH)) return cover
  const ink = intersectBox(cover, crop) ?? intersectBox(
    seg.bbox ? clampCoverBox(seg.bbox, frameW, frameH) : cover,
    crop,
  )
  return ink
}

/** Preview: cover/caption luôn nằm trong crop hiện tại (16:9, 9:16…). */
export function resolvePreviewOverLayout(
  seg: Segment | undefined,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  crop: CropRect,
  coverOverride?: PixelBox,
): PreviewOverLayout | null {
  const base = resolveOverLayout(seg, settings, frameW, frameH, coverOverride)
  if (!base) return null
  // Nếu segment có bbox (OCR hoặc user kéo) HOẶC thuộc layout mid/label/vertical
  // -> GIỮ NGUYÊN tọa độ đè đúng chỗ. Không tự động shift/fallback xuống đáy màn hình.
  const overlayLay = seg ? effectiveOverlayLayout(seg, frameH) : null
  if (
    overlayLay === 'mid' ||
    overlayLay === 'label' ||
    overlayLay === 'vertical' ||
    seg?.bbox
  ) {
    const fullCrop = cropCoversFull(crop, frameW, frameH)
    const cover = fullCrop ? base.cover : fitBoxToCrop(base.cover, crop)
    const caption = fullCrop ? base.caption : fitBoxToCrop(base.caption, crop)
    return { ...base, cover, caption, mask: base.cover }
  }

  // Dưới đây là logic dành cho Whisper (dịch giọng nói, KHÔNG CÓ BBOX)
  // -> tự động fallback căn lề dưới cùng của vùng video (crop).
  // Caption đáy 16:9 — logic HEAD gốc (không sửa mid)
  if (crop.w >= crop.h) {
    const fontPx = base.fontPx ?? 16
    const padY = Math.max(2, Math.round(fontPx * 0.08))
    const offsetY = Math.max(3, Math.round(fontPx * 0.25))
    const caption = {
      ...base.caption,
      y: Math.min(crop.y + crop.h - base.caption.h - padY, base.caption.y + offsetY),
    }
    const y = Math.max(crop.y, caption.y - padY)
    const bottom = Math.min(crop.y + crop.h, caption.y + caption.h + padY)
    const cover = { ...base.cover, y, h: Math.max(4, bottom - y) }
    return { ...base, cover, caption, mask: cover }
  }
  const fullCrop = cropCoversFull(crop, frameW, frameH)
  const fittedCover = fullCrop ? base.cover : fitBoxToCrop(base.cover, crop)
  let caption = fullCrop ? base.caption : fitBoxToCrop(base.caption, crop)
  const text = seg?.translation?.trim() || base.lines.join(' ')
  const preferredFont = resolveCaptionFontSize(seg, settings, frameW, frameH)
  const maxLines = 2
  let fontPx = preferredFont
  let lines = wrapCaptionText(text, caption.w * 0.9, fontPx, maxLines)
  while (
    fontPx > 10
    && lines.some((line) => measureLineWidth(line, fontPx) > caption.w * 0.98)
  ) {
    fontPx -= 1
    lines = wrapCaptionText(text, caption.w * 0.9, fontPx, maxLines)
  }
  const neededHeight = Math.ceil(fontPx * lines.length * 1.25 + 8)
  if (caption.h < neededHeight) {
    caption = fitBoxToCrop({
      ...caption,
      y: caption.y + (caption.h - neededHeight) / 2,
      h: neededHeight,
    }, crop)
  }
  const offsetY = Math.max(2, Math.round(fontPx * 0.06))
  const groupBottom = Math.max(
    fittedCover.y + fittedCover.h,
    caption.y + caption.h,
  )
  const shiftY = Math.max(0, Math.min(offsetY, crop.y + crop.h - groupBottom))
  const shiftedCover = { ...fittedCover, y: fittedCover.y + shiftY }
  caption = { ...caption, y: caption.y + shiftY }
  const union = unionBox(shiftedCover, caption)
  const maskSeed = unionBox(fittedCover, caption)
  const maskBottom = Math.min(
    crop.y + crop.h,
    maskSeed.y + maskSeed.h + Math.round(fontPx * 0.65),
  )
  const mask = intersectBox({
    ...maskSeed,
    h: maskBottom - maskSeed.y,
  }, crop) ?? maskSeed
  const padY = Math.max(2, Math.round(fontPx * 0.08))
  const coverY = Math.max(crop.y, caption.y - padY)
  const coverBottom = Math.min(crop.y + crop.h, caption.y + caption.h + padY)
  const cover = fitBoxToCrop({
    x: union.x,
    y: coverY,
    w: union.w,
    h: Math.max(4, coverBottom - coverY),
  }, crop)
  return {
    ...base,
    cover,
    caption,
    lines,
    fontPx,
    mask,
  }
}

export function estimatePreviewCaptionBox(
  ocr: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  crop: CropRect,
  placement: 'over' | 'below' | 'above',
): PixelBox {
  if (cropCoversFull(crop, frameW, frameH)) {
    return estimateCaptionBox(ocr, text, fontSizePx, frameW, frameH, placement)
  }
  const localOcr = {
    x: Math.max(0, ocr.x - crop.x),
    y: Math.max(0, ocr.y - crop.y),
    w: Math.min(ocr.w, crop.w),
    h: Math.min(ocr.h, crop.h),
  }
  const box = estimateCaptionBox(localOcr, text, fontSizePx, crop.w, crop.h, placement)
  return { x: box.x + crop.x, y: box.y + crop.y, w: box.w, h: box.h }
}

export function segmentWithLayout(seg: Segment, layout: OverLayout, fontPx: number): Segment {
  return {
    ...seg,
    bbox: { x: layout.cover.x, y: layout.cover.y, w: layout.cover.w, h: layout.cover.h },
    captionLayout: toCaptionLayout(layout.caption, layout.lines, layout.fontPx ?? fontPx),
  }
}

/**
 * below/above (không cover): bake đúng khung chữ preview (`estimateCaptionBox`).
 * Không dùng resolveOverLayout — hàm đó chỉ trả layout khi cover / OCR overlay.
 */
export function resolveBelowAboveLayout(
  seg: Segment,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  crop: CropRect,
  placement: 'below' | 'above',
): OverLayout | null {
  if (!seg.translation.trim()) return null
  const preferred = resolveCaptionFontSize(seg, settings, frameW, frameH)
  const ocr =
    (seg.bbox ? clampCoverBox(seg.bbox, frameW, frameH) : null)
    ?? seedCoverBox(seg, frameW, frameH, preferred)
    ?? fallbackCoverBox(frameW, frameH, preferred)
  const fitFrameW = cropCoversFull(crop, frameW, frameH) ? frameW : crop.w
  const { lines, fontPx } = fitOutsideCaption(ocr, seg.translation, preferred, fitFrameW)
  const caption = estimatePreviewCaptionBox(ocr, seg.translation, fontPx, frameW, frameH, crop, placement)
  return { cover: ocr, caption, lines, fontPx }
}

/** Caption ngoài bbox: co font để giữ một dòng trước, rồi mới cho xuống tối đa hai dòng. */
export function fitOutsideCaption(
  _ocr: PixelBox,
  text: string,
  preferred: number,
  frameW: number,
) {
  // Bottom-lane captions share the project/segment font. Do not derive size
  // from each OCR box: varying source glyph heights made adjacent cues jump.
  const baseFont = Math.max(12, Math.round(preferred || AUTO_SUBTITLE_FONT))
  const maxInnerW = Math.max(24, Math.round(frameW * 0.92))
  let fontPx = baseFont
  // Keep the shared font for up to three lines. Shrinking a two-line caption
  // to 12px is what made wide portrait captions unreadably tiny.
  let lines = wrapCaptionText(text, maxInnerW, fontPx, 3)
  // First choice: one horizontal line at the shared font. Otherwise keep the
  // same font and wrap; shrink only as a last resort when a 3-line unit still
  // exceeds the video width.
  while (
    fontPx > 12
    && lines.some((line) => measureLineWidth(line, fontPx) > maxInnerW)
  ) {
    fontPx -= 1
    lines = wrapCaptionText(text, maxInnerW, fontPx, 3)
  }
  return { lines, fontPx }
}

/** Bake đúng layout đang hiện ở preview vào segment — Xuất bản khóa WYSIWYG. */
export function buildExportSegments(
  segments: Segment[],
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
): Segment[] {
  if (!settings.burnSubs || frameW <= 0) return segments
  const place = captionPlacement(settings)
  const crop = resolveCropRect(frameW, frameH, settings.previewAspectRatio ?? 'original', settings.previewCrop)
  return segments.map((seg) => {
    if (!seg.translation.trim()) return seg
    const layout = resolvePreviewOverLayout(seg, settings, frameW, frameH, crop)
    if (layout) {
      const fontPx = layout.fontPx ?? resolveCaptionFontSize(seg, settings, frameW, frameH)
      return segmentWithLayout(seg, layout, fontPx)
    }
    // Chèn dưới/trên: bake mid + horizontal (không dọc/nhãn) — khớp preview emerald box
    if (
      (place === 'below' || place === 'above')
      && seg.layout !== 'vertical'
      && seg.layout !== 'label'
    ) {
      const baked = resolveBelowAboveLayout(seg, settings, frameW, frameH, crop, place)
      if (baked) {
        return segmentWithLayout(seg, baked, baked.fontPx ?? resolveCaptionFontSize(seg, settings, frameW, frameH))
      }
    }
    return seg
  })
}

export function isCjkHardsubSource(src: string | undefined): boolean {
  let cjk = 0
  for (const c of src ?? '') {
    if (c >= '\u4e00' && c <= '\u9fff') cjk += 1
  }
  return cjk >= 2
}

/** Anchor OCR suy từ cover — chỉ để căn caption */
export function coverToAnchor(cover: PixelBox, fontSizePx: number, frameW = 1080): PixelBox {
  const pad = coverPad(fontSizePx, frameW)
  return {
    x: Math.round(cover.x + pad.x),
    y: Math.round(cover.y + pad.top),
    w: Math.max(12, Math.round(cover.w - pad.x * 2)),
    h: Math.max(12, Math.round(cover.h - pad.top - pad.bottom)),
  }
}

export function coverMaxHeight(frameH: number, fontSizePx = AUTO_SUBTITLE_FONT) {
  const one = Math.round(fontSizePx * 1.45 + 10)
  const cap = Math.round(fontSizePx * 3.4 + 16)
  const byFrame = Math.round(frameH * COVER_MAX_H_FRAME_RATIO)
  return Math.max(one, Math.min(cap, byFrame))
}

/** Giữ chiều cao OCR; ngang được full frame (không cắt 85%). */
export function normalizeCoverBox(box: PixelBox, frameW: number, frameH: number, _fontSizePx = AUTO_SUBTITLE_FONT): PixelBox {
  let { x, y, w, h } = box
  const sanityMaxH = Math.round(frameH * 0.15)
  // Ngang: full video — chỉ kẹp frameW
  const sanityMaxW = frameW
  if (h > sanityMaxH) {
    const cy = y + h / 2
    h = sanityMaxH
    y = Math.round(Math.max(0, Math.min(frameH - h, cy - h / 2)))
  }
  if (w > sanityMaxW) {
    const cx = x + w / 2
    w = sanityMaxW
    x = Math.round(Math.max(0, Math.min(frameW - w, cx - w / 2)))
  }
  x = Math.max(0, Math.min(x, frameW - 12))
  y = Math.max(0, Math.min(y, frameH - 12))
  w = Math.max(12, Math.min(w, frameW - x))
  h = Math.max(12, Math.min(h, frameH - y))
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}

/** Kéo tay: chỉ kẹp trong khung video, không cắt theo % sanity. */
export function clampCoverBox(box: PixelBox, frameW: number, frameH: number, minSize = 12): PixelBox {
  let { x, y, w, h } = box
  x = Math.max(0, Math.min(x, frameW - minSize))
  y = Math.max(0, Math.min(y, frameH - minSize))
  w = Math.max(minSize, Math.min(w, frameW - x))
  h = Math.max(minSize, Math.min(h, frameH - y))
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}

/** Caption trong cover: 1 dòng (co font) → 2 dòng; căn giữa. */
export function layoutCaptionInCover(
  cover: PixelBox,
  text: string,
  fontSizePx: number,
  _frameW: number,
): Pick<OverLayout, 'caption' | 'lines'> & { fontPx?: number } {
  const trimmed = text.trim()
  const edge = Math.max(2, Math.round(cover.w * 0.02))
  const maxInnerW = Math.max(4, cover.w - edge * 2)
  const sharedOneLineFits =
    measureLineWidth(trimmed, fontSizePx) <= maxInnerW
    && Math.ceil(fontSizePx * 1.12 + 4) <= cover.h
  // Check 1-line font size
  const fit1 = fitCaptionLines(trimmed, maxInnerW, fontSizePx, {
    minFont: 1,
    preferOneLine: true,
    maxLines: 1,
  })
  const font1 = fit1.fontPx

  // Check 2-line font size (don't force one line)
  const fit2 = fitCaptionLines(trimmed, maxInnerW, fontSizePx, {
    minFont: 1,
    preferOneLine: false,
    maxLines: 2,
  })
  // However, fitCaptionLines doesn't check cover.h!
  // We need to ensure that the 2-line layout fits within cover.h.
  let font2 = fit2.fontPx
  let lines2 = fit2.lines
  while (font2 > 8 && lines2.length > 1 && Math.ceil(lines2.length * font2 * 1.12 + 4) > cover.h) {
    font2 -= 1
    const refit = fitCaptionLines(trimmed, maxInnerW, font2, { preferOneLine: false, maxLines: 2, minFont: 1 })
    font2 = refit.fontPx
    lines2 = refit.lines
  }
  if (Math.ceil(lines2.length * font2 * 1.12 + 4) > cover.h) {
    font2 = 0 // Does not fit vertically
  }

  let fontPx: number
  let lines: string[]
  if (sharedOneLineFits) {
    // First priority for auto mid/horizontal: one line at the shared font.
    fontPx = fontSizePx
    lines = [trimmed]
  } else if (font2 > font1 && lines2.length > 1) {
    fontPx = font2
    lines = lines2
  } else {
    // If 2-lines didn't fit, we fallback to 1 line.
    // The previous fit1 might have aborted early due to preferOneLine's 20% limit.
    // We force it to shrink all the way down to minFont to guarantee it fits.
    const forcedFit = fitCaptionLines(trimmed, maxInnerW, fontSizePx, { minFont: 1, preferOneLine: false, maxLines: 1 })
    fontPx = forcedFit.fontPx
    lines = forcedFit.lines
  }

  const lineH = fontPx * 1.12
  const textBlockH = Math.ceil(lines.length * lineH + 4)
  const textW = Math.max(...lines.map((l) => measureLineWidth(l, fontPx)), 1)
  const captionW = Math.ceil(
    lines.length === 1
      ? Math.max(textW + CAP_PAD_X * 2, cover.w - edge * 2)
      : textW + CAP_PAD_X * 2,
  )
  const cx = cover.x + cover.w / 2
  const captionX = Math.round(Math.max(cover.x, Math.min(cover.x + cover.w - captionW, cx - captionW / 2)))
  const captionY = captionCenterInCover(cover.y, cover.h, textBlockH)
  return {
    caption: { x: captionX, y: captionY, w: captionW, h: textBlockH },
    lines,
    fontPx: fontPx !== fontSizePx ? fontPx : undefined,
  }
}

/** Tự co/giãn cover: full ngang được; 1 dòng (co font) rồi 2 dòng. */
export function adaptiveCoverLayout(
  cover: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
): OverLayout {
  const pad = coverPad(fontSizePx, frameW)
  const cx = cover.x + cover.w / 2
  const topY = cover.y
  const trimmed = text.trim()
  const maxInnerW = frameMaxInnerWidth(fontSizePx, frameW)
  let { lines, fontPx } = fitCaptionLines(trimmed, maxInnerW, fontSizePx, {
    preferOneLine: true,
    maxLines: 2,
  })

  const sizeFromLines = (ls: string[], fs: number) => {
    const lineH = fs * 1.12
    const textBlockH = Math.ceil(ls.length * lineH + 4)
    const textW = Math.max(...ls.map((l) => measureLineWidth(l, fs)), 1)
    const captionW = Math.ceil(textW + CAP_PAD_X * 2)
    const coverW = Math.min(frameW, Math.max(cover.w, captionW + pad.x * 2))
    const byText = textBlockH + pad.top + pad.bottom + COVER_SHADOW_BOT
    const coverH = Math.min(frameH, Math.max(cover.h, byText))
    return { lineH, textBlockH, textW, captionW, coverW, coverH }
  }

  let { textBlockH, captionW, coverW, coverH } = sizeFromLines(lines, fontPx)
  let coverX = Math.round(Math.max(0, Math.min(frameW - coverW, cx - coverW / 2)))
  let coverY = Math.round(Math.max(0, Math.min(frameH - coverH, topY)))
  let box = clampCoverBox({ x: coverX, y: coverY, w: coverW, h: coverH }, frameW, frameH)

  const inner = coverInnerWidth(box.w, fontPx, frameW)
  const refit = fitCaptionLines(trimmed, inner, fontPx, { preferOneLine: true, maxLines: 2 })
  if (refit.lines.join('\n') !== lines.join('\n') || refit.fontPx !== fontPx) {
    lines = refit.lines
    fontPx = refit.fontPx
    const sized = sizeFromLines(lines, fontPx)
    textBlockH = sized.textBlockH
    captionW = sized.captionW
    coverW = sized.coverW
    coverH = sized.coverH
    coverX = Math.round(Math.max(0, Math.min(frameW - coverW, cx - coverW / 2)))
    coverY = Math.round(Math.max(0, Math.min(frameH - coverH, topY)))
    box = clampCoverBox({ x: coverX, y: coverY, w: coverW, h: coverH }, frameW, frameH)
  }

  const capX = Math.round(Math.max(box.x, Math.min(box.x + box.w - captionW, box.x + box.w / 2 - captionW / 2)))
  const capY = captionCenterInCover(box.y, box.h, textBlockH)
  return {
    cover: box,
    caption: { x: capX, y: capY, w: Math.min(captionW, box.w), h: textBlockH },
    lines,
    fontPx,
  }
}

export function manualCoverLayout(
  cover: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  fixed = false,
  allowExpand = true,
): OverLayout {
  if (fixed) {
    let box = clampCoverBox(cover, frameW, frameH)
    // Automatic horizontal captions keep the shared font: expand to one line
    // first, otherwise to a two-line block. Shrink happens only after the
    // shared block has exhausted the frame.
    const sharedLines = wrapCaptionText(
      text.trim(),
      frameMaxInnerWidth(fontSizePx, frameW),
      fontSizePx,
      2,
    )
    const sharedNeedW = Math.ceil(
      Math.max(...sharedLines.map((line) => measureLineWidth(line, fontSizePx)), 1) / 0.96
      + coverPad(fontSizePx, frameW).x * 2,
    )
    const sharedNeedH = Math.ceil(sharedLines.length * fontSizePx * 1.12 + 4)
    if (
      allowExpand
      && text.trim()
      && sharedLines.every((line) => measureLineWidth(line, fontSizePx) <= frameMaxInnerWidth(fontSizePx, frameW))
      && (sharedNeedW > box.w || sharedNeedH > box.h)
    ) {
      const cx = box.x + box.w / 2
      const cy = box.y + box.h / 2
      const w = Math.min(frameW, Math.max(box.w, sharedNeedW))
      const h = Math.min(frameH, Math.max(box.h, sharedNeedH))
      box = clampCoverBox({ x: Math.round(cx - w / 2), y: Math.round(cy - h / 2), w, h }, frameW, frameH)
    }
    const laid = layoutCaptionInCover(box, text, fontSizePx, frameW)
    
    if (allowExpand && laid.caption.w > box.w) {
      const cx = box.x + box.w / 2
      let newW = Math.min(frameW, laid.caption.w)
      let newX = Math.round(Math.max(0, Math.min(frameW - newW, cx - newW / 2)))
      const expandedBox = clampCoverBox({ ...box, x: newX, w: newW }, frameW, frameH)
      const laid2 = layoutCaptionInCover(expandedBox, text, fontSizePx, frameW)
      return {
        cover: expandedBox,
        caption: laid2.caption,
        lines: laid2.lines,
        fontPx: laid2.fontPx ?? fontSizePx,
      }
    }
    
    return {
      cover: box,
      caption: laid.caption,
      lines: laid.lines,
      fontPx: laid.fontPx ?? fontSizePx,
    }
  }
  return adaptiveCoverLayout(cover, text, fontSizePx, frameW, frameH)
}

/** Cover mặc định phụ đề đáy — chỉ khi không phải CJK chờ OCR. */

/** Fit chu trong bbox co dinh — keo/tha caption ngang (khong lien quan mid). */
export function fitFixedCoverCaption(
  cover: PixelBox,
  text: string,
  frameW: number,
  frameH: number,
): OverLayout {
  const startFs = autoFontFromBbox(cover, text, 0)
  return manualCoverLayout(cover, text, startFs, frameW, frameH, true)
}

export function fallbackCoverBox(frameW: number, frameH: number, fontSizePx = AUTO_SUBTITLE_FONT): PixelBox {
  const h = coverMaxHeight(frameH, fontSizePx)
  const w = Math.round(frameW * 0.4)
  return {
    x: Math.round((frameW - w) / 2),
    y: Math.round(frameH - h - Math.round(frameH * 0.06)),
    w,
    h,
  }
}

/**
 * Seed cover: bbox OCR nếu có.
 * CJK chưa bbox → null (không đoán giữa/đáy — video khác nhau vị trí khác nhau).
 */
export function seedCoverBox(
  seg: Pick<Segment, 'source' | 'bbox' | 'layout'> | undefined,
  frameW: number,
  frameH: number,
  fontSizePx = AUTO_SUBTITLE_FONT,
): PixelBox | null {
  if (seg?.bbox) {
    return clampCoverBox(seg.bbox, frameW, frameH)
  }
  if (seg && isCjkHardsubSource(seg.source)) return null
  return fallbackCoverBox(frameW, frameH, fontSizePx)
}

/** Khung chữ dịch — below/above hoặc fallback */
export function tightCaptionTextBox(
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  wrapW?: number,
  maxLines = 3,
): PixelBox {
  const pad = coverPad(fontSizePx, frameW)
  const innerW = wrapW ?? Math.min(frameW, Math.round(frameW * 0.88))
  const lines = wrapCaptionText(text, innerW, fontSizePx, maxLines)
  const lineH = fontSizePx * 1.12
  const textW = Math.min(innerW, Math.max(...lines.map((l) => measureLineWidth(l, fontSizePx)), 1))
  return {
    x: 0,
    y: 0,
    w: Math.min(frameW, Math.ceil(textW + pad.x * 2)),
    h: Math.min(frameH, Math.ceil(lines.length * lineH + pad.top + pad.bottom)),
  }
}

export function fitCoverBoxOver(
  anchor: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  sourceText = '',
): PixelBox {
  return layoutOverMode(anchor, text, fontSizePx, frameW, frameH, sourceText).cover
}

/** Font preview: cqw theo chiều ngang caption (1 dòng), cqh khi nhiều dòng */
export function captionFontStyle(
  fontPx: number,
  boxSource: number,
  axis: 'w' | 'h' = 'h',
): React.CSSProperties {
  if (boxSource <= 0) return { fontSize: fontPx }
  const unit = axis === 'w' ? 'cqw' : 'cqh'
  return { fontSize: `calc(100${unit} * ${fontPx / boxSource})` }
}

/**
 * Overlay mid/dọc/nhãn: scale theo fontPx nguồn / kích thước cover
 * (không dùng cqh/n — công thức cũ bỏ qua fontPx nên kéo cỡ không ăn).
 */
export function overlayDisplayFontStyle(
  layout: 'vertical' | 'label' | 'mid',
  cover: PixelBox,
  fontPx: number,
  _lineCount: number,
): React.CSSProperties {
  const w = Math.max(1, cover.w)
  const h = Math.max(1, cover.h)
  const byW = Math.min(0.98, fontPx / w)
  const byH = Math.min(0.98, fontPx / h)
  if (layout === 'vertical') {
    // kẹp theo fontPx/cột — không fill full cqh (chữ to hơn bbox)
    return {
      fontSize: `min(calc(100cqw * ${byW}), calc(100cqh * ${byH}))`,
      lineHeight: 1.05,
      maxWidth: '100%',
      width: '100%',
      height: '100%',
      overflow: 'hidden',
    }
  }
  // mid/label: scale ≤ fontPx/box — không phình cqh (chữ to tràn bbox)
  if (layout === 'mid' || layout === 'label') {
    const n = Math.max(1, _lineCount)
    const lh = layout === 'mid' ? 1.1 : 1.12
    const fracH = Math.min(fontPx / h, 0.95 / (n * lh))
    const fracW = Math.min(0.98, fontPx / w)
    return {
      fontSize: `min(calc(100cqw * ${fracW}), calc(100cqh * ${fracH}))`,
      lineHeight: lh,
      maxWidth: '100%',
      width: '100%',
      height: '100%',
      overflow: 'hidden',
      padding: '0 1px',
      boxSizing: 'border-box' as const,
    }
  }
  return {
    fontSize: `min(calc(100cqw * ${byW}), calc(100cqh * ${byH}))`,
    lineHeight: 1.1,
    maxWidth: '100%',
  }
}

export type SnapGuides = { h: boolean; v: boolean }

/** Snap tâm khung về giữa khung video — kiểu CapCut */
export function snapBoxToCenter(box: PixelBox, frameW: number, frameH: number): { box: PixelBox; guides: SnapGuides } {
  const thresholdX = Math.max(8, frameW * 0.012)
  const thresholdY = Math.max(8, frameH * 0.012)
  const cx = frameW / 2
  const cy = frameH / 2
  let { x, y, w, h } = box
  const guides: SnapGuides = { h: false, v: false }
  const boxCx = x + w / 2
  const boxCy = y + h / 2
  if (Math.abs(boxCx - cx) <= thresholdX) {
    x = cx - w / 2
    guides.v = true
  }
  if (Math.abs(boxCy - cy) <= thresholdY) {
    y = cy - h / 2
    guides.h = true
  }
  return {
    box: {
      x: Math.max(0, Math.min(frameW - w, x)),
      y: Math.max(0, Math.min(frameH - h, y)),
      w,
      h,
    },
    guides,
  }
}

/**
 * Font theo bbox che chữ (OCR) — chèn trên/dưới/cover đều bám cỡ dải này.
 * Không sàn 48: chữ to tràn đè hardsub.
 */
export function autoFontFromBbox(
  bbox: PixelBox,
  text: string,
  baseFontPx = 0,
): number {
  const compactLen = Math.max(1, text.replace(/\s+/g, '').length)
  const byH = Math.floor(bbox.h * (compactLen <= 12 ? 0.78 : 0.65))
  const byW = Math.floor(bbox.w / Math.max(2.5, compactLen * 0.55))
  const auto = Math.max(10, Math.min(byH, byW, Math.floor(bbox.h * 0.92), 56))
  if (baseFontPx > 0) {
    // preferred user: không lớn hơn bbox che
    return Math.max(10, Math.min(baseFontPx, Math.max(auto, Math.floor(bbox.h * 0.95))))
  }
  return auto
}

export function resolveCaptionFontSize(
  seg: Segment | undefined,
  settings: ProjectSettings,
  _width: number,
  _height: number,
) {
  const segFs = seg?.fontSize ?? 0
  if (segFs > 0) return segFs
  if (settings.subtitleFontSize > 0) return settings.subtitleFontSize
  return AUTO_SUBTITLE_FONT
}

/** Overlay mid/dọc/nhãn: 0 = auto fit khung; >0 = đúng cỡ user set (không lấy cỡ phụ đề đáy dự án). */
export function resolveOverlayFontPreferred(seg: Segment | undefined): number {
  const segFs = seg?.fontSize ?? 0
  return segFs > 0 ? segFs : 0
}

/** placement khi xuất: cover+ burn → over; không cover → below/above.
 * Mid/dọc/nhãn luôn 'over' (neo OCR) — không đẩy xuống đáy khi chọn “phía dưới”.
 */
export function captionPlacement(settings: ProjectSettings): 'over' | 'below' | 'above' {
  if (settings.coverHardsubs && settings.burnSubs) return 'over'
  return settings.captionPlacement === 'above' ? 'above' : 'below'
}

/** Overlay OCR vẫn neo theo bbox khi burn — coverHardsubs chỉ bật mask. */
export function overlayTextEnabled(settings: ProjectSettings): boolean {
  return Boolean(settings.burnSubs && settings.targetLang !== 'none')
}

/** Ước lượng vị trí phụ đề — below/above: cỡ ≈ bbox che, neo sát trên/dưới dải OCR. */
export function estimateCaptionBox(
  ocr: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  placement: 'over' | 'below' | 'above',
): PixelBox {
  if (placement === 'over') return layoutOverMode(ocr, text, fontSizePx, frameW, frameH, '').caption

  // Caption đáy/trên ưu tiên một dòng trên bề rộng video; chỉ xuống dòng
  // khi đã co tới giới hạn đọc được. Dùng chung với preview/export bake.
  const fitted = fitOutsideCaption(ocr, text, fontSizePx, frameW)
  const fs = fitted.fontPx
  const wrapW = Math.max(24, Math.round(frameW * 0.92))
  const textBox = tightCaptionTextBox(text, fs, frameW, frameH, wrapW, Math.max(1, fitted.lines.length))
  const gap = Math.max(3, Math.round(fs * 0.18))
  const cx = ocr.x + ocr.w / 2
  let y0: number
  if (placement === 'below') {
    y0 = Math.min(frameH - textBox.h, ocr.y + ocr.h + gap)
  } else {
    y0 = Math.max(0, ocr.y - gap - textBox.h)
  }
  const x0 = Math.max(0, Math.min(frameW - textBox.w, Math.round(cx - textBox.w / 2)))
  return { x: x0, y: y0, w: textBox.w, h: textBox.h }
}
