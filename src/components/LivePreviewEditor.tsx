import React, { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { ProjectSettings, Segment, TextOverlay } from '../types'
import { api } from '../services/api'
import { IconHeadphones } from './Icons'
import { cn } from '@/lib/cn'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ScrollArea } from '@/components/ui/scroll-area'

type Props = {
  videoUrl: string
  projectId: string
  segments: Segment[]
  settings: ProjectSettings
  voices: { id: string; name: string }[]
  busy: boolean
  onBack: () => void
  onChange: (segment: Segment) => void | Promise<void>
  onExport: (segments?: Segment[]) => void | Promise<void>
  onSettings: (settings: ProjectSettings) => void
  overlays: TextOverlay[]
  onOverlayChange: (overlay: TextOverlay, isNew?: boolean) => void
  onOverlayDelete: (overlayId: string) => void
}

function formatTime(value: number) {
  const min = Math.floor(value / 60)
  const sec = value % 60
  return `${min}:${sec.toFixed(1).padStart(4, '0')}`
}

/* OpenCut-style HH:MM:SS:FF timecode (assumes 30fps for the frame counter) */
function formatTimecode(value: number) {
  const h = Math.floor(value / 3600)
  const m = Math.floor((value % 3600) / 60)
  const s = Math.floor(value % 60)
  const f = Math.floor((value % 1) * 30)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(f)}`
}

function parseHexColor(hex: string): [number, number, number] {
  const h = (hex || '#4c1d95').replace('#', '')
  if (h.length !== 6) return [76, 29, 149]
  const n = (i: number) => parseInt(h.slice(i, i + 2), 16)
  return [Number.isNaN(n(0)) ? 76 : n(0), Number.isNaN(n(2)) ? 29 : n(2), Number.isNaN(n(4)) ? 149 : n(4)]
}

/** Preview CSS khớp export (_apply_cover_mask). */
function coverMaskPreviewStyle(
  style: ProjectSettings['coverMaskStyle'],
  color: string,
  opacity: number,
): React.CSSProperties {
  const [r, g, b] = parseHexColor(color)
  const a = Math.max(0.05, Math.min(1, opacity / 100))
  if (style === 'solid') {
    return { backgroundColor: `rgba(${r},${g},${b},${a})` }
  }
  if (style === 'mosaic') {
    // Khớp export _blur_region — che kín chữ gốc
    const strength = Math.max(0.35, Math.min(0.85, a))
    return {
      backgroundColor: `rgba(48,48,56,${strength * 0.45})`,
      backdropFilter: 'blur(14px) saturate(0.45) contrast(0.95)',
      WebkitBackdropFilter: 'blur(14px) saturate(0.45) contrast(0.95)',
    }
  }
  const blurPx = Math.round(10 + a * 16)
  return {
    backgroundColor: `rgba(${r},${g},${b},${a})`,
    backdropFilter: `blur(${blurPx}px) saturate(0.35)`,
    WebkitBackdropFilter: `blur(${blurPx}px) saturate(0.35)`,
  }
}

const COVER_MASK_STYLES: { id: ProjectSettings['coverMaskStyle']; label: string }[] = [
  { id: 'blur', label: 'Làm mờ' },
  { id: 'solid', label: 'Màu nền' },
  { id: 'mosaic', label: 'Khối' },
]

type CropRect = { x: number; y: number; w: number; h: number }

type AspectPreset =
  | { id: 'original' | 'custom'; label: string; disabled?: boolean }
  | { id: string; label: string; w: number; h: number; orient: 'landscape' | 'portrait' | 'square' }

const ASPECT_PRESETS: AspectPreset[] = [
  { id: 'original', label: 'Bản gốc' },
  { id: 'custom', label: 'Tùy chỉnh', disabled: true },
  { id: '16:9', label: '16:9', w: 16, h: 9, orient: 'landscape' },
  { id: '4:3', label: '4:3', w: 4, h: 3, orient: 'landscape' },
  { id: '2.35:1', label: '2.35:1', w: 235, h: 100, orient: 'landscape' },
  { id: '2:1', label: '2:1', w: 2, h: 1, orient: 'landscape' },
  { id: '1.85:1', label: '1.85:1', w: 185, h: 100, orient: 'landscape' },
  { id: '9:16', label: '9:16', w: 9, h: 16, orient: 'portrait' },
  { id: '3:4', label: '3:4', w: 3, h: 4, orient: 'portrait' },
  { id: '58inch', label: '5.8-inch', w: 108, h: 234, orient: 'portrait' },
  { id: '1:1', label: '1:1', w: 1, h: 1, orient: 'square' },
]

function resolveCropRect(sourceW: number, sourceH: number, presetId: string): CropRect {
  if (sourceW <= 0 || sourceH <= 0) return { x: 0, y: 0, w: 1, h: 1 }
  if (!presetId || presetId === 'original' || presetId === 'custom') {
    return { x: 0, y: 0, w: sourceW, h: sourceH }
  }
  const preset = ASPECT_PRESETS.find((p) => p.id === presetId && 'w' in p) as
    | Extract<AspectPreset, { w: number }>
    | undefined
  if (!preset) return { x: 0, y: 0, w: sourceW, h: sourceH }
  const target = preset.w / preset.h
  const source = sourceW / sourceH
  if (source >= target) {
    const h = sourceH
    const w = h * target
    return { x: (sourceW - w) / 2, y: 0, w, h }
  }
  const w = sourceW
  const h = w / target
  return { x: 0, y: (sourceH - h) / 2, w, h }
}

function sourceToDisplayStyle(
  box: { x: number; y: number; w: number; h: number },
  crop: CropRect,
): React.CSSProperties {
  return {
    left: `${((box.x - crop.x) / crop.w) * 100}%`,
    top: `${((box.y - crop.y) / crop.h) * 100}%`,
    width: `${(box.w / crop.w) * 100}%`,
    height: `${(box.h / crop.h) * 100}%`,
  }
}

function videoCropStyle(sourceW: number, sourceH: number, crop: CropRect): React.CSSProperties {
  return {
    width: `${(sourceW / crop.w) * 100}%`,
    height: `${(sourceH / crop.h) * 100}%`,
    left: `${(-crop.x / crop.w) * 100}%`,
    top: `${(-crop.y / crop.h) * 100}%`,
    objectFit: 'fill',
  }
}

function AspectIcon({ orient }: { orient: 'landscape' | 'portrait' | 'square' }) {
  const cls = 'border border-current rounded-[2px] opacity-70'
  if (orient === 'portrait') return <span className={cn(cls, 'inline-block h-3.5 w-2')} aria-hidden />
  if (orient === 'square') return <span className={cn(cls, 'inline-block size-2.5')} aria-hidden />
  return <span className={cn(cls, 'inline-block h-2 w-3.5')} aria-hidden />
}

function segmentAt(segments: Segment[], time: number) {
  return segments.find((s) => time >= s.start && time < s.end) ?? null
}

function segmentHasDub(seg: Segment | undefined): boolean {
  if (!seg) return false
  const isOverlay = seg.layout === 'vertical' || seg.layout === 'label'
  return isOverlay ? seg.dub === true : seg.dub !== false
}

/** Chiều rộng clip TTS trên timeline (giây) */
function dubClipSeconds(seg: Segment): number {
  const slot = Math.max(0.05, seg.end - seg.start)
  const ad = seg.audioDuration ?? 0
  const speed = Math.max(0.75, Math.min(1.5, seg.ttsSpeed ?? 1))
  if (ad <= 0) return slot
  return Math.min(slot, ad / speed)
}

/** Filmstrip timeline từ MP4 — ponytail: tối đa 48 khung, seek tuần tự */
function TimelineFilmstrip({
  videoUrl,
  duration,
  widthPx,
  heightPx,
  className,
}: {
  videoUrl: string
  duration: number
  widthPx: number
  heightPx: number
  className?: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!videoUrl || duration <= 0 || widthPx <= 0) return
    let cancelled = false
    const video = document.createElement('video')
    video.src = videoUrl
    video.muted = true
    video.playsInline = true
    video.preload = 'auto'

    const seekTo = (t: number) => new Promise<void>((resolve) => {
      const done = () => { video.removeEventListener('seeked', done); resolve() }
      video.addEventListener('seeked', done)
      video.currentTime = Math.max(0, Math.min(duration - 0.04, t))
    })

    void (async () => {
      try {
        await new Promise<void>((resolve, reject) => {
          video.onloadeddata = () => resolve()
          video.onerror = () => reject(new Error('filmstrip'))
        })
        if (cancelled) return
        const canvas = canvasRef.current
        const ctx = canvas?.getContext('2d')
        if (!canvas || !ctx) return
        const w = Math.max(1, Math.round(widthPx))
        const h = Math.max(1, Math.round(heightPx))
        canvas.width = w
        canvas.height = h
        const n = Math.max(1, Math.min(48, Math.ceil(w / 52)))
        const tw = w / n
        const vW = video.videoWidth || 16
        const vH = video.videoHeight || 9
        const scale = Math.max(tw / vW, h / vH)
        const dw = vW * scale
        const dh = vH * scale
        for (let i = 0; i < n; i++) {
          if (cancelled) return
          await seekTo(((i + 0.5) / n) * duration)
          const dx = i * tw + (tw - dw) / 2
          const dy = (h - dh) / 2
          ctx.drawImage(video, dx, dy, dw, dh)
          if (i < n - 1) {
            ctx.fillStyle = 'rgba(0,0,0,0.35)'
            ctx.fillRect(i * tw + tw - 1, 0, 1, h)
          }
        }
      } catch { /* preview optional */ }
    })()

    return () => {
      cancelled = true
      video.removeAttribute('src')
      video.load()
    }
  }, [videoUrl, duration, widthPx, heightPx])

  return (
    <canvas
      ref={canvasRef}
      className={cn('pointer-events-none select-none', className)}
      style={{ width: widthPx, height: heightPx }}
      aria-hidden
    />
  )
}

type PixelBox = { x: number; y: number; w: number; h: number }

const AUTO_SUBTITLE_FONT = 36
/** Khớp burn._cover_max_h — đủ 1–3 dòng theo font */
const COVER_MAX_H_FRAME_RATIO = 0.065

const COVER_SHADOW_BOT = 4

function coverPad(fontSizePx = AUTO_SUBTITLE_FONT, frameW = 1080) {
  return {
    x: Math.max(6, Math.round(frameW * 0.008)),
    top: 3,
    bottom: Math.max(10, Math.round(fontSizePx * 0.22)),
  }
}

const CAP_PAD_X = 2

function coverInnerWidth(coverW: number, fontSizePx: number, frameW: number) {
  const pad = coverPad(fontSizePx, frameW)
  return Math.max(24, coverW - pad.x * 2 - CAP_PAD_X * 2)
}

function frameMaxInnerWidth(fontSizePx: number, frameW: number) {
  const maxCoverW = Math.min(frameW, Math.round(frameW * 0.92))
  return coverInnerWidth(maxCoverW, fontSizePx, frameW)
}

function coverBleedX(contentW: number) {
  return Math.max(2, Math.round(contentW * 0.008))
}

/** Chiều ngang ink chữ cũ: max(OCR anchor, đo source, cover đã lưu). */
function resolveInkWidth(
  anchor: PixelBox,
  coverBox: PixelBox | null,
  hasSource: boolean,
  sourceW: number,
): number {
  let w = hasSource ? Math.max(sourceW, anchor.w) : anchor.w
  if (coverBox) {
    w = Math.max(w, coverBox.w - coverBleedX(coverBox.w) * 2)
  }
  return w
}

function coverContentWidth(origW: number, transW: number) {
  return Math.max(origW, transW)
}

function coverBoxWidth(contentW: number, frameW: number) {
  const bleed = coverBleedX(contentW)
  return Math.min(frameW, Math.ceil(contentW + bleed * 2))
}

type OverLayout = { cover: PixelBox; caption: PixelBox; lines: string[] }

let _measureCtx: CanvasRenderingContext2D | null = null
function measureLineWidth(text: string, fontSizePx: number) {
  if (typeof document !== 'undefined') {
    if (!_measureCtx) {
      const c = document.createElement('canvas')
      _measureCtx = c.getContext('2d')
    }
    if (_measureCtx) {
      _measureCtx.font = `700 ${fontSizePx}px system-ui, -apple-system, "Segoe UI", sans-serif`
      return _measureCtx.measureText(text).width
    }
  }
  return text.length * fontSizePx * 0.40
}

/** Xuống dòng — đổ ngang tối đa trước, rồi mới cân 2–3 dòng */
function wrapCaptionText(text: string, maxInnerW: number, fontSizePx: number, maxLines = 3): string[] {
  const trimmed = text.trim()
  if (!trimmed) return ['']
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

  // Chỉ tách 2 dòng khi thật sự không gộp được — tránh 3 dòng kiểu "mơ" / "tới"
  if (out.length >= 3 && maxLines >= 2) {
    let best: string[] | null = null
    let bestScore = Infinity
    for (let i = 1; i < words.length; i++) {
      const a = words.slice(0, i).join(' ')
      const b = words.slice(i).join(' ')
      if (!fits(a) || !fits(b)) continue
      const score = Math.abs(lineWidth(a) - lineWidth(b))
      if (score < bestScore) {
        bestScore = score
        best = [a, b]
      }
    }
    if (best) out = best
  }

  return out
}

/** Layout over: cover sát chữ gốc; chỉ nới ngang khi bản dịch dài hơn */
function layoutOverMode(
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
  const oneLineW = measureLineWidth(trimmed, fontSizePx)
  const maxInnerW = frameMaxInnerWidth(fontSizePx, frameW)

  const lines = oneLineW <= maxInnerW
    ? [trimmed]
    : wrapCaptionText(trimmed, maxInnerW, fontSizePx, 3)

  const lineH = fontSizePx * 1.12
  const textBlockH = Math.ceil(lines.length * lineH + 4)
  const textW = Math.max(...lines.map((l) => measureLineWidth(l, fontSizePx)), oneLineW)

  const sourceTrim = sourceText.trim()
  const sourceFontPx = Math.max(fontSizePx, Math.round(anchor.h * 0.72))
  const sourceW = sourceTrim ? measureLineWidth(sourceTrim, sourceFontPx) : 0
  const origW = inkW ?? (sourceTrim ? Math.max(sourceW, anchor.w) : anchor.w)
  const contentW = coverContentWidth(origW, textW)
  const capPadX = 2
  const captionW = Math.ceil(textW + capPadX * 2)
  const coverW = Math.min(frameW, Math.max(coverBoxWidth(contentW, frameW), captionW))
  const coverX = Math.round(Math.max(0, Math.min(frameW - coverW, cx - coverW / 2)))
  const coverY = Math.max(0, anchor.y - pad.top)
  const coverH = Math.min(
    frameH - coverY,
    Math.max(anchor.h, textBlockH) + pad.top + pad.bottom + COVER_SHADOW_BOT,
  )

  const captionX = Math.round(Math.max(0, Math.min(frameW - captionW, cx - captionW / 2)))
  const captionY = Math.round(anchor.y + Math.max(0, (anchor.h - textBlockH) / 2))

  return {
    cover: { x: Math.round(coverX), y: Math.round(coverY), w: Math.round(coverW), h: Math.round(coverH) },
    caption: { x: captionX, y: captionY, w: captionW, h: textBlockH },
    lines,
  }
}

/** Cover hiển thị / xuất — bbox lưu trực tiếp khung này (mode over). */
function resolveSegmentCover(
  seg: Segment | undefined,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
): PixelBox | null {
  if (!seg) return null
  const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)
  const over = settings.coverHardsubs && settings.burnSubs && seg.translation.trim()
  if (!over) {
    return normalizeCoverBox(seg.bbox ?? fallbackCoverBox(frameW, frameH, fontPx), frameW, frameH, fontPx)
  }
  if (seg.bbox) {
    return clampCoverBox(seg.bbox, frameW, frameH)
  }
  const anchor = normalizeCoverBox(fallbackCoverBox(frameW, frameH, fontPx), frameW, frameH, fontPx)
  return fitCoverBoxOver(anchor, seg.translation, fontPx, frameW, frameH, seg.source ?? '')
}

function hasStoredLayout(seg: Segment | undefined, fontPx?: number): boolean {
  const cl = seg?.captionLayout
  const b = seg?.bbox
  if (!(b && cl?.lines?.length && cl.w > 0 && cl.h > 0)) return false
  if (fontPx != null && fontPx > 0 && cl.fontSize > 0 && fontPx !== cl.fontSize) return false
  return true
}

function toCaptionLayout(caption: PixelBox, lines: string[], fontSize: number): NonNullable<Segment['captionLayout']> {
  return { x: caption.x, y: caption.y, w: caption.w, h: caption.h, lines, fontSize }
}

/** Chỉ gọi khi chưa có layout lưu hoặc user vừa chỉnh cover/chữ. */
function computeOverLayout(
  seg: Segment,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  coverOverride?: PixelBox,
): OverLayout | null {
  if (!seg.translation.trim()) return null
  if (!(settings.coverHardsubs && settings.burnSubs)) return null
  const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)
  if (coverOverride) {
    return manualCoverLayout(coverOverride, seg.translation, fontPx, frameW, frameH, true)
  }
  const coverHint = seg.bbox ? clampCoverBox(seg.bbox, frameW, frameH) : null
  const anchor = coverHint
    ? coverToAnchor(coverHint, fontPx, frameW)
    : normalizeCoverBox(fallbackCoverBox(frameW, frameH, fontPx), frameW, frameH, fontPx)
  const sourceTrim = (seg.source ?? '').trim()
  const sourceFontPx = Math.max(fontPx, Math.round(anchor.h * 0.72))
  const sourceW = sourceTrim ? measureLineWidth(sourceTrim, sourceFontPx) : 0
  const inkW = resolveInkWidth(anchor, coverHint, !!sourceTrim, sourceW)
  return layoutOverMode(anchor, seg.translation, fontPx, frameW, frameH, seg.source ?? '', inkW)
}

function resolveOverLayout(
  seg: Segment | undefined,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  coverOverride?: PixelBox,
): OverLayout | null {
  if (!seg?.translation.trim()) return null
  if (!(settings.coverHardsubs && settings.burnSubs)) return null
  const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)
  if (coverOverride) {
    return manualCoverLayout(coverOverride, seg.translation, fontPx, frameW, frameH, true)
  }
  if (seg.bbox) {
    const cover = clampCoverBox(seg.bbox, frameW, frameH)
    // Đã kéo tay (có captionLayout): chữ bám đúng width bbox — kéo dài = ít dòng hơn
    if (hasStoredLayout(seg, fontPx)) {
      const laid = layoutCaptionInCover(cover, seg.translation, fontPx, frameW)
      return { cover, ...laid }
    }
    // Chưa khóa tay: ưu tiên giãn ngang 1 dòng, không xuống dòng sớm
    return adaptiveCoverLayout(cover, seg.translation, fontPx, frameW, frameH)
  }
  return computeOverLayout(seg, settings, frameW, frameH)
}

function cropCoversFull(crop: CropRect, frameW: number, frameH: number): boolean {
  return crop.x <= 1 && crop.y <= 1 && crop.w >= frameW - 2 && crop.h >= frameH - 2
}

function intersectBox(a: PixelBox, crop: CropRect): PixelBox | null {
  const x = Math.max(a.x, crop.x)
  const y = Math.max(a.y, crop.y)
  const x2 = Math.min(a.x + a.w, crop.x + crop.w)
  const y2 = Math.min(a.y + a.h, crop.y + crop.h)
  if (x2 - x < 4 || y2 - y < 4) return null
  return { x: Math.round(x), y: Math.round(y), w: Math.round(x2 - x), h: Math.round(y2 - y) }
}

function unionBox(a: PixelBox, b: PixelBox): PixelBox {
  const x = Math.min(a.x, b.x)
  const y = Math.min(a.y, b.y)
  const x2 = Math.max(a.x + a.w, b.x + b.w)
  const y2 = Math.max(a.y + a.h, b.y + b.h)
  return { x: Math.round(x), y: Math.round(y), w: Math.round(x2 - x), h: Math.round(y2 - y) }
}

type PreviewOverLayout = OverLayout & { mask: PixelBox }

/** Preview: chữ luôn theo cover; 9:16 chỉ thêm mask che OCR trong crop — không wrap lệch bbox. */
function resolvePreviewOverLayout(
  seg: Segment | undefined,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  crop: CropRect,
  coverOverride?: PixelBox,
): PreviewOverLayout | null {
  const base = resolveOverLayout(seg, settings, frameW, frameH, coverOverride)
  if (!base) return null
  const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)
  // Luôn đo lại chữ theo đúng cover đang hiện (kéo ngang → unwrap)
  const laid = layoutCaptionInCover(base.cover, seg!.translation, fontPx, frameW)
  if (cropCoversFull(crop, frameW, frameH)) {
    return { cover: base.cover, caption: laid.caption, lines: laid.lines, mask: base.cover }
  }
  const ink = intersectBox(base.cover, crop)
  const mask = ink ? unionBox(ink, base.cover) : base.cover
  return { cover: base.cover, caption: laid.caption, lines: laid.lines, mask }
}

function estimatePreviewCaptionBox(
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

function segmentWithLayout(seg: Segment, layout: OverLayout, fontPx: number): Segment {
  return {
    ...seg,
    bbox: { x: layout.cover.x, y: layout.cover.y, w: layout.cover.w, h: layout.cover.h },
    captionLayout: toCaptionLayout(layout.caption, layout.lines, fontPx),
  }
}

function buildExportSegments(
  segments: Segment[],
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
): Segment[] {
  if (!(settings.coverHardsubs && settings.burnSubs) || frameW <= 0) return segments
  return segments.map((seg) => {
    if (!seg.translation.trim()) return seg
    const layout = resolveOverLayout(seg, settings, frameW, frameH)
    if (!layout) return seg
    const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)
    return segmentWithLayout(seg, layout, fontPx)
  })
}

/** Anchor OCR suy từ cover — chỉ để căn caption */
function coverToAnchor(cover: PixelBox, fontSizePx: number, frameW = 1080): PixelBox {
  const pad = coverPad(fontSizePx, frameW)
  return {
    x: Math.round(cover.x + pad.x),
    y: Math.round(cover.y + pad.top),
    w: Math.max(12, Math.round(cover.w - pad.x * 2)),
    h: Math.max(12, Math.round(cover.h - pad.top - pad.bottom)),
  }
}

function coverMaxHeight(frameH: number, fontSizePx = AUTO_SUBTITLE_FONT) {
  const one = Math.round(fontSizePx * 1.45 + 10)
  const cap = Math.round(fontSizePx * 3.4 + 16)
  const byFrame = Math.round(frameH * COVER_MAX_H_FRAME_RATIO)
  return Math.max(one, Math.min(cap, byFrame))
}

/** Giữ nguyên chiều cao OCR — chỉ kẹp trần sanity, không cắt hardsub */
function normalizeCoverBox(box: PixelBox, frameW: number, frameH: number, _fontSizePx = AUTO_SUBTITLE_FONT): PixelBox {
  let { x, y, w, h } = box
  const sanityMaxH = Math.round(frameH * 0.15)
  const sanityMaxW = Math.round(frameW * 0.85)
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
function clampCoverBox(box: PixelBox, frameW: number, frameH: number, minSize = 12): PixelBox {
  let { x, y, w, h } = box
  x = Math.max(0, Math.min(x, frameW - minSize))
  y = Math.max(0, Math.min(y, frameH - minSize))
  w = Math.max(minSize, Math.min(w, frameW - x))
  h = Math.max(minSize, Math.min(h, frameH - y))
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}

/** Caption trong cover cố định (user vừa kéo tay). Kéo rộng → ít dòng; cao tự đủ nếu wrap. */
function layoutCaptionInCover(
  cover: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
): Pick<OverLayout, 'caption' | 'lines'> {
  const trimmed = text.trim()
  const maxInnerW = coverInnerWidth(cover.w, fontSizePx, frameW)
  const oneLineW = measureLineWidth(trimmed, fontSizePx)
  const lines = oneLineW <= maxInnerW
    ? [trimmed]
    : wrapCaptionText(trimmed, maxInnerW, fontSizePx, 3)
  const lineH = fontSizePx * 1.12
  const textBlockH = Math.ceil(lines.length * lineH + 4)
  const textW = Math.max(...lines.map((l) => measureLineWidth(l, fontSizePx)), oneLineW)
  const captionW = Math.ceil(Math.min(textW + CAP_PAD_X * 2, cover.w))
  const cx = cover.x + cover.w / 2
  const captionX = Math.round(Math.max(cover.x, Math.min(cover.x + cover.w - captionW, cx - captionW / 2)))
  const captionY = Math.round(cover.y + Math.max(0, (cover.h - textBlockH) / 2))
  return {
    caption: { x: captionX, y: captionY, w: captionW, h: textBlockH },
    lines,
  }
}

/** Tự co/giãn cover theo chữ — giữ tâm khung cũ; ngang trước, bbox khít chữ. */
function adaptiveCoverLayout(
  cover: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
): OverLayout {
  const pad = coverPad(fontSizePx, frameW)
  const cx = cover.x + cover.w / 2
  const cy = cover.y + cover.h / 2
  const trimmed = text.trim()
  const maxInnerW = frameMaxInnerWidth(fontSizePx, frameW)
  const oneLineW = measureLineWidth(trimmed, fontSizePx)

  const wrapAt = (innerW: number) =>
    oneLineW <= innerW ? [trimmed] : wrapCaptionText(trimmed, innerW, fontSizePx, 3)

  let lines = wrapAt(maxInnerW)

  const sizeFromLines = (ls: string[]) => {
    const lineH = fontSizePx * 1.12
    const textBlockH = Math.ceil(ls.length * lineH + 4)
    const textW = Math.max(...ls.map((l) => measureLineWidth(l, fontSizePx)), 1)
    const captionW = Math.ceil(textW + CAP_PAD_X * 2)
    const coverW = Math.min(frameW, Math.max(12, captionW + pad.x * 2))
    const coverH = Math.min(frameH, Math.max(12, textBlockH + pad.top + pad.bottom + COVER_SHADOW_BOT))
    return { lineH, textBlockH, textW, captionW, coverW, coverH }
  }

  let { textBlockH, captionW, coverW, coverH } = sizeFromLines(lines)
  let coverX = Math.round(Math.max(0, Math.min(frameW - coverW, cx - coverW / 2)))
  let coverY = Math.round(Math.max(0, Math.min(frameH - coverH, cy - coverH / 2)))
  let box = clampCoverBox({ x: coverX, y: coverY, w: coverW, h: coverH }, frameW, frameH)

  const inner = coverInnerWidth(box.w, fontSizePx, frameW)
  const finalLines = wrapAt(inner)
  if (finalLines.join('\n') !== lines.join('\n')) {
    lines = finalLines
    const sized = sizeFromLines(lines)
    textBlockH = sized.textBlockH
    captionW = sized.captionW
    coverW = sized.coverW
    coverH = sized.coverH
    coverX = Math.round(Math.max(0, Math.min(frameW - coverW, cx - coverW / 2)))
    coverY = Math.round(Math.max(0, Math.min(frameH - coverH, cy - coverH / 2)))
    box = clampCoverBox({ x: coverX, y: coverY, w: coverW, h: coverH }, frameW, frameH)
  }

  const capX = Math.round(Math.max(box.x, Math.min(box.x + box.w - captionW, box.x + box.w / 2 - captionW / 2)))
  const capY = Math.round(box.y + Math.max(0, (box.h - textBlockH) / 2))
  return {
    cover: box,
    caption: { x: capX, y: capY, w: Math.min(captionW, box.w), h: textBlockH },
    lines,
  }
}

function manualCoverLayout(
  cover: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  fixed = false,
): OverLayout {
  if (fixed) {
    let box = clampCoverBox(cover, frameW, frameH)
    let laid = layoutCaptionInCover(box, text, fontSizePx, frameW)
    const pad = coverPad(fontSizePx, frameW)
    const needH = laid.caption.h + pad.top + pad.bottom + COVER_SHADOW_BOT
    if (needH > box.h) {
      const cy = box.y + box.h / 2
      box = clampCoverBox(
        { x: box.x, y: Math.round(cy - needH / 2), w: box.w, h: needH },
        frameW,
        frameH,
      )
      laid = layoutCaptionInCover(box, text, fontSizePx, frameW)
    }
    return { cover: box, ...laid }
  }
  return adaptiveCoverLayout(cover, text, fontSizePx, frameW, frameH)
}

function fallbackCoverBox(frameW: number, frameH: number, fontSizePx = AUTO_SUBTITLE_FONT): PixelBox {
  const h = coverMaxHeight(frameH, fontSizePx)
  const w = Math.round(frameW * 0.4)
  return {
    x: Math.round((frameW - w) / 2),
    y: Math.round(frameH - h - Math.round(frameH * 0.06)),
    w,
    h,
  }
}

/** Khung chữ dịch — below/above hoặc fallback */
function tightCaptionTextBox(
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  wrapW?: number,
): PixelBox {
  const pad = coverPad(fontSizePx, frameW)
  const innerW = wrapW ?? Math.min(frameW, Math.round(frameW * 0.88))
  const lines = wrapCaptionText(text, innerW, fontSizePx, 3)
  const lineH = fontSizePx * 1.12
  const textW = Math.min(innerW, Math.max(...lines.map((l) => measureLineWidth(l, fontSizePx)), 1))
  return {
    x: 0,
    y: 0,
    w: Math.min(frameW, Math.ceil(textW + pad.x * 2)),
    h: Math.min(frameH, Math.ceil(lines.length * lineH + pad.top + pad.bottom)),
  }
}

function fitCoverBoxOver(
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
function captionFontStyle(
  fontPx: number,
  boxSource: number,
  axis: 'w' | 'h' = 'h',
): React.CSSProperties {
  if (boxSource <= 0) return { fontSize: fontPx }
  const unit = axis === 'w' ? 'cqw' : 'cqh'
  return { fontSize: `calc(100${unit} * ${fontPx / boxSource})` }
}

type SnapGuides = { h: boolean; v: boolean }

/** Snap tâm khung về giữa khung video — kiểu CapCut */
function snapBoxToCenter(box: PixelBox, frameW: number, frameH: number): { box: PixelBox; guides: SnapGuides } {
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

function resolveCaptionFontSize(
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

/** placement khi xuất: cover+ burn → over; không cover → below/above */
function captionPlacement(settings: ProjectSettings): 'over' | 'below' | 'above' {
  if (settings.coverHardsubs && settings.burnSubs) return 'over'
  return settings.captionPlacement === 'above' ? 'above' : 'below'
}

/** Ước lượng vị trí phụ đề */
function estimateCaptionBox(
  ocr: PixelBox,
  text: string,
  fontSizePx: number,
  frameW: number,
  frameH: number,
  placement: 'over' | 'below' | 'above',
): PixelBox {
  if (placement === 'over') return layoutOverMode(ocr, text, fontSizePx, frameW, frameH, '').caption

  const textBox = tightCaptionTextBox(text, fontSizePx, frameW, frameH)
  const gap = Math.max(4, Math.round(fontSizePx / 5))
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

/* ── OpenCut assets-panel tab rail (same tabs as opencut.app) ── */
type AssetsTab =
  | 'media' | 'sounds' | 'text' | 'stickers' | 'effects'
  | 'transitions' | 'captions' | 'filters' | 'adjustment' | 'settings'

const ASSET_TABS: { key: AssetsTab; label: string; icon: React.ReactNode }[] = [
  {
    key: 'media', label: 'Media',
    icon: <TabSvg><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" /></TabSvg>,
  },
  {
    key: 'sounds', label: 'Sounds',
    icon: <TabSvg><path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3" /></TabSvg>,
  },
  {
    key: 'text', label: 'Text',
    icon: <TabSvg><polyline points="4 7 4 4 20 4 20 7" /><line x1="9" y1="20" x2="15" y2="20" /><line x1="12" y1="4" x2="12" y2="20" /></TabSvg>,
  },
  {
    key: 'stickers', label: 'Stickers',
    icon: <TabSvg><circle cx="12" cy="12" r="10" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></TabSvg>,
  },
  {
    key: 'effects', label: 'Effects',
    icon: <TabSvg><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72Z" /><path d="m14 7 3 3" /></TabSvg>,
  },
  {
    key: 'transitions', label: 'Transitions',
    icon: <TabSvg><path d="m6 17 5-5-5-5" /><path d="m13 17 5-5-5-5" /></TabSvg>,
  },
  {
    key: 'captions', label: 'Captions',
    icon: <TabSvg><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 15h4M15 15h2M7 11h2M13 11h4" /></TabSvg>,
  },
  {
    key: 'filters', label: 'Filters',
    icon: <TabSvg><circle cx="13.5" cy="6.5" r=".5" /><circle cx="17.5" cy="10.5" r=".5" /><circle cx="8.5" cy="7.5" r=".5" /><circle cx="6.5" cy="12.5" r=".5" /><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z" /></TabSvg>,
  },
  {
    key: 'adjustment', label: 'Adjustment',
    icon: <TabSvg><line x1="21" y1="4" x2="14" y2="4" /><line x1="10" y1="4" x2="3" y2="4" /><line x1="21" y1="12" x2="12" y2="12" /><line x1="8" y1="12" x2="3" y2="12" /><line x1="21" y1="20" x2="16" y2="20" /><line x1="12" y1="20" x2="3" y2="20" /><line x1="14" y1="2" x2="14" y2="6" /><line x1="8" y1="10" x2="8" y2="14" /><line x1="16" y1="18" x2="16" y2="22" /></TabSvg>,
  },
  {
    key: 'settings', label: 'Settings',
    icon: <TabSvg><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" /><circle cx="12" cy="12" r="3" /></TabSvg>,
  },
]

function TabSvg({ children }: { children: React.ReactNode }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {children}
    </svg>
  )
}

const FONT_SIZES = [16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96, 120]

type PropTab = 'caption' | 'video' | 'audio' | 'mask' | 'overlay'

export default function LivePreviewEditor({
  videoUrl,
  projectId,
  segments,
  settings,
  voices,
  busy,
  onBack,
  onChange,
  onExport,
  onSettings,
  overlays,
  onOverlayChange,
  onOverlayDelete,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const dubAudioRef = useRef<HTMLAudioElement | null>(null)
  const dubTokenRef = useRef('')
  const videoMutedForDubRef = useRef(false)
  const trackRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const previewRef = useRef<HTMLDivElement>(null)
  const rulerScrollRef = useRef<HTMLDivElement>(null)
  const tracksScrollRef = useRef<HTMLDivElement>(null)
  const tracksColRef = useRef<HTMLDivElement>(null)
  const bboxDraftRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null)
  const draftRef = useRef<{ id: string; start: number; end: number } | null>(null)

  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [videoSize, setVideoSize] = useState({ width: 1920, height: 1080 })
  const [selectedId, setSelectedId] = useState(segments[0]?.id ?? '')
  const [ttsBusy, setTtsBusy] = useState(false)
  const [ttsError, setTtsError] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ id: string; start: number; end: number } | null>(null)
  const [bboxDraft, setBboxDraft] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [draggingBox, setDraggingBox] = useState(false)
  const [snapGuides, setSnapGuides] = useState<SnapGuides>({ h: false, v: false })
  const [selectedOverlayId, setSelectedOverlayId] = useState<string | null>(null)
  const [tool, setTool] = useState<'select' | 'cover' | 'text'>('select')
  const [zoom, setZoom] = useState(1)
  const [scrollLeft, setScrollLeft] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [assetsTab, setAssetsTab] = useState<AssetsTab>('media')
  const [propTab, setPropTab] = useState<PropTab>('caption')
  const [fontSizeDraft, setFontSizeDraft] = useState(0)
  const [aspectMenuOpen, setAspectMenuOpen] = useState(false)
  const aspectMenuRef = useRef<HTMLDivElement>(null)
  const pxPerSec = 50 * zoom

  function syncFollowers() {
    const scrl = tracksScrollRef.current?.scrollLeft ?? 0
    setScrollLeft(scrl)
    if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = scrl
  }

  const active = segmentAt(segments, time)
  const selected = segments.find((s) => s.id === selectedId) ?? active ?? segments[0]
  const lastSegment = segments[segments.length - 1]
  const timelineDuration = Math.max(duration, lastSegment?.end ?? 0, 1)
  const videoSpan = Math.max(duration, timelineDuration)
  const trackWidth = Math.max(Math.ceil(timelineDuration * pxPerSec) + 200, 400)
  const playheadPx = time * pxPerSec - scrollLeft
  const tickInterval = [1, 2, 5, 10, 30, 60, 120, 300, 600].find((c) => c * pxPerSec >= 80) ?? 600
  const ticks = Array.from(
    { length: Math.ceil(timelineDuration / tickInterval) + 1 },
    (_, i) => i * tickInterval,
  ).filter((t) => t <= timelineDuration + tickInterval)

  const sourceWidth = videoSize.width
  const sourceHeight = videoSize.height
  const aspectId = settings.previewAspectRatio ?? 'original'
  const crop = useMemo(
    () => resolveCropRect(sourceWidth, sourceHeight, aspectId),
    [sourceWidth, sourceHeight, aspectId],
  )
  const overCoverMode = settings.coverHardsubs && settings.burnSubs
  const selectedFontPx = resolveCaptionFontSize(selected ?? undefined, settings, sourceWidth, sourceHeight)
  const fallbackBox = fallbackCoverBox(sourceWidth, sourceHeight, selectedFontPx)
  const selectedLayoutSource = resolveOverLayout(selected, settings, sourceWidth, sourceHeight)
  const selectedBoxSource = bboxDraft
    ?? (selected?.bbox ? clampCoverBox(selected.bbox, sourceWidth, sourceHeight) : null)
    ?? selectedLayoutSource?.cover
    ?? resolveSegmentCover(selected, settings, sourceWidth, sourceHeight)
    ?? fallbackBox
  const activeCoverDraft =
    active && selected?.id === active.id && bboxDraft
      ? bboxDraft
      : active?.bbox
        ? clampCoverBox(active.bbox, sourceWidth, sourceHeight)
        : undefined
  const activeOverLayout =
    overCoverMode && active?.translation.trim() && settings.burnSubs && settings.targetLang !== 'none'
      ? resolvePreviewOverLayout(active, settings, sourceWidth, sourceHeight, crop, activeCoverDraft)
      : null
  // Khung tím: đang kéo → draft; không thì theo layout (giãn ngang 1 dòng khi chưa khóa tay)
  const selectedBox = bboxDraft
    ?? activeOverLayout?.cover
    ?? selectedBoxSource
  const maskBox = activeOverLayout?.mask ?? null
  const activeOcrBox = selectedBox
  const activeOverlays = overlays.filter((o) => time >= o.start && time < o.end)
  const selectedOverlay = overlays.find((o) => o.id === selectedOverlayId) ?? null

  useEffect(() => {
    if (!selectedId && segments[0]) setSelectedId(segments[0].id)
  }, [segments, selectedId])

  useEffect(() => {
    setFontSizeDraft(selected?.fontSize ?? 0)
  }, [selected?.id, selected?.fontSize])

  useEffect(() => () => {
    audioRef.current?.pause()
    dubAudioRef.current?.pause()
  }, [])

  function pauseDubAudio() {
    dubAudioRef.current?.pause()
    dubTokenRef.current = ''
    const video = videoRef.current
    if (video && videoMutedForDubRef.current) {
      video.muted = false
      videoMutedForDubRef.current = false
    }
  }

  /** Đồng bộ clip TTS với playhead khi phát timeline */
  function syncDubAudio(videoTime: number, isPlaying: boolean) {
    const video = videoRef.current
    if (!video || !isPlaying) {
      pauseDubAudio()
      return
    }
    const seg = segmentAt(segments, videoTime)
    const inSlot = seg && videoTime >= seg.start && videoTime < seg.end
    if (!inSlot || !segmentHasDub(seg) || !seg.audioUrl) {
      if (dubTokenRef.current) pauseDubAudio()
      return
    }

    const speed = Math.max(0.75, Math.min(1.5, seg.ttsSpeed ?? 1))
    const vol = Math.min(1, Math.max(0, (seg.ttsVolume ?? 100) / 100))
    const offset = videoTime - seg.start
    const wantTime = Math.max(0, offset * speed)
    const token = `${seg.id}|${seg.audioUrl}`

    if (!videoMutedForDubRef.current) {
      video.muted = true
      videoMutedForDubRef.current = true
    }

    let a = dubAudioRef.current
    if (!a) {
      a = new Audio()
      dubAudioRef.current = a
    }

    if (dubTokenRef.current !== token) {
      dubTokenRef.current = token
      a.src = seg.audioUrl
      a.playbackRate = speed
      a.volume = vol
      a.currentTime = wantTime
      void a.play().catch(() => { /* autoplay policy */ })
      return
    }

    a.playbackRate = speed
    a.volume = vol
    if (Math.abs(a.currentTime - wantTime) > 0.18) a.currentTime = wantTime
    if (a.paused) void a.play().catch(() => { /* autoplay policy */ })
  }

  useEffect(() => {
    if (!aspectMenuOpen) return
    const close = (e: MouseEvent) => {
      if (aspectMenuRef.current && !aspectMenuRef.current.contains(e.target as Node)) {
        setAspectMenuOpen(false)
      }
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [aspectMenuOpen])

  const aspectLabel = ASPECT_PRESETS.find((p) => p.id === aspectId)?.label ?? 'Bản gốc'

  function seek(segment: Segment) {
    const video = videoRef.current
    setSelectedId(segment.id)
    if (!video) return
    video.currentTime = segment.start
    setTime(segment.start)
    void video.play().catch(() => { /* requires explicit user gesture */ })
  }

  function beginDrag(event: ReactPointerEvent, segment: Segment, mode: 'move' | 'start' | 'end') {
    if (busy) return
    event.preventDefault()
    event.stopPropagation()
    setSelectedId(segment.id)
    const original = { start: segment.start, end: segment.end }
    const index = segments.findIndex((s) => s.id === segment.id)
    const before = index > 0 ? segments[index - 1] : undefined
    const after = index >= 0 ? segments[index + 1] : undefined
    const gap = 0.04
    const minDuration = 0.15

    const update = (move: PointerEvent) => {
      const delta = (move.clientX - event.clientX) / pxPerSec
      let start = original.start
      let end = original.end
      if (mode === 'move') {
        const lower = (before?.end ?? 0) + gap
        const upper = (after?.start ?? timelineDuration) - gap - (original.end - original.start)
        start = Math.max(lower, Math.min(upper, original.start + delta))
        end = start + (original.end - original.start)
      } else if (mode === 'start') {
        start = Math.max((before?.end ?? 0) + gap, Math.min(original.end - minDuration, original.start + delta))
      } else {
        end = Math.min(
          (after?.start ?? timelineDuration) - gap,
          Math.max(original.start + minDuration, original.end + delta),
        )
      }
      const next = { id: segment.id, start: Math.max(0, start), end: Math.min(timelineDuration, end) }
      draftRef.current = next
      setDraft(next)
    }

    const commit = () => {
      window.removeEventListener('pointermove', update)
      window.removeEventListener('pointerup', commit)
      const current = draftRef.current
      draftRef.current = null
      setDraft(null)
      if (
        current?.id === segment.id &&
        (Math.abs(current.start - original.start) > 0.001 || Math.abs(current.end - original.end) > 0.001)
      ) {
        onChange({ ...segment, start: current.start, end: current.end })
        if (videoRef.current) videoRef.current.currentTime = current.start
        setTime(current.start)
      }
    }
    window.addEventListener('pointermove', update)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function beginScrub(event: ReactPointerEvent<HTMLElement>) {
    if (busy) return
    const scroller = tracksScrollRef.current
    const col = tracksColRef.current
    const video = videoRef.current
    if (!scroller || !col || !video) return
    event.preventDefault()
    const colLeft = col.getBoundingClientRect().left
    const update = (clientX: number) => {
      const px = clientX - colLeft + scroller.scrollLeft
      const nextTime = Math.max(0, Math.min(timelineDuration, px / pxPerSec))
      video.currentTime = nextTime
      setTime(nextTime)
      const current = segmentAt(segments, nextTime)
      if (current) setSelectedId(current.id)
    }
    update(event.clientX)
    const move = (pointer: PointerEvent) => update(pointer.clientX)
    const commit = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', commit)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function beginBboxDrag(
    event: ReactPointerEvent,
    mode: 'move' | 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w',
  ) {
    if (!selected || busy || tool === 'text') return
    const canvas = canvasRef.current
    if (!canvas) return
    event.preventDefault()
    event.stopPropagation()
    setPropTab('mask')
    setTool('cover')
    const rect = canvas.getBoundingClientRect()
    const overDrag = overCoverMode && !!selected.translation.trim()
    const original = overDrag
      ? (bboxDraft ?? (selected.bbox ? clampCoverBox(selected.bbox, sourceWidth, sourceHeight) : null) ?? resolveSegmentCover(selected, settings, sourceWidth, sourceHeight) ?? fallbackBox)
      : clampCoverBox(selected.bbox ?? fallbackBox, sourceWidth, sourceHeight)
    const minSize = 12
    setDraggingBox(true)
    setSnapGuides({ h: false, v: false })

    const clipBox = (left: number, top: number, right: number, bottom: number): PixelBox => {
      const x = Math.max(0, Math.min(sourceWidth - minSize, left))
      const y = Math.max(0, Math.min(sourceHeight - minSize, top))
      const w = Math.max(minSize, Math.min(sourceWidth - x, right - left))
      const h = Math.max(minSize, Math.min(sourceHeight - y, bottom - top))
      return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
    }

    const update = (move: PointerEvent) => {
      const dx = ((move.clientX - event.clientX) / rect.width) * crop.w
      const dy = ((move.clientY - event.clientY) / rect.height) * crop.h
      let left = original.x, top = original.y
      let right = original.x + original.w, bottom = original.y + original.h
      if (mode === 'move') {
        left = Math.max(0, Math.min(sourceWidth - original.w, original.x + dx))
        top = Math.max(0, Math.min(sourceHeight - original.h, original.y + dy))
        right = left + original.w; bottom = top + original.h
      } else {
        if (mode.includes('w')) left = Math.max(0, Math.min(right - minSize, original.x + dx))
        if (mode.includes('e')) right = Math.min(sourceWidth, Math.max(left + minSize, right + dx))
        if (mode.includes('n')) top = Math.max(0, Math.min(bottom - minSize, original.y + dy))
        if (mode.includes('s')) bottom = Math.min(sourceHeight, Math.max(top + minSize, bottom + dy))
      }
      let next = clipBox(left, top, right, bottom)
      if (mode === 'move') {
        const snapped = snapBoxToCenter(next, sourceWidth, sourceHeight)
        next = snapped.box
        setSnapGuides(snapped.guides)
      } else {
        setSnapGuides({ h: false, v: false })
      }
      bboxDraftRef.current = next; setBboxDraft(next)
    }

    const commit = () => {
      window.removeEventListener('pointermove', update)
      window.removeEventListener('pointerup', commit)
      setDraggingBox(false)
      setSnapGuides({ h: false, v: false })
      const next = bboxDraftRef.current
      bboxDraftRef.current = null; setBboxDraft(null)
      if (next) {
        const norm = clampCoverBox(next, sourceWidth, sourceHeight)
        if (overDrag && selected) {
          const layout = manualCoverLayout(norm, selected.translation, selectedFontPx, sourceWidth, sourceHeight, true)
          onChange(segmentWithLayout(selected, layout, selectedFontPx))
        } else {
          onChange({ ...selected, bbox: norm })
        }
      }
    }
    window.addEventListener('pointermove', update)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function beginOverlayDrag(event: ReactPointerEvent, overlay: TextOverlay) {
    if (busy || tool === 'text') return
    const canvas = canvasRef.current
    if (!canvas) return
    event.preventDefault()
    const rect = canvas.getBoundingClientRect()
    const original = { x: overlay.x, y: overlay.y }
    setSelectedOverlayId(overlay.id)

    const update = (move: PointerEvent) => {
      const dx = ((move.clientX - event.clientX) / rect.width) * crop.w
      const dy = ((move.clientY - event.clientY) / rect.height) * crop.h
      onOverlayChange({
        ...overlay,
        x: Math.round(Math.max(0, Math.min(sourceWidth - overlay.w, original.x + dx))),
        y: Math.round(Math.max(0, Math.min(sourceHeight - overlay.h, original.y + dy))),
      })
    }
    const commit = () => { window.removeEventListener('pointermove', update); window.removeEventListener('pointerup', commit) }
    window.addEventListener('pointermove', update)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function applyFontSize(scope: 'one' | 'all') {
    const size = fontSizeDraft
    const relayout = (seg: Segment): Segment => {
      if (!seg.translation.trim() || !(settings.coverHardsubs && settings.burnSubs)) {
        return { ...seg, fontSize: size, captionLayout: null }
      }
      const fontPx = resolveCaptionFontSize({ ...seg, fontSize: size }, settings, sourceWidth, sourceHeight)
      const base = seg.bbox
        ? clampCoverBox(seg.bbox, sourceWidth, sourceHeight)
        : resolveSegmentCover(seg, settings, sourceWidth, sourceHeight)
          ?? fallbackCoverBox(sourceWidth, sourceHeight, fontPx)
      const layout = adaptiveCoverLayout(base, seg.translation, fontPx, sourceWidth, sourceHeight)
      return segmentWithLayout({ ...seg, fontSize: size, captionLayout: null }, layout, fontPx)
    }
    if (scope === 'one') {
      if (selected) onChange(relayout(selected))
      return
    }
    for (const seg of segments) onChange(relayout(seg))
  }

  async function previewTts() {
    if (!selected || ttsBusy) return
    setTtsBusy(true); setTtsError(null)
    pauseDubAudio()
    try {
      const voice = selected.voice || settings.defaultVoice
      const result = await api.previewTts(projectId, selected.id, {
        text: selected.translation,
        voice,
        lang: settings.targetLang === 'none' ? 'vi' : settings.targetLang,
      })
      onChange({ ...selected, audioUrl: result.audioUrl, audioDuration: result.duration })
      audioRef.current?.pause()
      audioRef.current = new Audio(result.audioUrl)
      await audioRef.current.play()
    } catch (error) {
      setTtsError(error instanceof Error ? error.message : 'Không thể nghe TTS')
    } finally {
      setTtsBusy(false)
    }
  }

  function playSegmentDub(seg: Segment) {
    setSelectedId(seg.id)
    setPropTab('audio')
    const video = videoRef.current
    if (!video) return
    video.currentTime = seg.start
    setTime(seg.start)
    void video.play().catch(() => { /* requires gesture */ })
  }

  function addTextOverlay(clientX?: number, clientY?: number) {
    const rect = canvasRef.current?.getBoundingClientRect()
    const x = rect && clientX !== undefined
      ? crop.x + Math.max(0, Math.min(crop.w * 0.85, ((clientX - rect.left) / rect.width) * crop.w))
      : crop.x + crop.w * 0.25
    const y = rect && clientY !== undefined
      ? crop.y + Math.max(0, Math.min(crop.h * 0.85, ((clientY - rect.top) / rect.height) * crop.h))
      : crop.y + crop.h * 0.2
    const overlay: TextOverlay = {
      id: crypto.randomUUID(), start: time, end: Math.min(timelineDuration, time + 3),
      text: 'Nhập nội dung',
      x: Math.round(x), y: Math.round(y),
      w: Math.round(sourceWidth * 0.5), h: Math.round(sourceHeight * 0.12),
      fontSize: 42, color: '#ffffff',
    }
    setSelectedOverlayId(overlay.id); setTool('select')
    onOverlayChange(overlay, true)
  }

  function togglePlay() {
    const video = videoRef.current
    if (!video) return
    if (video.paused) {
      void video.play().catch(() => { /* requires gesture */ })
    } else {
      video.pause()
      pauseDubAudio()
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void previewRef.current?.requestFullscreen()
  }

  /* Keyboard shortcuts (OpenCut-style). No dependency array on purpose:
     re-registering each render keeps every closure fresh (time, segments, selection). */
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement)?.matches('input, textarea, select')) return
      const video = videoRef.current

      const seekTo = (next: number) => {
        if (!video) return
        const clamped = Math.max(0, Math.min(timelineDuration, next))
        video.currentTime = clamped
        setTime(clamped)
        const current = segmentAt(segments, clamped)
        if (current) setSelectedId(current.id)
      }
      const seekBy = (delta: number) => { if (video) seekTo(video.currentTime + delta) }
      const stepSegment = (dir: -1 | 1) => {
        const index = segments.findIndex((s) => s.id === selected?.id)
        const next = segments[index + dir]
        if (next) { setSelectedId(next.id); seekTo(next.start) }
      }

      switch (event.code) {
        case 'Space':
        case 'KeyK':
          event.preventDefault()
          if (video) void (video.paused ? video.play() : video.pause())
          break
        case 'KeyJ': event.preventDefault(); seekBy(-5); break
        case 'KeyL': event.preventDefault(); seekBy(5); break
        case 'ArrowLeft':  event.preventDefault(); seekBy(event.shiftKey ? -1 : -1 / 30); break
        case 'ArrowRight': event.preventDefault(); seekBy(event.shiftKey ? 1 : 1 / 30); break
        case 'ArrowUp':    event.preventDefault(); stepSegment(-1); break
        case 'ArrowDown':  event.preventDefault(); stepSegment(1); break
        case 'Home': event.preventDefault(); seekTo(0); break
        case 'End':  event.preventDefault(); seekTo(timelineDuration); break
        case 'KeyT':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); addTextOverlay() }
          break
        case 'KeyF':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); toggleFullscreen() }
          break
        case 'Escape':
          setSelectedOverlayId(null); setTool('select')
          break
        case 'Delete':
        case 'Backspace':
          if (selectedOverlayId) {
            event.preventDefault()
            onOverlayDelete(selectedOverlayId)
            setSelectedOverlayId(null)
          }
          break
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  })

  /* Effective properties tab: overlay tab only valid while an overlay is selected */
  const effectivePropTab: PropTab = propTab === 'overlay' && !selectedOverlay ? 'caption' : propTab
  const isOverlaySeg = selected?.layout === 'vertical' || selected?.layout === 'label'
  const dubOn = isOverlaySeg ? selected?.dub === true : selected?.dub !== false
  const activeCaptionPx = resolveCaptionFontSize(active ?? undefined, settings, sourceWidth, sourceHeight)
  const placement = captionPlacement(settings)
  const activeCaptionBox =
    settings.burnSubs && active?.translation.trim() && settings.targetLang !== 'none' && placement !== 'over'
      ? estimatePreviewCaptionBox(
          activeOcrBox,
          active.translation,
          activeCaptionPx,
          sourceWidth,
          sourceHeight,
          crop,
          placement,
        )
      : null
  const showCoverBlur = settings.coverHardsubs && settings.burnSubs
  const coverMaskStyle = settings.coverMaskStyle ?? 'blur'
  const coverMaskColor = settings.coverMaskColor ?? '#4c1d95'
  const coverMaskOpacity = settings.coverMaskOpacity ?? 40
  const fontSizeOptions = FONT_SIZES.includes(fontSizeDraft) || fontSizeDraft === 0
    ? FONT_SIZES
    : [...FONT_SIZES, fontSizeDraft].sort((a, b) => a - b)

  function commitCoverBox(patch: Partial<PixelBox>) {
    if (!selected) return
    const display: PixelBox = { ...selectedBoxSource, ...patch }
    const norm = clampCoverBox(display, sourceWidth, sourceHeight)
    if (overCoverMode && selected.translation.trim()) {
      const layout = manualCoverLayout(norm, selected.translation, selectedFontPx, sourceWidth, sourceHeight, true)
      onChange(segmentWithLayout(selected, layout, selectedFontPx))
      return
    }
    onChange({ ...selected, bbox: norm, captionLayout: null })
  }

  async function handleExport() {
    if (busy) return
    const payload = buildExportSegments(segments, settings, sourceWidth, sourceHeight)
    await Promise.resolve(onExport(payload))
  }

  const PROP_TABS: { key: PropTab; label: string; icon: React.ReactNode; hidden?: boolean }[] = [
    {
      key: 'caption', label: 'Phụ đề',
      icon: <TabSvg><polyline points="4 7 4 4 20 4 20 7" /><line x1="9" y1="20" x2="15" y2="20" /><line x1="12" y1="4" x2="12" y2="20" /></TabSvg>,
    },
    {
      key: 'video', label: 'Video',
      icon: <TabSvg><rect x="2" y="2" width="20" height="20" rx="2.18" /><path d="M7 2v20M17 2v20M2 12h20M2 7h5M2 17h5M17 17h5M17 7h5" /></TabSvg>,
    },
    {
      key: 'audio', label: 'Âm thanh',
      icon: <TabSvg><path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3" /></TabSvg>,
    },
    {
      key: 'mask', label: 'Vùng che chữ',
      icon: <TabSvg><rect x="4" y="4" width="16" height="16" rx="1" strokeDasharray="3 3" /></TabSvg>,
    },
    {
      key: 'overlay', label: 'Text overlay', hidden: !selectedOverlay,
      icon: <TabSvg><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></TabSvg>,
    },
  ]

  return (
    <div className="live-preview-editor-root bg-background text-foreground flex h-full min-h-0 w-full flex-col overflow-hidden">

      {/* ── Header — OpenCut EditorHeader: h-[3.4rem] px-3 pt-0.5 ── */}
      <header className="bg-background flex h-12 shrink-0 items-center justify-between px-3">
        <div className="flex items-center gap-1 min-w-0">
          <button
            type="button"
            className="flex items-center justify-center rounded-sm size-8 p-1 hover:bg-accent hover:text-accent-foreground transition-colors shrink-0"
            onClick={onBack}
            title="Thoát editor"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M19 12H5M12 5l-7 7 7 7" />
            </svg>
          </button>
          <span className="text-[0.9rem] h-8 px-2 py-1 rounded-sm truncate max-w-[240px] hover:bg-accent hover:text-accent-foreground cursor-default">
            Video Clone Studio
          </span>
        </div>
        <nav className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-muted-foreground">{busy ? 'Đang xử lý…' : 'Đã lưu'}</span>
          <button
            type="button"
            className="h-8 px-4 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick={handleExport}
            disabled={busy}
          >
            Xuất bản
          </button>
        </nav>
      </header>

      {/* ── Editor layout — vertical gap-[0.18rem], panels rounded-sm ── */}
      <div className="min-h-0 min-w-0 flex-1">
        <ResizablePanelGroup direction="vertical" className="size-full">

          {/* Main content: Assets | Preview | Properties */}
          <ResizablePanel id="main" defaultSize={72} minSize={45} maxSize={88} className="min-h-0">
            <ResizablePanelGroup direction="horizontal" className="size-full px-2">

              {/* ── LEFT: Assets panel — vertical icon rail + view (OpenCut AssetsPanel) ── */}
              <ResizablePanel id="tools" defaultSize={25} minSize={12} maxSize={45} className="min-w-0 pr-1">
                <div className="panel bg-background flex h-full rounded-sm border border-border overflow-hidden">

                  {/* Icon tab rail */}
                  <div className="scrollbar-hidden flex p-1 flex-col items-center justify-start gap-0.5 overflow-y-auto shrink-0">
                    {ASSET_TABS.map((tab) => (
                      <button
                        key={tab.key}
                        type="button"
                        aria-label={tab.label}
                        title={tab.label}
                        className={cn(
                          'flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors',
                          assetsTab === tab.key
                            ? 'bg-accent text-accent-foreground'
                            : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                        )}
                        onClick={() => setAssetsTab(tab.key)}
                      >
                        {tab.icon}
                      </button>
                    ))}
                  </div>

                  <div className="w-px bg-border shrink-0" />

                  {/* Active view */}
                  <div className="flex-1 overflow-hidden">
                    {assetsTab === 'media' && (
                      <PanelView title="Media">
                        <div className="flex flex-col gap-0.5">
                          {segments.map((segment) => (
                            <button
                              key={segment.id}
                              type="button"
                              className={cn(
                                'w-full text-left rounded-sm px-2 py-1.5 text-[11px] transition-colors',
                                segment.id === selected?.id
                                  ? 'bg-secondary text-secondary-foreground'
                                  : 'hover:bg-accent text-muted-foreground hover:text-accent-foreground',
                              )}
                              onClick={() => seek(segment)}
                            >
                              <div className="font-semibold tabular-nums">
                                #{String(segment.index).padStart(2, '0')}
                                <span className="ml-1.5 font-normal opacity-60">{formatTime(segment.start)}</span>
                              </div>
                              <div className="mt-0.5 line-clamp-2 leading-relaxed opacity-75">{segment.translation || '(chưa dịch)'}</div>
                            </button>
                          ))}
                        </div>
                      </PanelView>
                    )}

                    {assetsTab === 'text' && (
                      <PanelView title="Text">
                        {/* Add-text card, like OpenCut TextView's "Default text" */}
                        <button
                          type="button"
                          className="w-full h-16 rounded-md bg-accent hover:bg-muted transition-colors flex items-center justify-center text-xl font-semibold text-foreground mb-2"
                          onClick={() => addTextOverlay()}
                        >
                          Default text
                        </button>
                        <div className="flex flex-col gap-0.5">
                          {overlays.map((overlay) => (
                            <div
                              key={overlay.id}
                              className={cn(
                                'flex items-center gap-1 rounded-sm px-2 py-1.5 text-[11px] cursor-pointer transition-colors',
                                overlay.id === selectedOverlayId
                                  ? 'bg-secondary text-secondary-foreground'
                                  : 'hover:bg-accent text-muted-foreground hover:text-accent-foreground',
                              )}
                              onClick={() => { setSelectedOverlayId(overlay.id); setPropTab('overlay') }}
                            >
                              <span className="flex-1 truncate">{overlay.text}</span>
                              <span className="tabular-nums opacity-60 shrink-0">{formatTime(overlay.start)}</span>
                              <button
                                type="button"
                                className="shrink-0 p-0.5 rounded hover:text-destructive"
                                title="Xóa"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  onOverlayDelete(overlay.id)
                                  if (selectedOverlayId === overlay.id) setSelectedOverlayId(null)
                                }}
                              >
                                <TabSvg><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /></TabSvg>
                              </button>
                            </div>
                          ))}
                          {overlays.length === 0 && (
                            <p className="text-muted-foreground text-[11px] px-2 py-1">Chưa có text overlay nào.</p>
                          )}
                        </div>
                      </PanelView>
                    )}

                    {assetsTab === 'captions' && (
                      <PanelView title="Captions">
                        <div className="flex flex-col gap-0.5">
                          {segments.map((segment) => (
                            <button
                              key={segment.id}
                              type="button"
                              className={cn(
                                'w-full text-left rounded-sm px-2 py-1.5 text-[11px] transition-colors',
                                segment.id === selected?.id
                                  ? 'bg-secondary text-secondary-foreground'
                                  : 'hover:bg-accent text-muted-foreground hover:text-accent-foreground',
                              )}
                              onClick={() => seek(segment)}
                            >
                              <span className="tabular-nums opacity-60 mr-1.5">{formatTime(segment.start)}</span>
                              {segment.translation || '(chưa dịch)'}
                            </button>
                          ))}
                        </div>
                      </PanelView>
                    )}

                    {!['media', 'text', 'captions'].includes(assetsTab) && (
                      <div className="text-muted-foreground p-4 text-sm">
                        {ASSET_TABS.find((t) => t.key === assetsTab)?.label} sắp ra mắt...
                      </div>
                    )}
                  </div>
                </div>
              </ResizablePanel>

              <ResizableHandle withHandle />

              {/* ── CENTER: Preview panel (OpenCut PreviewPanel) ── */}
              <ResizablePanel id="preview" defaultSize={50} minSize={25} className="min-h-0 min-w-0 px-1">
                <div ref={previewRef} className="panel bg-background relative flex size-full min-h-0 min-w-0 flex-col rounded-sm border border-border overflow-hidden">

                  {/* Viewport — canvas fit trong panel, overlay bám pixel video */}
                  <div className="flex-1 min-h-0 w-full flex items-center justify-center px-3 pt-2 overflow-hidden">
                    <div
                      ref={canvasRef}
                      className={cn(
                        '@container relative h-full w-auto max-h-full max-w-full shadow-lg [container-type:size]',
                        tool === 'text' ? 'cursor-crosshair' : tool === 'cover' ? 'cursor-cell' : 'cursor-default',
                      )}
                      style={{ aspectRatio: `${crop.w} / ${crop.h}` }}
                      onPointerDown={(event) => {
                        if (tool === 'text') addTextOverlay(event.clientX, event.clientY)
                      }}
                    >
                      <div className="absolute inset-0 overflow-hidden bg-black">
                        <video
                          ref={videoRef}
                          className="absolute max-w-none pointer-events-none select-none"
                          style={videoCropStyle(sourceWidth, sourceHeight, crop)}
                          src={videoUrl}
                          controls={false}
                          playsInline
                          onPlay={() => {
                            setPlaying(true)
                            syncDubAudio(videoRef.current?.currentTime ?? time, true)
                          }}
                          onPause={() => {
                            setPlaying(false)
                            pauseDubAudio()
                          }}
                          onLoadedMetadata={(event) => {
                            const { duration: mediaDuration, videoWidth, videoHeight } = event.currentTarget
                            setDuration(mediaDuration)
                            if (videoWidth > 0 && videoHeight > 0) setVideoSize({ width: videoWidth, height: videoHeight })
                          }}
                          onTimeUpdate={(event) => {
                            const current = event.currentTarget.currentTime
                            setTime(current)
                            const now = segmentAt(segments, current)
                            if (now) setSelectedId(now.id)
                            event.currentTarget.playbackRate = now?.videoSpeed ?? 1
                            syncDubAudio(current, !event.currentTarget.paused)
                          }}
                          onSeeked={(event) => {
                            const current = segmentAt(segments, event.currentTarget.currentTime)
                            if (current) setSelectedId(current.id)
                          }}
                        />

                      {/* Snap guides — căn giữa ngang/dọc khi kéo khung (CapCut-style) */}
                      {draggingBox && (snapGuides.v || snapGuides.h) && (
                        <div className="absolute inset-0 z-[15] pointer-events-none" aria-hidden>
                          {snapGuides.v && (
                            <div className="absolute inset-y-0 left-1/2 w-0 -translate-x-1/2 border-l-[1.5px] border-dashed border-fuchsia-400/95 shadow-[0_0_8px_rgba(232,121,249,0.55)]" />
                          )}
                          {snapGuides.h && (
                            <div className="absolute inset-x-0 top-1/2 h-0 -translate-y-1/2 border-t-[1.5px] border-dashed border-fuchsia-400/95 shadow-[0_0_8px_rgba(232,121,249,0.55)]" />
                          )}
                          {(snapGuides.v && snapGuides.h) && (
                            <div className="absolute left-1/2 top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fuchsia-400/90 ring-2 ring-fuchsia-300/50" />
                          )}
                        </div>
                      )}

                      {/* Blur che chữ cũ trong crop (9:16) — không chặn kéo */}
                      {selected && showCoverBlur && maskBox && tool !== 'text' && (
                        <div
                          className="absolute z-[9] pointer-events-none overflow-hidden"
                          style={{
                            ...sourceToDisplayStyle(maskBox, crop),
                            ...coverMaskPreviewStyle(coverMaskStyle, coverMaskColor, coverMaskOpacity),
                          }}
                          aria-hidden
                        />
                      )}

                      {/* Bbox kéo tự do — luôn theo bbox nguồn */}
                      {selected && tool !== 'text' && (
                        <div
                          className={cn(
                            'absolute border-2 border-violet-400 cursor-move z-10 overflow-hidden',
                            !showCoverBlur && 'bg-violet-900/10 border-dashed',
                            draggingBox && 'opacity-80 ring-2 ring-violet-300',
                            (tool === 'cover' || effectivePropTab === 'mask') && 'border-yellow-400 ring-1 ring-yellow-400/50',
                          )}
                          style={{
                            ...sourceToDisplayStyle(selectedBox, crop),
                            // bản gốc / không có maskLayer: blur ngay trên khung kéo
                            ...(!maskBox && showCoverBlur
                              ? coverMaskPreviewStyle(coverMaskStyle, coverMaskColor, coverMaskOpacity)
                              : {}),
                          }}
                          onPointerDown={(e) => beginBboxDrag(e, 'move')}
                        >
                          {(['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const).map((handle) => (
                            <span
                              key={handle}
                              className={cn(
                                'absolute w-3.5 h-3.5 rounded-sm bg-white border-2 border-violet-500 shadow-sm z-20 touch-none',
                                handle === 'nw' && 'top-[-6px] left-[-6px] cursor-nwse-resize',
                                handle === 'n'  && 'top-[-6px] left-[calc(50%-6px)] cursor-ns-resize',
                                handle === 'ne' && 'top-[-6px] right-[-6px] cursor-nesw-resize',
                                handle === 'e'  && 'top-[calc(50%-6px)] right-[-6px] cursor-ew-resize',
                                handle === 'se' && 'bottom-[-6px] right-[-6px] cursor-nwse-resize',
                                handle === 's'  && 'bottom-[-6px] left-[calc(50%-6px)] cursor-ns-resize',
                                handle === 'sw' && 'bottom-[-6px] left-[-6px] cursor-nesw-resize',
                                handle === 'w'  && 'top-[calc(50%-6px)] left-[-6px] cursor-ew-resize',
                              )}
                              onPointerDown={(e) => { e.stopPropagation(); beginBboxDrag(e, handle) }}
                            />
                          ))}
                          {(effectivePropTab === 'mask' || draggingBox) && (
                            <span className="absolute -top-5 left-0 bg-violet-600/90 text-white text-[10px] px-1.5 py-0.5 rounded pointer-events-none whitespace-nowrap z-30">
                              Vùng che · kéo góc/cạnh tự do
                            </span>
                          )}
                        </div>
                      )}

                      {/* Phụ đề dịch — over: ô caption riêng, font scale theo ô đó */}
                      {activeOverLayout && active && (
                        <div
                          className="@container [container-type:size] absolute z-20 pointer-events-none flex items-center justify-center"
                          style={sourceToDisplayStyle(activeOverLayout.caption, crop)}
                        >
                          <p
                            className={cn(
                              'w-full text-center text-white font-bold drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)]',
                              activeOverLayout.lines.length === 1 && 'whitespace-nowrap',
                            )}
                            style={{
                              ...captionFontStyle(
                                activeCaptionPx,
                                activeOverLayout.lines.length === 1
                                  ? activeOverLayout.caption.w
                                  : activeOverLayout.caption.h,
                                activeOverLayout.lines.length === 1 ? 'w' : 'h',
                              ),
                              lineHeight: 1.12,
                            }}
                          >
                            {activeOverLayout.lines.length === 1
                              ? active.translation
                              : activeOverLayout.lines.map((line, i) => (
                                <span key={i} className="whitespace-nowrap">{i > 0 && <br />}{line}</span>
                              ))}
                          </p>
                        </div>
                      )}
                      {activeCaptionBox && active && (
                        <div
                          className="@container [container-type:size] absolute z-20 pointer-events-none flex items-center justify-center border border-dashed border-emerald-400/70 bg-black/35"
                          style={sourceToDisplayStyle(activeCaptionBox, crop)}
                        >
                          <p
                            className="w-full text-center text-white font-bold leading-none drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)]"
                            style={captionFontStyle(activeCaptionPx, activeCaptionBox.h)}
                          >
                            {active.translation}
                          </p>
                        </div>
                      )}

                      {/* Text overlays */}
                      {activeOverlays.map((overlay) => (
                        <div
                          key={overlay.id}
                          className={cn(
                            '@container [container-type:size] absolute cursor-move overflow-visible',
                            overlay.id === selectedOverlayId && 'ring-1 ring-yellow-300',
                          )}
                          style={sourceToDisplayStyle(overlay, crop)}
                          onPointerDown={(e) => beginOverlayDrag(e, overlay)}
                        >
                          {overlay.id === selectedOverlayId && (
                            <span className="absolute -top-5 left-0 bg-violet-600 text-white text-[10px] px-1 rounded">⠿ drag</span>
                          )}
                          <textarea
                            className="block w-full h-full bg-transparent outline-none resize-none text-center font-extrabold cursor-move"
                            style={{
                              ...captionFontStyle(overlay.fontSize, overlay.h),
                              color: overlay.color,
                              textShadow: '0 2px 4px #000',
                              lineHeight: 1.25,
                              border: overlay.id === selectedOverlayId ? '1px dashed #ffd166' : '1px dashed transparent',
                            }}
                            value={overlay.text}
                            onPointerDown={(e) => beginOverlayDrag(e, overlay)}
                            onFocus={() => setSelectedOverlayId(overlay.id)}
                            onChange={(e) => onOverlayChange({ ...overlay, text: e.target.value })}
                          />
                        </div>
                      ))}

                      </div>
                    </div>
                  </div>

                  {/* Preview toolbar — OpenCut: grid-cols-[1fr_auto_1fr] pb-3 pt-5 px-5 */}
                  <div className="grid grid-cols-[1fr_auto_1fr] items-center pb-2 pt-2 px-4 shrink-0">
                    {/* Left: timecode */}
                    <div className="flex items-center">
                      <span className="font-mono text-xs tabular-nums">{formatTimecode(time)}</span>
                      <span className="text-muted-foreground px-2 font-mono text-xs">/</span>
                      <span className="text-muted-foreground font-mono text-xs tabular-nums">{formatTimecode(timelineDuration)}</span>
                    </div>

                    {/* Center: play/pause */}
                    <button
                      type="button"
                      className="flex h-8 w-8 items-center justify-center rounded-md text-foreground hover:bg-accent transition-colors"
                      onClick={togglePlay}
                      title="Phát / dừng (Space hoặc K)"
                    >
                      {playing
                        ? <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
                        : <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M8 5.14v13.72c0 .83.9 1.34 1.61.9l10.9-6.86a1.05 1.05 0 0 0 0-1.8L9.61 4.24A1.05 1.05 0 0 0 8 5.14Z" /></svg>
                      }
                    </button>

                    {/* Right: tools + fullscreen */}
                    <div className="justify-self-end flex items-center gap-2.5">
                      <div className="flex items-center gap-0.5">
                        {(['select', 'cover', 'text'] as const).map((t) => (
                          <button
                            key={t}
                            type="button"
                            title={t === 'select' ? 'Chọn / kéo thả' : t === 'cover' ? 'Vùng che chữ' : 'Chèn text (click lên video)'}
                            className={cn(
                              'flex h-8 w-8 items-center justify-center rounded-md transition-colors',
                              tool === t
                                ? 'bg-accent text-accent-foreground'
                                : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                            )}
                            onClick={() => setTool(t)}
                          >
                            {t === 'select' && <TabSvg><path d="m3 3 7.07 16.97 2.51-7.39 7.39-2.51L3 3z" /></TabSvg>}
                            {t === 'cover' && <TabSvg><rect x="4" y="4" width="16" height="16" rx="1" strokeDasharray="3 3" /></TabSvg>}
                            {t === 'text' && <TabSvg><polyline points="4 7 4 4 20 4 20 7" /><line x1="9" y1="20" x2="15" y2="20" /><line x1="12" y1="4" x2="12" y2="20" /></TabSvg>}
                          </button>
                        ))}
                      </div>
                      <div className="w-px h-4 bg-border" />
                      <div ref={aspectMenuRef} className="relative">
                        {aspectMenuOpen && (
                          <div className="absolute bottom-full right-0 mb-2 w-[200px] rounded-lg border border-border bg-popover py-1.5 shadow-lg text-popover-foreground text-[13px] z-50">
                            {ASPECT_PRESETS.filter((p) => p.id === 'original' || p.id === 'custom').map((preset) => {
                              const disabled = 'disabled' in preset && preset.disabled
                              return (
                                <button
                                  key={preset.id}
                                  type="button"
                                  disabled={disabled}
                                  className={cn(
                                    'flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed',
                                    aspectId === preset.id && 'text-primary',
                                  )}
                                  onClick={() => {
                                    if (disabled) return
                                    onSettings({ ...settings, previewAspectRatio: preset.id })
                                    setAspectMenuOpen(false)
                                  }}
                                >
                                  <span className="w-4 shrink-0 text-primary">
                                    {aspectId === preset.id ? '✓' : ''}
                                  </span>
                                  <span className="flex-1">{preset.label}</span>
                                </button>
                              )
                            })}
                            <div className="my-1 border-t border-border" />
                            {ASPECT_PRESETS.filter((p) => 'orient' in p && p.orient === 'landscape').map((preset) => (
                              <button
                                key={preset.id}
                                type="button"
                                className={cn(
                                  'flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent',
                                  aspectId === preset.id && 'text-primary',
                                )}
                                onClick={() => {
                                  onSettings({ ...settings, previewAspectRatio: preset.id })
                                  setAspectMenuOpen(false)
                                }}
                              >
                                <span className="w-4 shrink-0 text-primary">
                                  {aspectId === preset.id ? '✓' : ''}
                                </span>
                                <span className="flex-1">{preset.label}</span>
                                {'orient' in preset && <AspectIcon orient={preset.orient} />}
                              </button>
                            ))}
                            <div className="my-1 border-t border-border" />
                            {ASPECT_PRESETS.filter((p) => 'orient' in p && p.orient !== 'landscape').map((preset) => (
                              <button
                                key={preset.id}
                                type="button"
                                className={cn(
                                  'flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent',
                                  aspectId === preset.id && 'text-primary',
                                )}
                                onClick={() => {
                                  onSettings({ ...settings, previewAspectRatio: preset.id })
                                  setAspectMenuOpen(false)
                                }}
                              >
                                <span className="w-4 shrink-0 text-primary">
                                  {aspectId === preset.id ? '✓' : ''}
                                </span>
                                <span className="flex-1">{preset.label}</span>
                                {'orient' in preset && <AspectIcon orient={preset.orient} />}
                              </button>
                            ))}
                          </div>
                        )}
                        <button
                          type="button"
                          className={cn(
                            'flex h-8 w-8 items-center justify-center rounded-md transition-colors',
                            aspectMenuOpen
                              ? 'bg-accent text-accent-foreground'
                              : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                          )}
                          onClick={() => setAspectMenuOpen((o) => !o)}
                          title={`Tỷ lệ khung hình · ${aspectLabel}`}
                          aria-label={`Tỷ lệ khung hình · ${aspectLabel}`}
                        >
                          <TabSvg><path d="M6 3H3v3M21 3h-3M3 18v3h3M18 21h3v-3" /><rect x="7" y="7" width="10" height="10" rx="1" /></TabSvg>
                        </button>
                      </div>
                      <button
                        type="button"
                        className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/60 hover:text-foreground transition-colors"
                        onClick={toggleFullscreen}
                        title="Toàn màn hình"
                      >
                        <TabSvg><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3" /></TabSvg>
                      </button>
                    </div>
                  </div>
                </div>
              </ResizablePanel>

              <ResizableHandle withHandle />

              {/* ── RIGHT: Properties panel — icon rail + content (OpenCut PropertiesPanel) ── */}
              <ResizablePanel id="properties" defaultSize={25} minSize={15} maxSize={45} className="min-w-0 pl-1">
                {selected ? (
                  <div className="panel bg-background flex h-full overflow-hidden rounded-sm border border-border">

                    {/* Vertical tab rail */}
                    <div className="flex shrink-0 flex-col gap-0.5 border-r border-border p-1 scrollbar-hidden overflow-y-auto">
                      {PROP_TABS.filter((t) => !t.hidden).map((tab) => (
                        <button
                          key={tab.key}
                          type="button"
                          aria-label={tab.label}
                          title={tab.label}
                          className={cn(
                            'flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors',
                            effectivePropTab === tab.key
                              ? 'bg-accent text-accent-foreground'
                              : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                          )}
                          onClick={() => setPropTab(tab.key)}
                          onPointerDown={() => {
                            if (tab.key === 'mask') setTool('cover')
                          }}
                        >
                          {tab.icon}
                        </button>
                      ))}
                    </div>

                    {/* Tab content */}
                    <ScrollArea className="flex-1 scrollbar-hidden">
                      <div className="p-3 flex flex-col gap-3">
                        <div className="text-sm text-muted-foreground pb-1 border-b border-border">
                          {PROP_TABS.find((t) => t.key === effectivePropTab)?.label} — Đoạn #{String(selected.index).padStart(2, '0')}
                        </div>

                        {effectivePropTab === 'caption' && (
                          <>
                            <PropLabel label="Ngôn ngữ gốc">
                              <textarea
                                className="w-full rounded-md border border-border bg-input px-2 py-1.5 text-xs resize-none outline-none focus:border-ring"
                                value={selected.source}
                                rows={2}
                                disabled={busy}
                                onChange={(e) => onChange({ ...selected, source: e.target.value })}
                              />
                            </PropLabel>
                            <PropLabel label="Bản dịch">
                              <textarea
                                className="w-full rounded-md border border-border bg-input px-2 py-1.5 text-xs resize-none outline-none focus:border-ring"
                                value={selected.translation}
                                rows={4}
                                disabled={busy}
                                onChange={(e) => onChange({ ...selected, translation: e.target.value, captionLayout: null })}
                              />
                            </PropLabel>

                            {isOverlaySeg && (
                              <label className="flex items-center gap-2 text-xs cursor-pointer py-0.5">
                                <input
                                  type="checkbox"
                                  checked={dubOn}
                                  disabled={busy}
                                  onChange={(e) => onChange({
                                    ...selected,
                                    dub: e.target.checked,
                                    ...(e.target.checked ? {} : { audioUrl: undefined, audioFile: undefined, audioDuration: undefined }),
                                  })}
                                  className="accent-primary"
                                />
                                Lồng tiếng
                              </label>
                            )}

                            <PropLabel label="Giọng đọc">
                              <select
                                className="w-full rounded-md border border-border bg-input px-2 py-1 text-xs outline-none focus:border-ring"
                                value={selected.voice || settings.defaultVoice}
                                disabled={busy || (isOverlaySeg && !dubOn)}
                                onChange={(e) => onChange({ ...selected, voice: e.target.value, ...(isOverlaySeg ? { dub: true } : {}) })}
                              >
                                {voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                              </select>
                            </PropLabel>

                            <button
                              type="button"
                              className="w-full rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5"
                              disabled={busy || ttsBusy || !selected.translation.trim() || (isOverlaySeg && !dubOn)}
                              onClick={previewTts}
                            >
                              {ttsBusy ? 'Đang tạo TTS…' : <><IconHeadphones size={13} /> Nghe TTS</>}
                            </button>
                            {ttsError && <p className="text-xs text-destructive">{ttsError}</p>}

                            <div className="border-t border-border pt-3 flex flex-col gap-2">
                              <PropLabel label={`Cỡ chữ (xem trước ~${activeCaptionPx}px)`}>
                                <select
                                  className="w-full rounded-md border border-border bg-input px-2 py-1 text-xs outline-none focus:border-ring"
                                  value={String(fontSizeDraft)}
                                  disabled={busy || !settings.burnSubs || settings.targetLang === 'none'}
                                  onChange={(e) => setFontSizeDraft(Number(e.target.value))}
                                >
                                  <option value="0">
                                    Tự động ({AUTO_SUBTITLE_FONT}px{settings.subtitleFontSize > 0 ? ` · dự án ${settings.subtitleFontSize}px` : ''})
                                  </option>
                                  {fontSizeOptions.map((px) => (
                                    <option key={px} value={px}>{px} px</option>
                                  ))}
                                </select>
                              </PropLabel>
                              <div className="grid grid-cols-2 gap-1.5">
                                <button
                                  type="button"
                                  className="rounded-md border border-border bg-accent hover:bg-muted px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50"
                                  disabled={busy || !selected}
                                  onClick={() => applyFontSize('one')}
                                >
                                  Áp dụng đoạn này
                                </button>
                                <button
                                  type="button"
                                  className="rounded-md border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50"
                                  disabled={busy}
                                  onClick={() => applyFontSize('all')}
                                >
                                  Áp dụng tất cả
                                </button>
                              </div>
                              {(selected?.fontSize ?? 0) > 0 && (
                                <button
                                  type="button"
                                  className="text-[11px] text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                                  onClick={() => {
                                    setFontSizeDraft(0)
                                    if (selected) onChange({ ...selected, fontSize: 0, captionLayout: null })
                                  }}
                                >
                                  Reset đoạn này về tự động
                                </button>
                              )}

                              <PropLabel label="Chèn phụ đề">
                                <select
                                  className="w-full rounded-md border border-border bg-input px-2 py-1 text-xs outline-none focus:border-ring"
                                  value={
                                    settings.coverHardsubs && settings.burnSubs ? 'cover'
                                      : !settings.burnSubs ? 'none'
                                      : settings.captionPlacement === 'above' ? 'above' : 'below'
                                  }
                                  disabled={busy || settings.targetLang === 'none'}
                                  onChange={(e) => {
                                    const mode = e.target.value
                                    if (mode === 'cover') onSettings({ ...settings, coverHardsubs: true, burnSubs: true })
                                    else if (mode === 'none') onSettings({ ...settings, coverHardsubs: false, burnSubs: false })
                                    else onSettings({ ...settings, coverHardsubs: false, burnSubs: true, captionPlacement: mode as 'below' | 'above' })
                                  }}
                                >
                                  <option value="cover">Che chữ cũ + chèn dịch</option>
                                  <option value="below">Chèn dịch phía dưới</option>
                                  <option value="above">Chèn dịch phía trên</option>
                                  <option value="none">Không chèn chữ</option>
                                </select>
                              </PropLabel>
                              {showCoverBlur && (
                                <p className="text-[10px] text-muted-foreground leading-snug">
                                  Kéo khung <strong className="text-violet-400">tím</strong> trên preview phủ đúng chữ gốc.
                                  Chữ dịch căn giữa khung tím. Chi tiết ở tab <strong>Vùng che chữ</strong>.
                                </p>
                              )}
                            </div>
                          </>
                        )}

                        {effectivePropTab === 'video' && (() => {
                          const idx = segments.findIndex((s) => s.id === selected.id)
                          const prevEnd = idx > 0 ? segments[idx - 1].end : 0
                          const nextStart = segments[idx + 1]?.start ?? timelineDuration
                          const minDur = 0.15
                          return (
                            <>
                              <PropLabel label={`Tốc độ video: ${(selected.videoSpeed ?? 1).toFixed(2)}×`}>
                                <input type="range" min={0.5} max={2} step={0.05}
                                  className="w-full accent-primary"
                                  value={selected.videoSpeed ?? 1} disabled={busy}
                                  onChange={(e) => onChange({ ...selected, videoSpeed: Number(e.target.value) })}
                                />
                              </PropLabel>
                              <div className="flex gap-1">
                                {[0.5, 0.75, 1, 1.5, 2].map((v) => (
                                  <button
                                    key={v}
                                    type="button"
                                    className={cn(
                                      'flex-1 rounded-sm border px-1 py-1 text-[10px] transition-colors',
                                      (selected.videoSpeed ?? 1) === v
                                        ? 'border-primary text-primary bg-primary/10'
                                        : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
                                    )}
                                    disabled={busy}
                                    onClick={() => onChange({ ...selected, videoSpeed: v })}
                                  >
                                    {v}×
                                  </button>
                                ))}
                              </div>

                              <div className="border-t border-border pt-3 flex flex-col gap-3">
                                <NumField
                                  label="Bắt đầu (s)" value={selected.start} step={0.1} disabled={busy}
                                  onCommit={(v) => onChange({
                                    ...selected,
                                    start: Math.max(prevEnd, Math.min(selected.end - minDur, v)),
                                  })}
                                />
                                <NumField
                                  label="Kết thúc (s)" value={selected.end} step={0.1} disabled={busy}
                                  onCommit={(v) => onChange({
                                    ...selected,
                                    end: Math.min(nextStart, Math.max(selected.start + minDur, v)),
                                  })}
                                />
                                <PropLabel label="Thời lượng">
                                  <span className="text-xs tabular-nums">{(selected.end - selected.start).toFixed(2)}s</span>
                                </PropLabel>
                              </div>
                            </>
                          )
                        })()}

                        {effectivePropTab === 'audio' && (
                          <>
                            {!segmentHasDub(selected) ? (
                              <p className="text-[11px] text-muted-foreground leading-relaxed">
                                Đoạn này đang tắt lồng tiếng. Bật <strong className="text-foreground font-medium">Lồng tiếng</strong> ở tab Phụ đề.
                              </p>
                            ) : !selected.audioUrl ? (
                              <p className="text-[11px] text-muted-foreground leading-relaxed">
                                Chưa có audio. Bấm <strong className="text-foreground font-medium">Lồng tiếng</strong> trên toolbar hoặc <strong className="text-foreground font-medium">Nghe TTS</strong> để tạo clip.
                              </p>
                            ) : (
                              <>
                                <PropLabel label="Clip lồng tiếng">
                                  <span className="text-xs tabular-nums text-foreground">
                                    {(selected.audioDuration ?? 0).toFixed(2)}s · slot {(selected.end - selected.start).toFixed(2)}s
                                  </span>
                                </PropLabel>
                                <div className="flex gap-1">
                                  <button
                                    type="button"
                                    className="flex-1 rounded-md border border-border bg-accent hover:bg-muted px-2 py-1.5 text-xs transition-colors"
                                    disabled={busy}
                                    onClick={() => playSegmentDub(selected)}
                                  >
                                    Phát với timeline
                                  </button>
                                  <button
                                    type="button"
                                    className="flex-1 rounded-md border border-border bg-accent hover:bg-muted px-2 py-1.5 text-xs transition-colors disabled:opacity-50"
                                    disabled={busy || ttsBusy || !selected.translation.trim()}
                                    onClick={previewTts}
                                  >
                                    {ttsBusy ? 'Đang tạo…' : 'Tạo lại TTS'}
                                  </button>
                                </div>
                              </>
                            )}

                            <PropLabel label={`Âm lượng TTS: ${selected.ttsVolume ?? 100}%`}>
                              <input type="range" min={0} max={200}
                                className="w-full accent-primary"
                                value={selected.ttsVolume ?? 100}
                                onChange={(e) => onChange({ ...selected, ttsVolume: Number(e.target.value) })}
                              />
                            </PropLabel>
                            <div className="flex gap-1">
                              {[0, 50, 100, 150, 200].map((v) => (
                                <button
                                  key={v}
                                  type="button"
                                  className={cn(
                                    'flex-1 rounded-sm border px-1 py-1 text-[10px] transition-colors',
                                    (selected.ttsVolume ?? 100) === v
                                      ? 'border-primary text-primary bg-primary/10'
                                      : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
                                  )}
                                  onClick={() => onChange({ ...selected, ttsVolume: v })}
                                >
                                  {v === 0 ? 'Tắt' : `${v}%`}
                                </button>
                              ))}
                            </div>

                            <PropLabel label={`Tốc độ TTS: ${(selected.ttsSpeed ?? 1).toFixed(2)}×`}>
                              <input type="range" min={0.75} max={1.5} step={0.05}
                                className="w-full accent-primary"
                                value={selected.ttsSpeed ?? 1}
                                onChange={(e) => onChange({ ...selected, ttsSpeed: Number(e.target.value) })}
                              />
                            </PropLabel>
                            <div className="flex gap-1">
                              {[0.75, 0.9, 1, 1.15, 1.3, 1.5].map((v) => (
                                <button
                                  key={v}
                                  type="button"
                                  className={cn(
                                    'flex-1 rounded-sm border px-1 py-1 text-[10px] transition-colors',
                                    (selected.ttsSpeed ?? 1) === v
                                      ? 'border-primary text-primary bg-primary/10'
                                      : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
                                  )}
                                  onClick={() => onChange({ ...selected, ttsSpeed: v })}
                                >
                                  {v}×
                                </button>
                              ))}
                            </div>

                            <button
                              type="button"
                              className="w-full rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors"
                              onClick={() => onChange({ ...selected, ttsVolume: 100, ttsSpeed: 1 })}
                            >
                              Reset âm thanh mặc định
                            </button>
                          </>
                        )}

                        {effectivePropTab === 'mask' && (
                          <>
                            <p className="text-[11px] text-muted-foreground leading-relaxed">
                              Khung trên preview = vùng che chữ gốc. Xuất video dùng <strong className="text-foreground font-medium">cùng khung + kiểu mặt nạ</strong>.
                              <strong className="text-foreground font-medium"> Làm mờ</strong> = kính mờ + màu phủ;
                              nếu vẫn lộ chữ cũ, chọn <strong className="text-foreground font-medium">Khối</strong> hoặc kéo rộng khung.
                            </p>
                            <PropLabel label="Kiểu mặt nạ">
                              <div className="grid grid-cols-3 gap-1">
                                {COVER_MASK_STYLES.map(({ id, label }) => (
                                  <button
                                    key={id}
                                    type="button"
                                    disabled={busy}
                                    className={cn(
                                      'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                                      coverMaskStyle === id
                                        ? 'border-violet-400 bg-violet-500/20 text-foreground'
                                        : 'border-border bg-accent hover:bg-muted text-muted-foreground',
                                    )}
                                    onClick={() => onSettings({ ...settings, coverMaskStyle: id })}
                                  >
                                    {label}
                                  </button>
                                ))}
                              </div>
                            </PropLabel>
                            {coverMaskStyle !== 'mosaic' && (
                              <PropLabel label="Màu phủ">
                                <input
                                  type="color"
                                  className="h-9 w-full cursor-pointer rounded-md border border-border bg-input"
                                  value={coverMaskColor}
                                  disabled={busy}
                                  onChange={(e) => onSettings({ ...settings, coverMaskColor: e.target.value })}
                                />
                              </PropLabel>
                            )}
                            {coverMaskStyle === 'mosaic' && (
                              <p className="text-[10px] text-muted-foreground leading-snug">
                                Che chữ gốc bằng làm mờ nền + texture (thuật toán bbox cũ) — không dùng màu phủ.
                              </p>
                            )}
                            {coverMaskStyle !== 'mosaic' && (
                              <PropLabel label={`Độ đậm: ${coverMaskOpacity}%`}>
                                <input
                                  type="range"
                                  min={5}
                                  max={100}
                                  step={1}
                                  className="w-full accent-violet-500"
                                  value={coverMaskOpacity}
                                  disabled={busy}
                                  onChange={(e) => onSettings({ ...settings, coverMaskOpacity: Number(e.target.value) })}
                                />
                              </PropLabel>
                            )}
                            <ul className="text-[10px] text-muted-foreground space-y-1 list-disc pl-4">
                              <li>Kéo <strong>giữa</strong> khung → di chuyển vùng che</li>
                              <li>Kéo <strong>góc/cạnh</strong> (chấm trắng) → phóng to/thu nhỏ</li>
                              <li>Phụ đề dịch tự tính từ bbox + bản dịch</li>
                            </ul>
                            <div className="grid grid-cols-2 gap-2">
                              <NumField label="X" value={selectedBoxSource.x} disabled={busy}
                                onCommit={(v) => commitCoverBox({ x: Math.round(Math.max(0, Math.min(sourceWidth - selectedBoxSource.w, v))) })} />
                              <NumField label="Y" value={selectedBoxSource.y} disabled={busy}
                                onCommit={(v) => commitCoverBox({ y: Math.round(Math.max(0, Math.min(sourceHeight - selectedBoxSource.h, v))) })} />
                              <NumField label="Rộng" value={selectedBoxSource.w} disabled={busy}
                                onCommit={(v) => commitCoverBox({ w: Math.round(Math.max(12, Math.min(sourceWidth - selectedBoxSource.x, v))) })} />
                              <NumField label="Cao" value={selectedBoxSource.h} disabled={busy}
                                onCommit={(v) => commitCoverBox({
                                  h: Math.round(Math.max(12, Math.min(sourceHeight - selectedBoxSource.y, v))),
                                })} />
                            </div>
                            <p className="text-[10px] text-muted-foreground">
                              Kéo cạnh trên/dưới (hoặc nhập Cao) để chỉnh chiều cao vùng che.
                            </p>
                            <button
                              type="button"
                              className="w-full rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
                              disabled={busy || !selected.bbox}
                              onClick={() => onChange({ ...selected, bbox: null, captionLayout: null })}
                            >
                              Reset vùng OCR
                            </button>
                          </>
                        )}

                        {effectivePropTab === 'overlay' && selectedOverlay && (
                          <>
                            <PropLabel label="Nội dung">
                              <textarea
                                className="w-full rounded-md border border-border bg-input px-2 py-1.5 text-xs resize-none outline-none focus:border-ring transition-colors"
                                value={selectedOverlay.text}
                                rows={3}
                                onChange={(e) => onOverlayChange({ ...selectedOverlay, text: e.target.value })}
                              />
                            </PropLabel>

                            <div className="grid grid-cols-2 gap-2">
                              <NumField label="Hiện từ (s)" value={selectedOverlay.start} step={0.1}
                                onCommit={(v) => onOverlayChange({ ...selectedOverlay, start: Math.max(0, Math.min(selectedOverlay.end - 0.1, v)) })} />
                              <NumField label="Đến (s)" value={selectedOverlay.end} step={0.1}
                                onCommit={(v) => onOverlayChange({ ...selectedOverlay, end: Math.min(timelineDuration, Math.max(selectedOverlay.start + 0.1, v)) })} />
                              <NumField label="X" value={selectedOverlay.x}
                                onCommit={(v) => onOverlayChange({ ...selectedOverlay, x: Math.round(Math.max(0, Math.min(sourceWidth - selectedOverlay.w, v))) })} />
                              <NumField label="Y" value={selectedOverlay.y}
                                onCommit={(v) => onOverlayChange({ ...selectedOverlay, y: Math.round(Math.max(0, Math.min(sourceHeight - selectedOverlay.h, v))) })} />
                              <NumField label="Rộng" value={selectedOverlay.w}
                                onCommit={(v) => onOverlayChange({ ...selectedOverlay, w: Math.round(Math.max(20, Math.min(sourceWidth - selectedOverlay.x, v))) })} />
                              <NumField label="Cao" value={selectedOverlay.h}
                                onCommit={(v) => onOverlayChange({ ...selectedOverlay, h: Math.round(Math.max(20, Math.min(sourceHeight - selectedOverlay.y, v))) })} />
                            </div>

                            <PropLabel label={`Cỡ chữ: ${selectedOverlay.fontSize}px`}>
                              <input type="range" min={12} max={160} className="w-full accent-primary"
                                value={selectedOverlay.fontSize}
                                onChange={(e) => onOverlayChange({ ...selectedOverlay, fontSize: Number(e.target.value) })} />
                            </PropLabel>

                            <PropLabel label="Màu text">
                              <div className="flex items-center gap-1.5">
                                <input type="color" className="h-7 w-14 rounded cursor-pointer border border-border shrink-0"
                                  value={selectedOverlay.color}
                                  onChange={(e) => onOverlayChange({ ...selectedOverlay, color: e.target.value })} />
                                {['#ffffff', '#ffd166', '#ef476f', '#06d6a0', '#118ab2', '#000000'].map((c) => (
                                  <button
                                    key={c}
                                    type="button"
                                    className={cn(
                                      'h-5 w-5 rounded-full border transition-transform hover:scale-110',
                                      selectedOverlay.color === c ? 'border-primary ring-1 ring-primary' : 'border-border',
                                    )}
                                    style={{ backgroundColor: c }}
                                    title={c}
                                    onClick={() => onOverlayChange({ ...selectedOverlay, color: c })}
                                  />
                                ))}
                              </div>
                            </PropLabel>

                            <button
                              type="button"
                              className="w-full rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors"
                              onClick={() => onOverlayChange({
                                ...selectedOverlay,
                                id: crypto.randomUUID(),
                                start: Math.min(timelineDuration - 0.1, selectedOverlay.end),
                                end: Math.min(timelineDuration, selectedOverlay.end + (selectedOverlay.end - selectedOverlay.start)),
                              }, true)}
                            >
                              Nhân bản overlay
                            </button>
                            <button
                              type="button"
                              className="w-full rounded-md border border-destructive/50 text-destructive hover:bg-destructive/10 px-3 py-1.5 text-xs transition-colors"
                              onClick={() => { onOverlayDelete(selectedOverlay.id); setSelectedOverlayId(null) }}
                            >
                              Xóa text overlay
                            </button>
                          </>
                        )}
                      </div>
                    </ScrollArea>
                  </div>
                ) : (
                  <div className="panel bg-background flex h-full flex-col items-center justify-center overflow-hidden rounded-sm border border-border">
                    <p className="text-muted-foreground text-sm">Chưa chọn phần tử nào.</p>
                  </div>
                )}
              </ResizablePanel>

            </ResizablePanelGroup>
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* ── BOTTOM: Timeline panel (OpenCut Timeline) ── */}
          <ResizablePanel id="timeline" defaultSize={28} minSize={18} maxSize={45} className="min-h-0 px-2 pb-2 pt-0.5">
            <div className="panel bg-background h-full flex flex-col rounded-sm border border-border overflow-hidden">

              {/* Timeline toolbar — h-10 border-b, tools left / scene center / zoom right */}
              <div className="flex items-center justify-between h-9 border-b border-border shrink-0 px-2">
                <div className="flex items-center gap-0.5">
                  <TlButton
                    title="Xóa text overlay đã chọn (Del)"
                    disabled={!selectedOverlayId}
                    onClick={() => {
                      if (selectedOverlayId) { onOverlayDelete(selectedOverlayId); setSelectedOverlayId(null) }
                    }}
                  >
                    <TabSvg><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6M14 11v6" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton title="Thêm text overlay tại playhead (T)" onClick={() => addTextOverlay()}>
                    <TabSvg><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton
                    title={'Phím tắt:\nSpace / K — Phát / dừng\nJ / L — Tua −5s / +5s\n← / → — Lùi / tiến 1 frame (Shift = 1s)\n↑ / ↓ — Đoạn trước / sau\nHome / End — Về đầu / cuối\nT — Thêm text overlay\nF — Toàn màn hình\nDelete — Xóa overlay đang chọn\nEsc — Bỏ chọn'}
                  >
                    <TabSvg><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" /></TabSvg>
                  </TlButton>
                </div>

                {/* Scene selector lookalike (SplitButton) */}
                <div className="flex items-center border border-foreground/10 rounded-md overflow-hidden text-[11px]">
                  <span className="px-2.5 py-1 font-medium text-foreground">Main Scene</span>
                  <span className="w-px self-stretch bg-border" />
                  <span className="px-1.5 py-1 text-muted-foreground flex items-center">
                    <TabSvg><path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" /><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" /><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" /></TabSvg>
                  </span>
                </div>

                {/* Zoom controls */}
                <div className="flex items-center gap-1">
                  <TlButton title="Thu nhỏ" onClick={() => setZoom((z) => Math.max(0.5, +(z / 1.5).toFixed(2)))}>
                    <TabSvg><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="8" y1="11" x2="14" y2="11" /></TabSvg>
                  </TlButton>
                  <input
                    type="range" min={0.5} max={40} step={0.1} value={zoom}
                    className="w-28 accent-primary"
                    onChange={(e) => setZoom(Number(e.target.value))}
                  />
                  <TlButton title="Phóng to" onClick={() => setZoom((z) => Math.min(40, +(z * 1.5).toFixed(2)))}>
                    <TabSvg><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="11" y1="8" x2="11" y2="14" /><line x1="8" y1="11" x2="14" y2="11" /></TabSvg>
                  </TlButton>
                </div>
              </div>

              {/* Timeline body: labels + tracks */}
              <div className="flex flex-1 min-h-0 overflow-hidden">

                {/* Track labels — TRACK_LABELS_WIDTH_PX = 112 */}
                <div className="w-[112px] shrink-0 flex flex-col border-r border-border bg-background">
                  <div className="h-[18px] shrink-0 border-b border-border bg-background/70" />
                  <div className="h-[48px] flex items-center gap-2 px-2.5 border-b border-border shrink-0">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-muted-foreground shrink-0" aria-hidden><polygon points="5 3 19 12 5 21 5 3" /></svg>
                    <span className="text-[11px] text-muted-foreground">Video</span>
                  </div>
                  <div className="h-[25px] flex items-center gap-2 px-2.5 border-b border-border shrink-0">
                    <span className="text-[11px] text-muted-foreground leading-none">◈</span>
                    <span className="text-[11px] text-muted-foreground">Caption</span>
                  </div>
                  <div className="h-[25px] flex items-center gap-2 px-2.5 border-b border-border shrink-0">
                    <IconHeadphones size={12} className="text-muted-foreground shrink-0" />
                    <span className="text-[11px] text-muted-foreground">Lồng tiếng</span>
                  </div>
                  <div className="h-[25px] flex items-center gap-2 px-2.5 border-b border-border shrink-0">
                    <span className="text-[11px] font-semibold text-muted-foreground leading-none">T</span>
                    <span className="text-[11px] text-muted-foreground">Text</span>
                  </div>
                </div>

                {/* Ruler + tracks */}
                <div className="flex flex-col flex-1 min-w-0 relative overflow-hidden" ref={tracksColRef}>

                  {/* Ruler — 22px, scroll-synced */}
                  <div className="h-[18px] overflow-hidden shrink-0 border-b border-border bg-background/70" ref={rulerScrollRef}>
                    <div
                      className="relative h-full cursor-crosshair select-none"
                      style={{ width: trackWidth }}
                      onPointerDown={beginScrub}
                    >
                      {ticks.map((tick) => (
                        <React.Fragment key={tick}>
                          <span
                            className="absolute bottom-0 w-px h-[6px] bg-border pointer-events-none"
                            style={{ left: tick * pxPerSec }}
                          />
                          <span
                            className="absolute top-[3px] text-[9px] text-muted-foreground translate-x-[-50%] pointer-events-none whitespace-nowrap tabular-nums"
                            style={{ left: tick * pxPerSec }}
                          >
                            {formatTime(tick)}
                          </span>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>

                  {/* Master scroll area */}
                  <div
                    className="flex-1 overflow-x-auto overflow-y-hidden scrollbar-thin"
                    ref={tracksScrollRef}
                    onScroll={syncFollowers}
                  >
                    <div className="flex flex-col" style={{ width: trackWidth }}>

                      {/* Video track — filmstrip MP4 + vùng kéo segment (trong suốt) */}
                      <div
                        ref={trackRef}
                        className="relative h-[48px] border-b border-border cursor-pointer bg-black/40"
                        onPointerDown={beginScrub}
                      >
                        {videoUrl && videoSpan > 0 && (
                          <TimelineFilmstrip
                            videoUrl={videoUrl}
                            duration={videoSpan}
                            widthPx={videoSpan * pxPerSec}
                            heightPx={36}
                            className="absolute top-1.5 left-0 rounded-sm"
                          />
                        )}
                        {segments.map((segment) => {
                          const display = draft?.id === segment.id ? { ...segment, ...draft } : segment
                          const isSelected = segment.id === selected?.id
                          return (
                            <button
                              key={segment.id}
                              type="button"
                              className={cn(
                                'absolute top-1.5 h-[calc(100%-12px)] rounded-sm overflow-hidden border-0 cursor-pointer z-[1]',
                                isSelected
                                  ? 'ring-[1.5px] ring-primary shadow-[inset_0_0_0_1px_rgba(255,255,255,0.25)]'
                                  : 'ring-1 ring-white/20',
                                'bg-transparent hover:bg-white/5',
                              )}
                              style={{
                                left: display.start * pxPerSec,
                                width: Math.max(2, (display.end - display.start) * pxPerSec),
                              }}
                              onClick={() => seek(display)}
                              onPointerDown={(e) => beginDrag(e, segment, 'move')}
                            >
                              <span
                                className="absolute inset-y-0 left-0 w-2 cursor-ew-resize rounded-l hover:bg-white/25 transition-colors z-10"
                                onPointerDown={(e) => beginDrag(e, segment, 'start')}
                              />
                              {(segment.videoSpeed ?? 1) !== 1 && (
                                <em className="absolute bottom-0.5 right-2 text-[9px] text-white/80 not-italic drop-shadow z-10">
                                  {segment.videoSpeed}×
                                </em>
                              )}
                              <span
                                className="absolute inset-y-0 right-0 w-2 cursor-ew-resize rounded-r hover:bg-white/25 transition-colors z-10"
                                onPointerDown={(e) => beginDrag(e, segment, 'end')}
                              />
                            </button>
                          )
                        })}
                      </div>

                      {/* Caption track — h-[25px], teal #5DBAA0 (OpenCut text track color) */}
                      <div className="relative h-[25px] border-b border-border" style={{ backgroundColor: 'var(--background)' }}>
                        {segments.map((seg) => (
                          <button
                            key={seg.id}
                            type="button"
                            className="absolute top-1.5 h-[calc(100%-12px)] rounded-sm text-[10px] text-white whitespace-nowrap overflow-hidden px-1.5 flex items-center cursor-pointer border-0 transition-opacity hover:opacity-90"
                            style={{
                              left: seg.start * pxPerSec,
                              width: Math.max(2, (seg.end - seg.start) * pxPerSec),
                              boxSizing: 'border-box',
                              background: seg.id === selected?.id ? '#3da88a' : '#5DBAA0',
                            }}
                            onClick={() => seek(seg)}
                          >
                            {seg.translation || 'Caption'}
                          </button>
                        ))}
                      </div>

                      {/* Dub / TTS track */}
                      <div className="relative h-[25px] border-b border-border" style={{ backgroundColor: 'var(--background)' }}>
                        {segments.map((seg) => {
                          if (!segmentHasDub(seg) || !seg.audioUrl) return null
                          const clipSec = dubClipSeconds(seg)
                          const isSelected = seg.id === selected?.id
                          return (
                            <button
                              key={seg.id}
                              type="button"
                              title={`TTS ${(seg.audioDuration ?? 0).toFixed(2)}s`}
                              className={cn(
                                'absolute top-1.5 h-[calc(100%-12px)] rounded-sm text-[10px] text-white whitespace-nowrap overflow-hidden px-1.5 flex items-center cursor-pointer border-0 transition-opacity hover:opacity-90',
                                isSelected && 'ring-[1.5px] ring-amber-200',
                              )}
                              style={{
                                left: seg.start * pxPerSec,
                                width: Math.max(2, clipSec * pxPerSec),
                                boxSizing: 'border-box',
                                background: isSelected ? '#c2780a' : '#E8A045',
                              }}
                              onClick={() => {
                                setSelectedId(seg.id)
                                setPropTab('audio')
                                seek(seg)
                              }}
                            >
                              <IconHeadphones size={10} className="shrink-0 mr-0.5 opacity-90" />
                              {(seg.ttsSpeed ?? 1) !== 1 ? `${seg.ttsSpeed}×` : 'TTS'}
                            </button>
                          )
                        })}
                      </div>

                      {/* Text overlay track — h-[25px], purple #8F5DBA */}
                      <div className="relative h-[25px] border-b border-border" style={{ backgroundColor: 'var(--background)' }}>
                        {overlays.map((overlay) => (
                          <button
                            key={overlay.id}
                            type="button"
                            className={cn(
                              'absolute top-1.5 h-[calc(100%-12px)] rounded-sm border-0 text-[10px] text-white whitespace-nowrap overflow-hidden px-1.5 cursor-pointer flex items-center transition-opacity hover:opacity-90',
                              overlay.id === selectedOverlayId && 'ring-[1.5px] ring-yellow-300',
                            )}
                            style={{
                              left: overlay.start * pxPerSec,
                              width: Math.max(2, (overlay.end - overlay.start) * pxPerSec),
                              boxSizing: 'border-box',
                              background: overlay.id === selectedOverlayId ? '#7a4da0' : '#8F5DBA',
                            }}
                            onClick={() => { setSelectedOverlayId(overlay.id); setPropTab('overlay') }}
                          >
                            {overlay.text}
                          </button>
                        ))}
                      </div>

                    </div>
                  </div>

                  {/* Playhead */}
                  <div
                    className="absolute inset-y-0 w-0 z-30 pointer-events-none"
                    style={{ left: playheadPx }}
                    aria-hidden
                  >
                    <div
                      className="absolute top-0 left-1/2 -translate-x-1/2 cursor-col-resize pointer-events-auto"
                      onPointerDown={(e) => { e.stopPropagation(); beginScrub(e) }}
                    >
                      <div className="w-0 h-0" style={{
                        borderLeft: '5px solid transparent',
                        borderRight: '5px solid transparent',
                        borderTop: '8px solid hsl(200, 90%, 52%)',
                      }} />
                    </div>
                    <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-[1.5px] bg-primary opacity-90" />
                  </div>

                </div>
              </div>
            </div>
          </ResizablePanel>

        </ResizablePanelGroup>
      </div>
    </div>
  )
}

/* ── OpenCut PanelView: h-11 header with title + scrollable content ── */
function PanelView({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="relative flex h-full flex-col">
      <div className="bg-background h-11 shrink-0 pl-3 pr-2 flex items-center justify-between border-b border-border">
        <span className="text-muted-foreground text-sm">{title}</span>
      </div>
      <div className="scrollbar-hidden size-full overflow-y-auto pt-2">
        <div className="w-full flex-1 px-2 pt-0">{children}</div>
      </div>
    </div>
  )
}

function PropLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-muted-foreground font-medium">{label}</span>
      {children}
    </label>
  )
}

/* Numeric input that commits on blur/Enter (avoids re-render storms while typing).
   key={value} re-seeds defaultValue whenever the outside value changes. */
function NumField({ label, value, step = 1, disabled, onCommit }: {
  label: string
  value: number
  step?: number
  disabled?: boolean
  onCommit: (value: number) => void
}) {
  const commit = (raw: string) => {
    const parsed = Number(raw)
    if (Number.isFinite(parsed) && Math.abs(parsed - value) > 1e-9) onCommit(parsed)
  }
  return (
    <PropLabel label={label}>
      <input
        key={value}
        type="number"
        className="w-full rounded-md border border-border bg-input px-2 py-1 text-xs outline-none focus:border-ring tabular-nums"
        defaultValue={Number.isInteger(value) ? value : +value.toFixed(2)}
        step={step}
        disabled={disabled}
        onBlur={(e) => commit(e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
      />
    </PropLabel>
  )
}

function TlButton({ title, onClick, disabled, children }: {
  title: string
  onClick?: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="w-7 h-7 rounded-sm flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-40 disabled:pointer-events-none"
    >
      {children}
    </button>
  )
}
