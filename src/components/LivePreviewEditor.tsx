import React, { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { ProjectSettings, Segment, TextOverlay } from '../types'
import { api } from '../services/api'
import { IconHeadphones } from './Icons'
import { cn } from '@/lib/cn'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ScrollArea } from '@/components/ui/scroll-area'
import { fitOverlayFontPx, layoutOcrOverlay, midInsideVerticalWatermark, ocrFallbackCover } from '../lib/ocrOverlayLayout'
import { resolveCoverWindow } from '../lib/coverTiming'

type Props = {
  videoUrl: string
  /** Độ dài file nguồn (giây) */
  mediaDuration?: number
  /** Clip lần dịch (giây); >0 = chỉ làm việc trong cửa sổ đó (preview N giây) */
  workClipSec?: number
  /** workVideo đã bake chậm 0.80× (preferVideo) → không playbackRate thêm */
  bakedPreferVideo?: boolean
  /** Tốc độ đã bake vào file preview */
  bakedSpeed?: number
  projectId: string
  segments: Segment[]
  settings: ProjectSettings
  voices: { id: string; name: string }[]
  busy: boolean
  /** Tạo TTS toàn bộ (track Lồng tiếng trống → bấm) */
  onDub?: () => void
  onBack: () => void
  onChange: (segment: Segment) => void | Promise<void>
  /** Thay cả list (split / duplicate / delete caption) */
  onSegmentsReplace: (segments: Segment[]) => void | Promise<void>
  /** Sau bake tốc độ preview toàn bộ */
  onPreviewRebaked?: (res: {
    segments: Segment[]
    overlays?: TextOverlay[]
    workClipSec: number
    duration: number
    bakedPreferVideo: boolean
    bakedSpeed: number
    videoUrl: string
  }) => void
  onExport: (segments?: Segment[]) => void | Promise<void>
  onSettings: (settings: ProjectSettings) => void
  overlays: TextOverlay[]
  onOverlayChange: (overlay: TextOverlay, isNew?: boolean) => void
  onOverlayDelete: (overlayId: string) => void
  onOverlaysReplace: (overlays: TextOverlay[]) => void | Promise<void>
}

function formatTime(value: number) {
  const min = Math.floor(value / 60)
  const sec = value % 60
  return `${min}:${sec.toFixed(1).padStart(4, '0')}`
}

const BOOKMARK_EPS = 1 / 30
const MIN_CLIP_SEC = 0.15
/** Lề tối thiểu hai phía để còn cắt được (clip ngắn ~0.4s vẫn split được) */
const SPLIT_EDGE = 0.05
const ZOOM_MIN = 0.05
const ZOOM_MAX = 40
const PX_PER_SEC_BASE = 50

function fitTimelineZoom(durationSec: number, widthPx: number) {
  if (!(durationSec > 0) || !(widthPx > 0)) return 1
  const z = (widthPx - 8) / (durationSec * PX_PER_SEC_BASE)
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(z * 1000) / 1000))
}

function bookmarkKey(projectId: string) {
  return `videoclone.bookmarks.${projectId}`
}

function loadBookmarks(projectId: string): number[] {
  try {
    const raw = localStorage.getItem(bookmarkKey(projectId))
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((t): t is number => typeof t === 'number' && Number.isFinite(t)).sort((a, b) => a - b)
  } catch {
    return []
  }
}

function persistBookmarks(projectId: string, marks: number[]) {
  try {
    localStorage.setItem(bookmarkKey(projectId), JSON.stringify(marks))
  } catch {
    /* ignore */
  }
}

function reindexSegments(list: Segment[]): Segment[] {
  return [...list]
    .sort((a, b) => a.start - b.start || a.end - b.end)
    .map((s, i) => ({ ...s, index: i }))
}

/** Clip Video / Âm gốc trên timeline (tách khỏi Caption·TTS) */
type MediaClip = { id: string; start: number; end: number }

function fullMediaClip(end: number): MediaClip {
  return { id: crypto.randomUUID(), start: 0, end: Math.max(end, MIN_CLIP_SEC) }
}

/** Clamp + fill cửa sổ làm việc. 1 clip bed luôn full span (preview→full không để Âm gốc/Video ngắn cụt). */
function normalizeMediaClips(clips: MediaClip[], durationSec: number, prevDuration = 0): MediaClip[] {
  if (!(durationSec > 0)) return []
  const next = clips
    .filter((c) => c && typeof c.start === 'number' && typeof c.end === 'number' && c.end > c.start)
    .map((c) => ({
      id: String(c.id || crypto.randomUUID()),
      start: Math.max(0, Math.min(c.start, durationSec - MIN_CLIP_SEC)),
      end: Math.max(MIN_CLIP_SEC, Math.min(c.end, durationSec)),
    }))
    .filter((c) => c.end - c.start >= SPLIT_EDGE)
  if (!next.length) return [fullMediaClip(durationSec)]
  // ponytail: 1 bed = luôn khớp timeline; trim gap chỉ khi đã Split thành nhiều clip
  if (next.length === 1 && next[0].start <= SPLIT_EDGE) {
    return [{ ...next[0], start: 0, end: durationSec }]
  }
  // Nhiều clip: cửa sổ phình (15s→full) → kéo đuôi các cạnh cũ
  if (prevDuration > 0 && durationSec > prevDuration + 0.25) {
    return next.map((c) =>
      Math.abs(c.end - prevDuration) <= 0.51 ? { ...c, end: durationSec } : c,
    )
  }
  return next
}

function mediaClipsKey(projectId: string, kind: 'video' | 'bg') {
  return `videoclone.${kind}Clips.${projectId}`
}

function loadMediaClips(projectId: string, kind: 'video' | 'bg', durationSec: number): MediaClip[] {
  try {
    const raw = localStorage.getItem(mediaClipsKey(projectId, kind))
    if (raw) {
      const parsed = JSON.parse(raw) as MediaClip[]
      if (Array.isArray(parsed) && parsed.length) {
        return normalizeMediaClips(parsed, durationSec)
      }
    }
  } catch { /* ignore */ }
  return durationSec > 0 ? [fullMediaClip(durationSec)] : []
}

function persistMediaClips(projectId: string, kind: 'video' | 'bg', clips: MediaClip[]) {
  // ponytail: skip [] so projectId reset không ghi đè clip đã lưu
  if (!clips.length) return
  try {
    localStorage.setItem(mediaClipsKey(projectId, kind), JSON.stringify(clips))
  } catch { /* ignore */ }
}

function splitMediaList(clips: MediaClip[], clipId: string, t: number): MediaClip[] {
  return clips.flatMap((c) => {
    if (c.id !== clipId) return [c]
    if (!(t > c.start + SPLIT_EDGE && t < c.end - SPLIT_EDGE)) return [c]
    return [
      { ...c, end: t },
      { id: crypto.randomUUID(), start: t, end: c.end },
    ]
  })
}

function clipAtTime(clips: MediaClip[], t: number): MediaClip | null {
  return clips.find((c) => t >= c.start && t < c.end) ?? clips.find((c) => t >= c.start && t <= c.end) ?? null
}

type EditorSnap = {
  segments: Segment[]
  overlays: TextOverlay[]
  settings: ProjectSettings
  bookmarks: number[]
  selectedId: string | null
  selectedOverlayId: string | null
  trackFocus: 'video' | 'caption' | 'dub' | 'bg' | 'text'
  videoClips: MediaClip[]
  bgClips: MediaClip[]
  selectedMediaId: string | null
}

const HISTORY_MAX = 40

function cloneSnap(s: EditorSnap): EditorSnap {
  return {
    segments: s.segments.map((x) => ({ ...x })),
    overlays: s.overlays.map((x) => ({ ...x })),
    settings: { ...s.settings },
    bookmarks: [...s.bookmarks],
    selectedId: s.selectedId,
    selectedOverlayId: s.selectedOverlayId,
    trackFocus: s.trackFocus,
    videoClips: s.videoClips.map((x) => ({ ...x })),
    bgClips: s.bgClips.map((x) => ({ ...x })),
    selectedMediaId: s.selectedMediaId,
  }
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

/** Preview mask «Làm mờ» — kính CapCut (blur + tint mỏng); xuất pad-blur khớp. */
function coverMaskPreviewStyle(
  style: ProjectSettings['coverMaskStyle'],
  color: string,
  opacity: number,
): React.CSSProperties {
  const [r, g, b] = parseHexColor(color)
  const pct = Math.max(0, Math.min(100, opacity))
  const a = Math.max(0.05, Math.min(1, pct / 100))
  if (style === 'solid') {
    return { backgroundColor: `rgba(${r},${g},${b},${a})` }
  }
  if (style === 'mosaic') {
    return {
      backgroundColor: 'rgba(42,42,48,0.72)',
      backdropFilter: 'blur(22px) saturate(0.4) contrast(0.92) brightness(0.92)',
      WebkitBackdropFilter: 'blur(22px) saturate(0.4) contrast(0.92) brightness(0.92)',
    }
  }
  // Làm mờ CapCut: blur phía sau + tint mỏng
  const tintA = Math.min(0.22, Math.max(0.06, a * 0.28))
  const blurPx = Math.round(22 + a * 20) // ~22–42px
  return {
    backgroundColor: `rgba(${r},${g},${b},${tintA})`,
    backdropFilter: `blur(${blurPx}px) saturate(0.88)`,
    WebkitBackdropFilter: `blur(${blurPx}px) saturate(0.88)`,
  }
}

type PixelBox = { x: number; y: number; w: number; h: number }
type CropRect = { x: number; y: number; w: number; h: number }

const COVER_MASK_STYLES: { id: ProjectSettings['coverMaskStyle']; label: string }[] = [
  { id: 'blur', label: 'Làm mờ' },
  { id: 'solid', label: 'Màu nền' },
  { id: 'mosaic', label: 'Khối' },
]

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

/**
 * Cửa sổ COVER khớp export (burn.py): hardsub hay lộ trước/sau ASR.
 * Caption/TTS vẫn dùng [start,end) chặt; chỉ mask che chữ dùng cửa sổ này.
 * peers: kẹp ngang/mid/label không đè clip kế.
 * Ưu tiên coverStart/coverEnd đã lưu từ OCR.
 */
function coverWindow(seg: Segment, peers?: Segment[]): { start: number; end: number } {
  const base = resolveCoverWindow(seg)
  const layout = seg.layout || 'horizontal'
  if (layout === 'label') {
    return clampOverlayPad(seg, base.start, base.end, peers, 'label')
  }
  if (layout === 'mid') {
    return clampOverlayPad(seg, base.start, base.end, peers, 'mid')
  }
  if (layout === 'vertical') {
    return base
  }
  // horizontal: cắt pad/tail để bbox trước không đè bbox sau
  return clampOverlayPad(seg, base.start, base.end, peers, 'horizontal')
}

/** Cắt pad tại giữa khe với clip cùng loại — không chồng cửa sổ. */
function clampOverlayPad(
  seg: Segment,
  start: number,
  end: number,
  peers: Segment[] | undefined,
  lane: CaptionLaneKey,
): { start: number; end: number } {
  let s = start
  let e = end
  if (peers?.length) {
    for (const o of peers) {
      if (o.id === seg.id) continue
      if (captionLaneOf(o) !== lane) continue
      if (o.end <= seg.start + 0.02) {
        // clip trước — không lấn vào nửa khe
        const cut = (o.end + seg.start) * 0.5
        s = Math.max(s, cut)
      } else if (o.start >= seg.end - 0.02) {
        const cut = (seg.end + o.start) * 0.5
        e = Math.min(e, cut)
      } else if (o.start < seg.end && o.end > seg.start) {
        // timeline đã chồng — chia đôi vùng chồng
        if (o.start >= seg.start) e = Math.min(e, (seg.end + o.start) * 0.5)
        else s = Math.max(s, (o.end + seg.start) * 0.5)
      }
    }
  }
  if (e < s + 0.04) e = s + 0.04
  // luôn phủ ít nhất [start,end) gốc
  s = Math.min(s, seg.start)
  e = Math.max(e, seg.end)
  return { start: Math.max(0, s), end: e }
}

/** Segment để che chữ tại playhead (nới theo coverWindow, khớp xuất). */
function segmentAtCover(segments: Segment[], time: number): Segment | null {
  const hit = segmentAt(segments, time)
  if (hit) return hit
  let best: Segment | null = null
  for (const s of segments) {
    const w = coverWindow(s, segments)
    if (time >= w.start && time < w.end) {
      if (!best || s.start > best.start) best = s
    }
  }
  // Lấp khe < 0.45s giữa 2 câu hardsub (cùng logic xuất)
  if (!best) {
    const ordered = [...segments].sort((a, b) => a.start - b.start)
    for (let i = 0; i < ordered.length - 1; i++) {
      const a = ordered[i]
      const b = ordered[i + 1]
      if ((a.layout || 'horizontal') !== 'horizontal') continue
      if ((b.layout || 'horizontal') !== 'horizontal') continue
      const wa = coverWindow(a, segments)
      const wb = coverWindow(b, segments)
      const gap = wb.start - wa.end
      if (gap > 0 && gap < 0.45 && time >= wa.end && time < wb.start) {
        return time < (wa.end + wb.start) / 2 ? a : b
      }
    }
  }
  return best
}

function segmentHasDub(seg: Segment | undefined): boolean {
  if (!seg) return false
  const isOverlay = seg.layout === 'vertical' || seg.layout === 'label'
  return isOverlay ? seg.dub === true : seg.dub !== false
}

function isOcrOverlayLayout(layout: Segment['layout']): layout is 'vertical' | 'label' | 'mid' {
  return layout === 'vertical' || layout === 'label' || layout === 'mid'
}

/** Caption ngang nhưng bbox OCR nằm giữa khung → xử lý như mid (không ép đáy). */
function effectiveOverlayLayout(
  seg: Segment,
  frameH: number,
): 'vertical' | 'label' | 'mid' | null {
  if (isOcrOverlayLayout(seg.layout)) return seg.layout
  const b = seg.bbox
  if (!b || frameH <= 0) return null
  const cy = b.y + b.h / 2
  // caption vẫn là caption — tag mid chỉ vì OCR đo được Y lệch đáy cổ điển
  if (cy > frameH * 0.18 && cy < frameH * 0.78) return 'mid'
  return null
}

type CaptionLaneKey = 'horizontal' | 'mid' | 'vertical' | 'label'

const CAPTION_LANE_DEFS: {
  key: CaptionLaneKey
  label: string
  color: string
  selected: string
}[] = [
  { key: 'horizontal', label: 'Caption', color: '#5DBAA0', selected: '#3da88a' },
  { key: 'mid', label: 'CAP-MID', color: '#D4A017', selected: '#B8860B' },
  { key: 'vertical', label: 'Dọc', color: '#8B5CF6', selected: '#7C3AED' },
  { key: 'label', label: 'Nhãn', color: '#38BDF8', selected: '#0EA5E9' },
]

/** Lane timeline / caption: ưu tiên layout lưu; horizontal/trống + bbox giữa → mid. */
function captionLaneOf(seg: Segment, frameH = 1920): CaptionLaneKey {
  const lay = seg.layout
  if (lay === 'mid' || lay === 'vertical' || lay === 'label') return lay
  const b = seg.bbox
  if (b && frameH > 0) {
    const cy = b.y + b.h / 2
    // khớp locate._layout_from_cy (0.18–0.78)
    if (cy > frameH * 0.18 && cy < frameH * 0.78) return 'mid'
  }
  return 'horizontal'
}

/** Chuẩn hoá layout theo bbox OCR — ghi đè horizontal sai khi chữ ở giữa khung. */
function withInferredLayout(seg: Segment, frameH: number): Segment {
  if (seg.layout === 'vertical' || seg.layout === 'label') return seg
  const lane = captionLaneOf({ ...seg, layout: undefined }, frameH)
  if (lane === 'mid') return seg.layout === 'mid' ? seg : { ...seg, layout: 'mid' }
  if (seg.layout === 'horizontal' || seg.layout === 'mid') return seg
  return { ...seg, layout: 'horizontal' }
}

/** Overlay Mid/Nhãn/Dọc đang dưới gạch theo [start,end) = đúng thanh timeline solid. */
function solidOverlaysAt(segments: Segment[], time: number): Segment[] {
  return segments.filter(
    (s) => isOcrOverlayLayout(s.layout) && time >= s.start && time < s.end,
  )
}

function solidMidAt(segments: Segment[], time: number, preferId?: string | null): Segment | null {
  const mids = solidOverlaysAt(segments, time).filter((s) => captionLaneOf(s) === 'mid')
  if (!mids.length) return null
  if (preferId) {
    const sel = mids.find((s) => s.id === preferId)
    if (sel) return sel
  }
  return mids.reduce((a, b) => (Math.abs(time - a.start) <= Math.abs(time - b.start) ? a : b))
}

/** Mọi segment đang cháy tại t (có thể chồng mid+dọc).
 * Caption/TTS: chỉ [start,end) chặt — không kéo cover-pad (tránh câu trước đè câu sau).
 * Mask che chữ dùng segmentsAtCover (có pad).
 */
function segmentsAt(segments: Segment[], time: number): Segment[] {
  return segments.filter((s) => time >= s.start && time < s.end)
}

function segmentsAtCover(segments: Segment[], time: number): Segment[] {
  const hits: Segment[] = []
  const seen = new Set<string>()
  for (const s of segments) {
    const w = coverWindow(s, segments)
    if (time >= w.start && time < w.end) {
      hits.push(s)
      seen.add(s.id)
    }
  }
  // Clip timeline [start,end) luôn che — kể cả coverStart/coverEnd cũ lệch
  for (const s of solidOverlaysAt(segments, time)) {
    if (seen.has(s.id)) continue
    hits.push(s)
    seen.add(s.id)
  }
  if (hits.length) return hits
  const one = segmentAtCover(segments, time)
  return one ? [one] : []
}

function pickTimelineSeg(segments: Segment[], time: number, selectedId: string | null): Segment | null {
  // Quy tắc: thanh vàng Mid solid dưới gạch → chọn đúng Mid đó (không stick vertical/cover pad cũ)
  const mid = solidMidAt(segments, time, selectedId)
  if (mid) return mid
  const labels = solidOverlaysAt(segments, time).filter((s) => captionLaneOf(s) === 'label')
  if (labels.length) {
    const sel = selectedId ? labels.find((s) => s.id === selectedId) : null
    return sel ?? labels[0]
  }
  const hits = segmentsAt(segments, time)
  if (!hits.length) return segmentAtCover(segments, time)
  if (selectedId) {
    const sel = hits.find((s) => s.id === selectedId)
    if (sel) {
      // Vertical dài: đừng cướp selection khi đang chỉ pad cover — nhưng solid mid đã return trên
      return sel
    }
  }
  return (
    hits.find((s) => captionLaneOf(s) === 'horizontal')
    ?? hits.find((s) => captionLaneOf(s) === 'vertical')
    ?? hits[0]
  )
}

/** Self-check: Mid solid dưới gạch luôn được chọn — không stick vertical/cover pad. */
export function __checkSolidMidBboxPick(): void {
  const vert = { id: 'v', start: 0, end: 400, layout: 'vertical' as const, source: '花', translation: 'x' }
  const midA = { id: 'a', start: 100, end: 120, layout: 'mid' as const, source: '旧', translation: 'cũ' }
  const midB = { id: 'b', start: 190, end: 220, layout: 'mid' as const, source: '打扫', translation: 'quét' }
  const segs = [vert, midA, midB]
  const t = 200
  const picked = pickTimelineSeg(segs as Segment[], t, 'v')
  if (picked?.id !== 'b') throw new Error(`expected mid b under playhead, got ${picked?.id}`)
  const stickPad = pickTimelineSeg(segs as Segment[], t, 'a')
  if (stickPad?.id !== 'b') throw new Error(`must not stick mid-a cover/select over solid mid-b`)
  if (!solidMidAt(segs as Segment[], t)) throw new Error('solidMidAt missing')
}

function dubPlaybackSpeed(seg: Segment): number {
  return Math.max(0.75, Math.min(1.5, seg.ttsSpeed ?? 1))
}

/** preferVideo = chậm 0.80×; đã bake vào file → rate 1. */
function previewVideoRate(
  matchDuration: string | undefined,
  bakedPreferVideo?: boolean,
): number {
  if (bakedPreferVideo) return 1
  return matchDuration === 'preferVideo' ? 0.8 : 1
}

/**
 * Hết cửa sổ TTS trên trục media.
 * Preview: tới trước câu sau (không cắt theo audioDuration — metadata hay ngắn hơn file → thiếu đuôi).
 */
function dubAudioAbsEnd(seg: Segment, segments: Segment[], _videoRate = 1): number {
  let nextStart = Number.POSITIVE_INFINITY
  for (const s of segments) {
    if (s.start > seg.start + 0.02) nextStart = Math.min(nextStart, s.start)
  }
  if (Number.isFinite(nextStart)) return nextStart - 0.02
  const speed = dubPlaybackSpeed(seg)
  const ad = seg.audioDuration ?? 0
  if (ad > 0.05) return seg.start + ad / speed
  return Math.max(seg.end, seg.start + 0.05)
}

/** Segment đang phát TTS tại playhead (kể cả tràn khỏi end caption tới hết audio / trước câu sau). */
function segmentForDub(segments: Segment[], time: number, videoRate = 1): Segment | null {
  const hit = segmentAt(segments, time)
  if (hit && segmentHasDub(hit) && hit.audioUrl && time < dubAudioAbsEnd(hit, segments, videoRate)) {
    return hit
  }
  let best: Segment | null = null
  for (const s of segments) {
    if (!segmentHasDub(s) || !s.audioUrl) continue
    if (time < s.start) continue
    if (time < dubAudioAbsEnd(s, segments, videoRate)) {
      if (!best || s.start > best.start) best = s
    }
  }
  return best
}

/** Chiều rộng clip TTS trên timeline (giây media) */
function dubClipSeconds(seg: Segment, segments: Segment[], videoRate = 1): number {
  return Math.max(0.05, dubAudioAbsEnd(seg, segments, videoRate) - seg.start)
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

const AUTO_SUBTITLE_FONT = 36
/** Khớp burn._cover_max_h — đủ 1–3 dòng theo font */
const COVER_MAX_H_FRAME_RATIO = 0.065

const COVER_SHADOW_BOT = 4

function coverPad(fontSizePx = AUTO_SUBTITLE_FONT, frameW = 1080) {
  return {
    x: Math.max(8, Math.round(frameW * 0.012)),
    // Trên sát chữ (OCR hay thừa trống); dưới phải đủ stroke CJK
    top: Math.max(2, Math.round(fontSizePx * 0.04)),
    bottom: Math.max(18, Math.round(fontSizePx * 0.55)),
  }
}

/** Căn giữa khối chữ trong cover (đúng giữa khung tím). */
function captionCenterInCover(coverY: number, coverH: number, textBlockH: number) {
  return Math.round(coverY + Math.max(0, (coverH - textBlockH) / 2))
}

const CAP_PAD_X = 2

function coverInnerWidth(coverW: number, fontSizePx: number, frameW: number) {
  const pad = coverPad(fontSizePx, frameW)
  return Math.max(24, coverW - pad.x * 2 - CAP_PAD_X * 2)
}

function frameMaxInnerWidth(fontSizePx: number, frameW: number) {
  // Ưu tiên gần mép 2 bên trước khi xuống dòng
  const maxCoverW = Math.min(frameW, Math.round(frameW * 0.96))
  return coverInnerWidth(maxCoverW, fontSizePx, frameW)
}

function coverBleedX(contentW: number, frameW = 1080) {
  // Bleed vừa đủ stroke CJK — không nới xa
  return Math.max(6, Math.round(contentW * 0.028), Math.round(frameW * 0.006))
}

/** Đo bề ngang mực chữ nguồn (CJK hardsub + outline) — không theo VI. */
function measureSourceInkWidth(sourceText: string, fontSizePx: number, anchorH: number) {
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
function fitHardsubCover(
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
  const botExtra = Math.max(pad.bottom, Math.round(seed.h * 0.4), Math.round(fontPx * 0.7))
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
function resolveInkWidth(
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

function coverContentWidth(origW: number, transW: number) {
  return Math.max(origW, transW)
}

function coverBoxWidth(contentW: number, frameW: number) {
  const bleed = coverBleedX(contentW, frameW)
  return Math.min(frameW, Math.ceil(contentW + bleed * 2))
}

type OverLayout = { cover: PixelBox; caption: PixelBox; lines: string[]; fontPx?: number }

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
  // system-ui đo hơi rộng hơn font burn/preview — chừa ~8% tránh xuống dòng sớm
  const fits = (s: string) => measureLineWidth(s, fontSizePx) <= maxInnerW * 1.08
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

  const lines = oneLineW <= maxInnerW * 1.1
    ? [trimmed]
    : wrapCaptionText(trimmed, maxInnerW, fontSizePx, 3)

  const lineH = fontSizePx * 1.12
  const textBlockH = Math.ceil(lines.length * lineH + 4)
  const textW = Math.max(...lines.map((l) => measureLineWidth(l, fontSizePx)), oneLineW)

  const sourceTrim = sourceText.trim()
  const sourceW = sourceTrim ? measureSourceInkWidth(sourceTrim, fontSizePx, anchor.h) : 0
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
  const captionY = captionCenterInCover(coverY, coverH, textBlockH)

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
  if (isOcrOverlayLayout(seg.layout)) {
    return overlayCoverSeed(seg, frameW, frameH)
  }
  if (!over) {
    const seed = seg.bbox ?? seedCoverBox(seg, frameW, frameH, fontPx)
    if (!seed) return null
    return normalizeCoverBox(seed, frameW, frameH, fontPx)
  }
  if (seg.bbox) {
    return clampCoverBox(seg.bbox, frameW, frameH)
  }
  const seed = seedCoverBox(seg, frameW, frameH, fontPx)
  if (!seed) return null
  const anchor = normalizeCoverBox(seed, frameW, frameH, fontPx)
  return fitCoverBoxOver(anchor, seg.translation, fontPx, frameW, frameH, seg.source ?? '')
}

/** Seed khung che overlay: chỉ fallback đúng layout; không bbox CJK → null (đừng bịa cột dọc). */
function overlayCoverSeed(seg: Segment, frameW: number, frameH: number): PixelBox | null {
  const layout: 'vertical' | 'label' | 'mid' | 'horizontal' =
    seg.layout === 'label'
      ? 'label'
      : seg.layout === 'mid'
        ? 'mid'
        : seg.layout === 'vertical'
          ? 'vertical'
          : 'horizontal'
  if (!seg.bbox) {
    // CJK chờ OCR thật — không đoán cột trái / giữa (gây caption “từ trên trời”)
    if (layout === 'horizontal' || isCjkHardsubSource(seg.source)) return null
    return clampCoverBox(ocrFallbackCover(frameW, frameH, layout), frameW, frameH)
  }
  const box = clampCoverBox(seg.bbox, frameW, frameH)
  if (layout === 'vertical' && box.w > box.h * 0.85) {
    return clampCoverBox(ocrFallbackCover(frameW, frameH, 'vertical'), frameW, frameH)
  }
  // mid: chỉ bỏ khung gần full-frame (lưới đáy nhầm). 2 dòng hardsub giữa/đáy vẫn giữ.
  if (layout === 'mid' && (box.w > frameW * 0.92 || box.h > frameH * 0.28)) {
    return clampCoverBox(ocrFallbackCover(frameW, frameH, 'mid'), frameW, frameH)
  }
  if (layout === 'label' && (box.w > frameW * 0.7 || box.h > frameH * 0.35)) {
    return clampCoverBox(ocrFallbackCover(frameW, frameH, 'label'), frameW, frameH)
  }
  return box
}

function isBadOverlayStoredCover(seg: Segment, cover: PixelBox, frameW = 1080, frameH = 1920): boolean {
  if (seg.layout === 'vertical' && cover.w > cover.h * 0.85) return true
  // ponytail: trước đây w>65%/h>12% quăng mid 2 dòng → fallback giữa → chữ đáy lộ + "bbox ẩn"
  if (seg.layout === 'mid' && (cover.w > frameW * 0.92 || cover.h > frameH * 0.28)) return true
  if (seg.layout === 'label' && (cover.w > frameW * 0.7 || cover.h > frameH * 0.35)) return true
  return false
}

function toCaptionLayout(caption: PixelBox, lines: string[], fontSize: number): NonNullable<Segment['captionLayout']> {
  return { x: caption.x, y: caption.y, w: caption.w, h: caption.h, lines, fontSize }
}

/** User đã kéo tay / lưu layout — giữ nguyên bbox (không adaptive reset). */
function hasStoredLayout(seg: Segment | undefined, fontPx?: number): boolean {
  const cl = seg?.captionLayout
  const b = seg?.bbox
  if (!(b && cl?.lines?.length && cl.w > 0 && cl.h > 0)) return false
  if (fontPx != null && fontPx > 0 && cl.fontSize > 0 && fontPx !== cl.fontSize) return false
  return true
}

/** Đọc đúng bbox + captionLayout đã lưu — không tính lại (preview = xuất). */
function storedOverLayout(seg: Segment, frameW: number, frameH: number): OverLayout | null {
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
function resolveOverLayout(
  seg: Segment | undefined,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  coverOverride?: PixelBox,
): OverLayout | null {
  if (!seg?.translation.trim()) return null
  if (!settings.burnSubs) return null
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
      const laid = layoutOcrOverlay(overlayLay, coverOverride, seg.translation, lockFs, frameW, frameH)
      return {
        cover: clampCoverBox(coverOverride, frameW, frameH),
        caption: laid.caption,
        lines: laid.lines,
        fontPx: laid.fontPx,
      }
    }
    if (hasStoredLayout(seg, undefined)) {
      const stored = storedOverLayout(seg, frameW, frameH)
      if (stored && !isBadOverlayStoredCover(seg, stored.cover, frameW, frameH)) {
        // Tin bbox/mask đã lưu; chữ xếp lại trong cover (tránh captionLayout x/y lệch → chữ sai chỗ)
        const cover = stored.cover
        const want = preferred > 0
          ? preferred
          : (Number(seg.captionLayout?.fontSize) > 0
            ? Number(seg.captionLayout?.fontSize)
            : 0)
        const laid = layoutOcrOverlay(overlayLay, cover, seg.translation, want, frameW, frameH)
        return {
          cover,
          caption: laid.caption,
          lines: laid.lines,
          fontPx: laid.fontPx,
        }
      }
    }
    // mid/dọc/nhãn: cover = bbox OCR (không nới theo VI); chưa OCR → không bịa khung
    const seed = overlayCoverSeed(seg, frameW, frameH)
    if (!seed) return null
    const laid = layoutOcrOverlay(overlayLay, seed, seg.translation, preferred, frameW, frameH)
    return { cover: laid.cover, caption: laid.caption, lines: laid.lines, fontPx: laid.fontPx }
  }

  // Caption đáy/over horizontal — cần chế độ che chữ
  if (!(settings.coverHardsubs && settings.burnSubs)) return null

  // Đang kéo: bám đúng draft (user chỉnh tay)
  if (coverOverride) {
    return manualCoverLayout(coverOverride, seg.translation, fontPx, frameW, frameH, true)
  }

  // Đã lưu từ editor (kéo tay) — giữ đúng bbox; chỉ xếp chữ trong cover
  if (hasStoredLayout(seg, fontPx)) {
    const stored = storedOverLayout(seg, frameW, frameH)
    if (!stored) return null
    const cover = stored.cover
    const laid = layoutCaptionInCover(cover, seg.translation, fontPx, frameW)
    return { cover, ...laid, fontPx }
  }

  const seedRaw = seg.bbox
    ? clampCoverBox(seg.bbox, frameW, frameH)
    : seedCoverBox(seg, frameW, frameH, fontPx)
  if (!seedRaw) return null
  const seed = normalizeCoverBox(seedRaw, frameW, frameH, fontPx)

  // Chưa kéo tay: auto (1) che full chữ cũ → (2) xếp chữ dịch trong cover
  const anchor = coverToAnchor(seed, fontPx, frameW)
  const sourceTrim = (seg.source ?? '').trim()
  const sourceW = sourceTrim ? measureSourceInkWidth(sourceTrim, fontPx, anchor.h) : 0
  const inkW = resolveInkWidth(anchor, seed, !!sourceTrim, sourceW, frameW)
  const auto = layoutOverMode(anchor, seg.translation, fontPx, frameW, frameH, seg.source ?? '', inkW)
  const cover = fitHardsubCover(seed, auto.cover.w, fontPx, frameW, frameH, seg.source ?? '')
  const laid = layoutCaptionInCover(cover, seg.translation, fontPx, frameW)
  return { cover, ...laid, fontPx }
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

/** Mask che chữ gốc — không cần bản dịch (mid/label/dọc OCR). */
function resolveCoverMaskOnly(
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
  return ink ? unionBox(ink, cover) : cover
}

/** Preview: dùng layout đã giãn ngang; 9:16 chỉ thêm mask che OCR trong crop. */
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
  if (cropCoversFull(crop, frameW, frameH)) {
    return { ...base, mask: base.cover }
  }
  const ink = intersectBox(base.cover, crop) ?? intersectBox(
    seg!.bbox ? clampCoverBox(seg!.bbox, frameW, frameH) : base.cover,
    crop,
  )
  const mask = ink ? unionBox(ink, base.cover) : base.cover
  return { ...base, mask }
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
    captionLayout: toCaptionLayout(layout.caption, layout.lines, layout.fontPx ?? fontPx),
  }
}

/**
 * below/above (không cover): bake đúng khung chữ preview (`estimateCaptionBox`).
 * Không dùng resolveOverLayout — hàm đó chỉ trả layout khi cover / OCR overlay.
 */
function resolveBelowAboveLayout(
  seg: Segment,
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
  placement: 'below' | 'above',
): OverLayout | null {
  if (!seg.translation.trim()) return null
  const fontPx = resolveCaptionFontSize(seg, settings, frameW, frameH)
  const ocr =
    (seg.bbox ? clampCoverBox(seg.bbox, frameW, frameH) : null)
    ?? seedCoverBox(seg, frameW, frameH, fontPx)
    ?? fallbackCoverBox(frameW, frameH, fontPx)
  const innerW = Math.min(frameW, Math.round(frameW * 0.88))
  const lines = wrapCaptionText(seg.translation, innerW, fontPx, 3)
  const caption = estimateCaptionBox(ocr, seg.translation, fontPx, frameW, frameH, placement)
  return { cover: ocr, caption, lines, fontPx }
}

/** Bake đúng layout đang hiện ở preview vào segment — Xuất bản khóa WYSIWYG. */
function buildExportSegments(
  segments: Segment[],
  settings: ProjectSettings,
  frameW: number,
  frameH: number,
): Segment[] {
  if (!settings.burnSubs || frameW <= 0) return segments
  const place = captionPlacement(settings)
  return segments.map((seg) => {
    if (!seg.translation.trim()) return seg
    const layout = resolveOverLayout(seg, settings, frameW, frameH)
    if (layout) {
      const fontPx = layout.fontPx ?? resolveCaptionFontSize(seg, settings, frameW, frameH)
      return segmentWithLayout(seg, layout, fontPx)
    }
    // Caption ngang chế độ chèn dưới/trên — khớp activeCaptionBox trên preview
    if (
      (place === 'below' || place === 'above')
      && !isOcrOverlayLayout(seg.layout)
      && !effectiveOverlayLayout(seg, frameH)
    ) {
      const baked = resolveBelowAboveLayout(seg, settings, frameW, frameH, place)
      if (baked) {
        return segmentWithLayout(seg, baked, baked.fontPx ?? resolveCaptionFontSize(seg, settings, frameW, frameH))
      }
    }
    return seg
  })
}

function isCjkHardsubSource(src: string | undefined): boolean {
  let cjk = 0
  for (const c of src ?? '') {
    if (c >= '\u4e00' && c <= '\u9fff') cjk += 1
  }
  return cjk >= 2
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

/** Caption trong cover cố định. Cover đã che chữ cũ — chỉ xếp chữ + khung text bên trong. */
function layoutCaptionInCover(
  cover: PixelBox,
  text: string,
  fontSizePx: number,
  _frameW: number,
): Pick<OverLayout, 'caption' | 'lines'> {
  const trimmed = text.trim()
  // Gần full bề ngang cover (pad mỏng) — tránh trừ pad quá mạnh → wrap sớm
  const edge = Math.max(4, Math.round(cover.w * 0.03))
  const maxInnerW = Math.max(24, cover.w - edge * 2)
  const oneLineW = measureLineWidth(trimmed, fontSizePx)
  const lines = oneLineW <= maxInnerW * 1.1
    ? [trimmed]
    : wrapCaptionText(trimmed, maxInnerW, fontSizePx, 3)
  const lineH = fontSizePx * 1.12
  const textBlockH = Math.ceil(lines.length * lineH + 4)
  const textW = Math.max(...lines.map((l) => measureLineWidth(l, fontSizePx)), oneLineW)
  // Khung chữ = text; không co cover. 1 dòng: trải gần full cover để căn giữa đẹp
  const captionW = Math.ceil(
    lines.length === 1
      ? Math.min(cover.w, Math.max(textW + CAP_PAD_X * 2, cover.w - edge * 2))
      : Math.min(cover.w, textW + CAP_PAD_X * 2),
  )
  const cx = cover.x + cover.w / 2
  const captionX = Math.round(Math.max(cover.x, Math.min(cover.x + cover.w - captionW, cx - captionW / 2)))
  const captionY = captionCenterInCover(cover.y, cover.h, textBlockH)
  return {
    caption: { x: captionX, y: captionY, w: captionW, h: textBlockH },
    lines,
  }
}

/** Tự co/giãn cover theo chữ — ngang trước; giữ mép trên (không kéo bbox lên). */
function adaptiveCoverLayout(
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
  const oneLineW = measureLineWidth(trimmed, fontSizePx)

  const wrapAt = (innerW: number) =>
    oneLineW <= innerW ? [trimmed] : wrapCaptionText(trimmed, innerW, fontSizePx, 3)

  let lines = wrapAt(maxInnerW)

  const sizeFromLines = (ls: string[]) => {
    const lineH = fontSizePx * 1.12
    const textBlockH = Math.ceil(ls.length * lineH + 4)
    const textW = Math.max(...ls.map((l) => measureLineWidth(l, fontSizePx)), 1)
    const captionW = Math.ceil(textW + CAP_PAD_X * 2)
    // Giữ tối thiểu bề ngang cover cũ (OCR) — không co về sát VI
    const coverW = Math.min(frameW, Math.max(cover.w, captionW + pad.x * 2))
    const byText = textBlockH + pad.top + pad.bottom + COVER_SHADOW_BOT
    const coverH = Math.min(frameH, Math.max(cover.h, byText))
    return { lineH, textBlockH, textW, captionW, coverW, coverH }
  }

  let { textBlockH, captionW, coverW, coverH } = sizeFromLines(lines)
  let coverX = Math.round(Math.max(0, Math.min(frameW - coverW, cx - coverW / 2)))
  let coverY = Math.round(Math.max(0, Math.min(frameH - coverH, topY)))
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
    coverY = Math.round(Math.max(0, Math.min(frameH - coverH, topY)))
    box = clampCoverBox({ x: coverX, y: coverY, w: coverW, h: coverH }, frameW, frameH)
  }

  const capX = Math.round(Math.max(box.x, Math.min(box.x + box.w - captionW, box.x + box.w / 2 - captionW / 2)))
  const capY = captionCenterInCover(box.y, box.h, textBlockH)
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
      // Giữ x + mép trên khi giãn cao — không recenter (kéo ngang bị nhảy)
      box = clampCoverBox({ x: box.x, y: box.y, w: box.w, h: needH }, frameW, frameH)
      laid = layoutCaptionInCover(box, text, fontSizePx, frameW)
    }
    return { cover: box, ...laid }
  }
  return adaptiveCoverLayout(cover, text, fontSizePx, frameW, frameH)
}

/** Cover mặc định phụ đề đáy — chỉ khi không phải CJK chờ OCR. */
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

/**
 * Seed cover: bbox OCR nếu có.
 * CJK chưa bbox → null (không đoán giữa/đáy — video khác nhau vị trí khác nhau).
 */
function seedCoverBox(
  seg: Pick<Segment, 'source' | 'bbox' | 'layout'> | undefined,
  frameW: number,
  frameH: number,
  fontSizePx = AUTO_SUBTITLE_FONT,
): PixelBox | null {
  if (seg?.bbox) return clampCoverBox(seg.bbox, frameW, frameH)
  if (seg && isCjkHardsubSource(seg.source)) return null
  return fallbackCoverBox(frameW, frameH, fontSizePx)
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

/**
 * Overlay mid/dọc/nhãn: scale theo fontPx nguồn / kích thước cover
 * (không dùng cqh/n — công thức cũ bỏ qua fontPx nên kéo cỡ không ăn).
 */
function overlayDisplayFontStyle(
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
    return {
      fontSize: `min(calc(100cqw * ${byW}), calc(100cqh * ${byH}))`,
      lineHeight: 1.08,
      maxWidth: '100%',
    }
  }
  // mid: kẹp H + W — chỉ cqh dễ phình font → cắt đuôi (như thế nà|)
  if (layout === 'mid') {
    const n = Math.max(1, _lineCount)
    const lh = 1.05
    const fracH = Math.min(fontPx / h, 0.92 / (n * lh))
    const fracW = Math.min(0.96, fontPx / w)
    return {
      fontSize: `min(calc(100cqh * ${fracH}), calc(100cqw * ${fracW}))`,
      lineHeight: lh,
      maxWidth: '100%',
      width: '100%',
    }
  }
  // label
  const lh = 1.15
  return {
    fontSize: `min(calc(100cqh * ${Math.min(0.88, byH)}), calc(100cqw * ${Math.min(0.96, byW)}))`,
    lineHeight: lh,
    maxWidth: '100%',
  }
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

/** Overlay mid/dọc/nhãn: 0 = auto fit khung; >0 = đúng cỡ user set (không lấy cỡ phụ đề đáy dự án). */
function resolveOverlayFontPreferred(seg: Segment | undefined): number {
  const segFs = seg?.fontSize ?? 0
  return segFs > 0 ? segFs : 0
}

/** placement khi xuất: cover+ burn → over; không cover → below/above.
 * Mid/dọc/nhãn luôn 'over' (neo OCR) — không đẩy xuống đáy khi chọn “phía dưới”.
 */
function captionPlacement(settings: ProjectSettings): 'over' | 'below' | 'above' {
  if (settings.coverHardsubs && settings.burnSubs) return 'over'
  return settings.captionPlacement === 'above' ? 'above' : 'below'
}

/** Overlay OCR vẫn neo theo bbox khi burn — coverHardsubs chỉ bật mask. */
function overlayTextEnabled(settings: ProjectSettings): boolean {
  return Boolean(settings.burnSubs && settings.targetLang !== 'none')
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
type TrackId = 'video' | 'caption' | 'dub' | 'bg' | 'text'
type CtxMenu =
  | { kind: 'segment'; segId: string; x: number; y: number }
  | { kind: 'dub'; segId: string; x: number; y: number }
  | { kind: 'bg'; x: number; y: number }
  | { kind: 'overlay'; overlayId: string; x: number; y: number }
  | { kind: 'track'; track: TrackId; x: number; y: number }

function emptyTrackFlags(): Record<TrackId, boolean> {
  return { video: false, caption: false, dub: false, bg: false, text: false }
}

/** Video mặc định tắt tiếng — nghe từ Âm gốc / stem, tránh double audio */
function defaultTrackMute(): Record<TrackId, boolean> {
  return { ...emptyTrackFlags(), video: true }
}

export default function LivePreviewEditor({
  videoUrl,
  mediaDuration: mediaDurationProp,
  workClipSec = 0,
  bakedPreferVideo = false,
  bakedSpeed = 1,
  projectId,
  segments,
  settings,
  voices,
  busy,
  onDub,
  onBack,
  onChange,
  onSegmentsReplace,
  onPreviewRebaked,
  onExport,
  onSettings,
  overlays,
  onOverlayChange,
  onOverlayDelete,
  onOverlaysReplace,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const dubAudioRef = useRef<HTMLAudioElement | null>(null)
  const bgAudioRef = useRef<HTMLAudioElement | null>(null)
  const dubTokenRef = useRef('')
  /** Tua video / đổi đoạn → hard sync TTS; còn lại để audio free-run (tránh ngắt vì seek mỗi timeupdate). */
  const dubHardSyncRef = useRef(false)
  const videoMutedForDubRef = useRef(false)
  const trackRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const previewRef = useRef<HTMLDivElement>(null)
  const rulerScrollRef = useRef<HTMLDivElement>(null)
  const tracksScrollRef = useRef<HTMLDivElement>(null)
  const labelsScrollRef = useRef<HTMLDivElement>(null)
  const tracksColRef = useRef<HTMLDivElement>(null)
  const syncingYRef = useRef(false)
  const bboxDraftRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null)
  const draftRef = useRef<{ id: string; start: number; end: number } | null>(null)

  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(() =>
    Number.isFinite(mediaDurationProp) && (mediaDurationProp ?? 0) > 0 ? mediaDurationProp! : 0,
  )

  useEffect(() => {
    if (Number.isFinite(mediaDurationProp) && (mediaDurationProp ?? 0) > 0) {
      setDuration(mediaDurationProp!)
    }
  }, [mediaDurationProp])
  const [videoSize, setVideoSize] = useState({ width: 1080, height: 1920 })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [ttsBusy, setTtsBusy] = useState(false)
  const [ttsError, setTtsError] = useState<string | null>(null)
  const [speedDraft, setSpeedDraft] = useState(() =>
    bakedSpeed > 0 ? bakedSpeed : bakedPreferVideo ? 0.8 : 1,
  )
  const [speedBusy, setSpeedBusy] = useState(false)
  const [speedError, setSpeedError] = useState<string | null>(null)
  const [stemStatus, setStemStatus] = useState<'off' | 'loading' | 'ready' | 'error'>('off')
  const [stemProgress, setStemProgress] = useState(0)
  const [stemError, setStemError] = useState<string | null>(null)
  const [stemRetry, setStemRetry] = useState(0)
  const [trackMute, setTrackMute] = useState(defaultTrackMute)
  const [trackHidden, setTrackHidden] = useState(emptyTrackFlags)
  const [trackLocked, setTrackLocked] = useState(emptyTrackFlags)
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null)
  const [draft, setDraft] = useState<{ id: string; start: number; end: number } | null>(null)
  const [bboxDraft, setBboxDraft] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [draggingBox, setDraggingBox] = useState(false)
  const [snapGuides, setSnapGuides] = useState<SnapGuides>({ h: false, v: false })
  const [selectedOverlayId, setSelectedOverlayId] = useState<string | null>(null)
  /** Track đang focus — click Caption ≠ TTS ≠ Âm gốc ≠ Text */
  const [trackFocus, setTrackFocus] = useState<'video' | 'caption' | 'dub' | 'bg' | 'text'>('video')
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(null)
  const [videoClips, setVideoClips] = useState<MediaClip[]>([])
  const [bgClips, setBgClips] = useState<MediaClip[]>([])
  const [tool, setTool] = useState<'select' | 'cover' | 'text'>('select')
  const [zoom, setZoom] = useState(1)
  const zoomTouchedRef = useRef(false)
  const [scrollLeft, setScrollLeft] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [bookmarks, setBookmarks] = useState<number[]>(() => loadBookmarks(projectId))
  const [histTick, setHistTick] = useState(0)
  const pastRef = useRef<EditorSnap[]>([])
  const futureRef = useRef<EditorSnap[]>([])
  const historyQuietRef = useRef(false)
  const [assetsTab, setAssetsTab] = useState<AssetsTab>('media')
  const [propTab, setPropTab] = useState<PropTab>('caption')
  const [fontSizeDraft, setFontSizeDraft] = useState(0)
  const [aspectMenuOpen, setAspectMenuOpen] = useState(false)
  const aspectMenuRef = useRef<HTMLDivElement>(null)
  /** Preview canvas zoom: fit = vừa khung; số = scale so với fit */
  const [previewZoom, setPreviewZoom] = useState<'fit' | number>('fit')
  const [fitMenuOpen, setFitMenuOpen] = useState(false)
  const fitMenuRef = useRef<HTMLDivElement>(null)
  const PREVIEW_ZOOM_PRESETS = [0.25, 0.5, 0.75, 1, 1.5, 2] as const
  const pxPerSec = PX_PER_SEC_BASE * zoom

  useEffect(() => {
    setBookmarks(loadBookmarks(projectId))
    pastRef.current = []
    futureRef.current = []
    zoomTouchedRef.current = false
    setVideoClips([])
    setBgClips([])
    setSelectedMediaId(null)
    setTrackMute(defaultTrackMute())
    setTrackHidden(emptyTrackFlags())
    setTrackLocked(emptyTrackFlags())
    setHistTick((n) => n + 1)
  }, [projectId])

  useEffect(() => {
    persistBookmarks(projectId, bookmarks)
  }, [projectId, bookmarks])

  useEffect(() => {
    persistMediaClips(projectId, 'video', videoClips)
  }, [projectId, videoClips])

  useEffect(() => {
    persistMediaClips(projectId, 'bg', bgClips)
  }, [projectId, bgClips])

  function takeSnap(): EditorSnap {
    return cloneSnap({
      segments,
      overlays,
      settings,
      bookmarks,
      selectedId,
      selectedOverlayId,
      trackFocus,
      videoClips,
      bgClips,
      selectedMediaId,
    })
  }

  function pushHistory() {
    if (historyQuietRef.current) return
    pastRef.current.push(takeSnap())
    if (pastRef.current.length > HISTORY_MAX) pastRef.current.shift()
    futureRef.current = []
    setHistTick((n) => n + 1)
  }

  function applySnap(snap: EditorSnap) {
    historyQuietRef.current = true
    void onSegmentsReplace(snap.segments)
    void onOverlaysReplace(snap.overlays)
    onSettings(snap.settings)
    setBookmarks(snap.bookmarks)
    setSelectedId(snap.selectedId)
    setSelectedOverlayId(snap.selectedOverlayId)
    setTrackFocus(snap.trackFocus)
    setVideoClips(snap.videoClips)
    setBgClips(snap.bgClips)
    setSelectedMediaId(snap.selectedMediaId)
    queueMicrotask(() => {
      historyQuietRef.current = false
    })
    setHistTick((n) => n + 1)
  }

  function undoEdit() {
    if (!pastRef.current.length) return
    const cur = takeSnap()
    const prev = pastRef.current.pop()!
    futureRef.current.push(cur)
    applySnap(prev)
  }

  function redoEdit() {
    if (!futureRef.current.length) return
    const cur = takeSnap()
    const next = futureRef.current.pop()!
    pastRef.current.push(cur)
    applySnap(next)
  }

  const canUndo = pastRef.current.length > 0
  const canRedo = futureRef.current.length > 0
  void histTick

  function toggleTrackFlag(
    setFlags: React.Dispatch<React.SetStateAction<Record<TrackId, boolean>>>,
    id: TrackId,
  ) {
    setFlags((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  function openCtxMenu(menu: CtxMenu, event: React.MouseEvent) {
    event.preventDefault()
    event.stopPropagation()
    const pad = 8
    const x = Math.min(event.clientX, window.innerWidth - 220)
    const y = Math.min(event.clientY, window.innerHeight - 280)
    setCtxMenu({ ...menu, x: Math.max(pad, x), y: Math.max(pad, y) })
  }

  useEffect(() => {
    if (!ctxMenu) return
    const close = () => setCtxMenu(null)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [ctxMenu])

  function syncFollowers() {
    const scrl = tracksScrollRef.current
    if (!scrl) return
    setScrollLeft(scrl.scrollLeft)
    if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = scrl.scrollLeft
    if (!syncingYRef.current && labelsScrollRef.current) {
      syncingYRef.current = true
      labelsScrollRef.current.scrollTop = scrl.scrollTop
      syncingYRef.current = false
    }
  }

  function syncLabelsY() {
    const lab = labelsScrollRef.current
    const trk = tracksScrollRef.current
    if (!lab || !trk || syncingYRef.current) return
    syncingYRef.current = true
    trk.scrollTop = lab.scrollTop
    syncingYRef.current = false
  }

  const selected = selectedId ? segments.find((s) => s.id === selectedId) : undefined
  const lastSegment = segments[segments.length - 1]
  // Preview Ns → chỉ làm việc trong Ns (khớp xuất). Dịch full → cả video.
  const sourceDur = Number.isFinite(duration) && duration > 0 ? duration : 0
  const clipCap = workClipSec > 0 ? workClipSec : 0
  const timelineDuration = clipCap > 0
    ? Math.min(clipCap, sourceDur > 0 ? sourceDur : clipCap)
    : Math.max(sourceDur, lastSegment?.end ?? 0, 1)
  const videoSpan = timelineDuration
  const trackWidth = Math.max(Math.ceil(timelineDuration * pxPerSec), 400)
  const playheadPx = time * pxPerSec - scrollLeft
  const tickInterval = [1, 2, 5, 10, 30, 60, 120, 300, 600].find((c) => c * pxPerSec >= 80) ?? 600
  const ticks = Array.from(
    { length: Math.ceil(timelineDuration / tickInterval) + 1 },
    (_, i) => i * tickInterval,
  ).filter((t) => t <= timelineDuration + tickInterval)

  // Mở editor / đổi độ dài clip → zoom fit full ngang (cho đến khi user chỉnh zoom)
  useEffect(() => {
    let ro: ResizeObserver | null = null
    let raf = 0
    const applyFit = (el: HTMLElement) => {
      if (zoomTouchedRef.current) return
      const w = el.clientWidth
      if (w < 80 || timelineDuration <= 0) return
      setZoom(fitTimelineZoom(timelineDuration, w))
      setScrollLeft(0)
      el.scrollLeft = 0
      if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = 0
    }
    const bind = () => {
      const el = tracksScrollRef.current
      if (!el) {
        raf = requestAnimationFrame(bind)
        return
      }
      applyFit(el)
      if (typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(() => applyFit(el))
        ro.observe(el)
      }
    }
    bind()
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
    }
  }, [timelineDuration, projectId])

  function setZoomManual(next: number | ((z: number) => number)) {
    zoomTouchedRef.current = true
    setZoom((z) => {
      const v = typeof next === 'function' ? next(z) : next
      return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, v))
    })
  }

  function zoomToFit() {
    zoomTouchedRef.current = false
    const w = tracksScrollRef.current?.clientWidth ?? 0
    if (w > 80 && timelineDuration > 0) {
      setZoom(fitTimelineZoom(timelineDuration, w))
      setScrollLeft(0)
      if (tracksScrollRef.current) tracksScrollRef.current.scrollLeft = 0
      if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = 0
    }
  }

  const mediaDurRef = useRef(0)

  // Init / clamp clip Video & Âm gốc theo cửa sổ làm việc
  useEffect(() => {
    if (!(timelineDuration > 0)) return
    const prevDur = mediaDurRef.current
    mediaDurRef.current = timelineDuration
    const ensure = (prev: MediaClip[], kind: 'video' | 'bg') => {
      const raw = prev.length ? prev : loadMediaClips(projectId, kind, timelineDuration)
      return normalizeMediaClips(raw, timelineDuration, prevDur)
    }
    setVideoClips((prev) => ensure(prev, 'video'))
    setBgClips((prev) => ensure(prev, 'bg'))
  }, [timelineDuration, projectId])

  useEffect(() => {
    mediaDurRef.current = 0
  }, [projectId])

  const sourceWidth = videoSize.width
  const sourceHeight = videoSize.height
  const aspectId = settings.previewAspectRatio ?? 'original'
  const crop = useMemo(
    () => resolveCropRect(sourceWidth, sourceHeight, aspectId),
    [sourceWidth, sourceHeight, aspectId],
  )
  const cropPortrait = crop.h >= crop.w

  // Video 9:16 gốc + preset landscape (16:9…) → khung preview bị cắt ngang rộng sai
  useEffect(() => {
    if (sourceWidth < 8 || sourceHeight < 8) return
    if (sourceHeight <= sourceWidth * 1.05) return
    const preset = ASPECT_PRESETS.find((p) => p.id === aspectId)
    if (!preset || !('orient' in preset) || preset.orient !== 'landscape') return
    onSettings({ ...settings, previewAspectRatio: 'original' })
  }, [sourceWidth, sourceHeight, aspectId])
  const overCoverMode = settings.coverHardsubs && settings.burnSubs
  const overlayBurnOn = overlayTextEnabled(settings)
  // layout trống + bbox giữa → mid (tránh lane ngang + khung đáy bịa)
  const layoutSegs = useMemo(
    () => segments.map((s) => withInferredLayout(s, sourceHeight > 0 ? sourceHeight : 1920)),
    [segments, sourceHeight],
  )
  const selectedLayout = selected
    ? layoutSegs.find((s) => s.id === selected.id) ?? withInferredLayout(selected, sourceHeight || 1920)
    : null
  const selectedFontPx = resolveCaptionFontSize(selectedLayout ?? undefined, settings, sourceWidth, sourceHeight)
  const fallbackBox = seedCoverBox(selectedLayout ?? undefined, sourceWidth, sourceHeight, selectedFontPx)
    ?? fallbackCoverBox(sourceWidth, sourceHeight, selectedFontPx)
  const selectedLayoutSource = resolveOverLayout(selectedLayout ?? undefined, settings, sourceWidth, sourceHeight)
  // Ưu tiên layout đã nới ngang — không dùng raw bbox hẹp (che hở chữ Trung)
  const selectedBoxSource = bboxDraft
    ?? selectedLayoutSource?.cover
    ?? (selectedLayout?.bbox ? clampCoverBox(selectedLayout.bbox, sourceWidth, sourceHeight) : null)
    ?? resolveSegmentCover(selectedLayout ?? undefined, settings, sourceWidth, sourceHeight)
    ?? fallbackBox
  const verticalWatermarkSegs = useMemo(
    () => layoutSegs.filter((s) => s.layout === 'vertical'),
    [layoutSegs],
  )
  const skipSpuriousMid = (s: Segment) =>
    midInsideVerticalWatermark(s, verticalWatermarkSegs)
  const solidAtPlayhead = solidOverlaysAt(layoutSegs, time)
  // Mask: cover mode → mọi clip; chỉ burn → vẫn che mid/dọc/nhãn (tránh chữ hiện mà không blur)
  const coverSegsRaw = settings.burnSubs
    ? (() => {
        if (overCoverMode) {
          const base = segmentsAtCover(layoutSegs, time)
          const seen = new Set(base.map((s) => s.id))
          for (const s of solidAtPlayhead) {
            if (!seen.has(s.id)) base.push(s)
          }
          return base
        }
        return solidAtPlayhead
      })()
    : []
  const timelineSegsRaw = segmentsAt(layoutSegs, time)
    .filter((s) => s.translation.trim() && !skipSpuriousMid(s))
  // Một mid / một caption ngang tại một thời điểm — tránh bbox trước đè bbox sau
  const pickOneMid = (list: Segment[]) => {
    const mids = list.filter((s) => captionLaneOf(s, sourceHeight) === 'mid')
    if (mids.length <= 1) return list
    const keep = solidMidAt(list, time, selectedId) ?? mids[0]
    return list.filter((s) => captionLaneOf(s, sourceHeight) !== 'mid' || s.id === keep.id)
  }
  const pickOneHorizontal = (list: Segment[]) => {
    const hors = list.filter((s) => captionLaneOf(s, sourceHeight) === 'horizontal')
    if (hors.length <= 1) return list
    // Ưu tiên clip đang trong [start,end); không thì cover pad mới nhất
    const active = hors.find((s) => time >= s.start && time < s.end)
      ?? hors.reduce((a, b) => (a.start >= b.start ? a : b))
    return list.filter((s) => captionLaneOf(s, sourceHeight) !== 'horizontal' || s.id === active.id)
  }
  const coverSegs = pickOneHorizontal(pickOneMid(coverSegsRaw))
  const timelineSegs = pickOneHorizontal(pickOneMid(timelineSegsRaw))
  const timelineSeg = pickTimelineSeg(layoutSegs, time, selectedId)
  const coverSeg = coverSegs.find((s) => s.id === selectedId) ?? coverSegs[0] ?? null
  // Khung kéo: Mid solid dưới gạch vàng — không phụ thuộc selected cũ (vertical/cover pad)
  const bboxSeg =
    solidMidAt(layoutSegs, time, selectedId)
    ?? solidAtPlayhead.find((s) => captionLaneOf(s, sourceHeight) === 'label')
    ?? (selectedLayout && (time >= selectedLayout.start && time < selectedLayout.end) ? selectedLayout : null)
    ?? (coverSeg && isOcrOverlayLayout(coverSeg.layout) ? coverSeg : null)
    ?? (timelineSeg && isOcrOverlayLayout(timelineSeg.layout) ? timelineSeg : null)
    ?? selectedLayout
    ?? selected
  const activeCoverDraft =
    bboxSeg && bboxDraft && bboxSeg.id === selected?.id
      ? bboxDraft
      : undefined
  const maskBoxes =
    settings.burnSubs
      ? coverSegs
          .filter((s) =>
            isOcrOverlayLayout(s.layout)
            || Boolean(effectiveOverlayLayout(s, sourceHeight))
            || (overCoverMode && s.translation.trim()),
          )
          .map((s) => {
            const override = s.id === selected?.id ? activeCoverDraft : undefined
            if (s.translation.trim()) {
              return resolvePreviewOverLayout(
                s,
                settings,
                sourceWidth,
                sourceHeight,
                crop,
                override,
              )?.mask ?? resolveCoverMaskOnly(s, sourceWidth, sourceHeight, crop, override)
            }
            return resolveCoverMaskOnly(s, sourceWidth, sourceHeight, crop, override)
          })
          .filter((b): b is PixelBox => !!b)
      : []
  const captionLayers =
    overlayBurnOn
      ? timelineSegs.map((s) => {
          // Mid/dọc/nhãn: luôn neo OCR. Caption đáy horizontal: chỉ khi cover mode.
          if (!overCoverMode && !isOcrOverlayLayout(s.layout) && !effectiveOverlayLayout(s, sourceHeight)) {
            return null
          }
          const layout = resolvePreviewOverLayout(
            s,
            settings,
            sourceWidth,
            sourceHeight,
            crop,
            s.id === selected?.id ? activeCoverDraft : undefined,
          )
          return layout ? { seg: s, layout } : null
        }).filter((x): x is { seg: Segment; layout: NonNullable<ReturnType<typeof resolvePreviewOverLayout>> } => !!x)
      : []
  const captionOverLayout =
    captionLayers.find((c) => c.seg.id === bboxSeg?.id)?.layout
    ?? captionLayers.find((c) => c.seg.id === timelineSeg?.id)?.layout
    ?? captionLayers[0]?.layout
    ?? null
  const bboxLayoutCover =
    bboxSeg && captionLayers.find((c) => c.seg.id === bboxSeg.id)?.layout.cover
  const selectedBox = bboxDraft && selected && bboxSeg?.id === selected.id
    ? bboxDraft
    : bboxLayoutCover
      ?? (bboxSeg
        ? (
            resolvePreviewOverLayout(bboxSeg, settings, sourceWidth, sourceHeight, crop)?.cover
            ?? resolveCoverMaskOnly(bboxSeg, sourceWidth, sourceHeight, crop)
            ?? (bboxSeg.bbox ? clampCoverBox(bboxSeg.bbox, sourceWidth, sourceHeight) : null)
            ?? overlayCoverSeed(bboxSeg, sourceWidth, sourceHeight)
          )
        : null)
      ?? selectedBoxSource
  // Khung kéo (handles): Caption / Cover tool / tab Vùng che chữ
  const showBboxAtPlayhead = (() => {
    if (tool === 'text') return false
    if (bboxDraft) return true
    if (!(trackFocus === 'caption' || tool === 'cover' || propTab === 'mask')) return false
    if (!selected && !bboxSeg) return false
    // Mid/nhãn: hiện tại playhead kể cả khi chưa chọn đoạn (áp dụng tất cả)
    if (bboxSeg && isOcrOverlayLayout(bboxSeg.layout) && time >= bboxSeg.start && time < bboxSeg.end) {
      return true
    }
    if (!selected) {
      // Ngang đã có bbox + cover mode: vẫn hiện mask khung khi tab mask / tool cover
      return Boolean(
        bboxSeg
        && overCoverMode
        && bboxSeg.bbox
        && time >= bboxSeg.start
        && time < bboxSeg.end,
      )
    }
    if (coverSeg?.id === selected.id || timelineSeg?.id === selected.id) return true
    if (!isOcrOverlayLayout(selected.layout)) return false
    const lane = captionLaneOf(selected)
    const otherSameLane = [...coverSegs, ...timelineSegs].some(
      (s) => s.id !== selected.id && captionLaneOf(s) === lane,
    )
    return !otherSameLane
  })()
  const activeOcrBox = selectedBox
  const captionLanes = useMemo(() => {
    const present = new Set(layoutSegs.map((s) => captionLaneOf(s, sourceHeight || 1920)))
    return CAPTION_LANE_DEFS.filter((l) => l.key === 'horizontal' || present.has(l.key))
  }, [layoutSegs, sourceHeight])
  const activeOverlays = overlays.filter((o) => time >= o.start && time < o.end)
  const selectedOverlay = overlays.find((o) => o.id === selectedOverlayId) ?? null

  useEffect(() => {
    setSpeedDraft(bakedSpeed > 0 ? bakedSpeed : bakedPreferVideo ? 0.8 : 1)
  }, [projectId, bakedSpeed, bakedPreferVideo])

  useEffect(() => {
    setFontSizeDraft(selected?.fontSize ?? 0)
  }, [selected?.id, selected?.fontSize])

  useEffect(() => () => {
    audioRef.current?.pause()
    dubAudioRef.current?.pause()
    bgAudioRef.current?.pause()
  }, [])

  const wantNoVocals =
    settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals'
  const muteOriginal =
    settings.processOriginalAudio &&
    (settings.originalAudioMode === 'mute' || settings.originalAudioMode === 'no_vocals')

  // Stem xóa lời — cùng cache với export; lần đầu Demucs có thể lâu.
  useEffect(() => {
    if (!wantNoVocals) {
      setStemStatus('off')
      setStemProgress(0)
      setStemError(null)
      bgAudioRef.current?.pause()
      bgAudioRef.current = null
      return
    }
    let dead = false
    setStemStatus('loading')
    setStemProgress(1)
    setStemError(null)
    const poll = window.setInterval(() => {
      void api.noVocalsProgress(projectId).then((p) => {
        if (dead) return
        setStemProgress(Math.max(1, Math.min(99, Math.round(p.progress || 0))))
      }).catch(() => { /* ignore poll errors while POSTing */ })
    }, 700)
    void api
      .prepareNoVocals(projectId)
      .then((res) => {
        if (dead) return
        const a = new Audio(res.audioUrl)
        a.preload = 'auto'
        bgAudioRef.current = a
        setStemProgress(100)
        setStemStatus('ready')
      })
      .catch((e: unknown) => {
        if (dead) return
        bgAudioRef.current = null
        setStemStatus('error')
        setStemError(e instanceof Error ? e.message : 'Không tách được stem xóa lời')
      })
      .finally(() => {
        window.clearInterval(poll)
      })
    return () => {
      dead = true
      window.clearInterval(poll)
    }
  }, [projectId, wantNoVocals, stemRetry])

  // Áp mute / stem ngay khi đổi filter (không đợi timeupdate).
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    syncOriginalBg(video.currentTime, !video.paused, Boolean(dubTokenRef.current))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: policy flags only
  }, [muteOriginal, wantNoVocals, stemStatus, settings.originalAudioVolume, trackMute.dub])

  function syncOriginalBg(
    videoTime: number,
    isPlaying: boolean,
    dubActive: boolean,
    playRate = 1,
    hardSync = false,
  ) {
    const video = videoRef.current
    if (!video) return
    const volMul = Math.max(0, Math.min(1, (settings.originalAudioVolume ?? 100) / 100))
    const bg = bgAudioRef.current
    const playStem = wantNoVocals && stemStatus === 'ready' && !!bg
    // Âm gốc chỉ điều khiển qua track «Âm gốc» (không duplicate mute trên Video)
    const playVideoAudio = !muteOriginal
    const rate = Math.max(0.5, Math.min(2, playRate))

    if (playStem && bg) {
      video.muted = true
      video.volume = 1
      videoMutedForDubRef.current = true
      bg.volume = Math.min(1, volMul * (dubActive ? 0.62 : 1))
      // Stem bám timeline media: cùng playbackRate với video (tránh seek liên tục khi 0.80×)
      if (Math.abs(bg.playbackRate - rate) > 0.01) bg.playbackRate = rate
      // Chỉ seek khi scrub — free-run cùng rate với video
      if (hardSync) {
        try {
          bg.currentTime = Math.max(0, videoTime)
        } catch { /* ignore */ }
      }
      if (isPlaying) {
        if (bg.paused) void bg.play().catch(() => { /* autoplay */ })
      } else {
        bg.pause()
      }
      return
    }

    bg?.pause()
    if (!playVideoAudio) {
      video.muted = true
      videoMutedForDubRef.current = true
      return
    }
    video.muted = false
    videoMutedForDubRef.current = false
    video.volume = Math.min(1, Math.max(0, volMul * (dubActive ? 0.14 : 0.42)))
  }

  function pauseDubAudio() {
    // Giữ token — pause/play không load lại file (tránh ngắt đầu câu)
    dubAudioRef.current?.pause()
    bgAudioRef.current?.pause()
    const video = videoRef.current
    const t = video?.currentTime ?? 0
    const playRate = (() => {
      const prefer = previewVideoRate(settings.matchDuration, bakedPreferVideo)
      if (prefer < 1) return prefer
      if (bakedPreferVideo) return 1
      return Math.max(0.5, Math.min(2, segmentAt(segments, t)?.videoSpeed ?? 1))
    })()
    syncOriginalBg(t, false, Boolean(dubTokenRef.current), playRate, false)
  }

  /** Đồng bộ clip TTS (+ nền). Preview: free-run TTS — không seek/cắt theo audioDuration (gây giật/thiếu). */
  function syncDubAudio(videoTime: number, isPlaying: boolean) {
    const video = videoRef.current
    if (!video || !isPlaying) {
      pauseDubAudio()
      return
    }
    const playRate = (() => {
      const prefer = previewVideoRate(settings.matchDuration, bakedPreferVideo)
      if (prefer < 1) return prefer
      if (bakedPreferVideo) return 1
      return Math.max(0.5, Math.min(2, segmentAt(segments, videoTime)?.videoSpeed ?? 1))
    })()
    if (Math.abs(video.playbackRate - playRate) > 0.01) {
      video.playbackRate = playRate
    }

    const hardSync = dubHardSyncRef.current
    dubHardSyncRef.current = false

    let seg = segmentForDub(segments, videoTime, playRate)

    // Giữ clip đang đọc tới hết file / tới câu sau (kể cả khi absEnd metadata ngắn)
    if (dubTokenRef.current) {
      const id = dubTokenRef.current.split('|')[0]
      const held = segments.find((s) => s.id === id)
      const aHold = dubAudioRef.current
      if (held?.audioUrl && aHold && !aHold.ended && videoTime >= held.start - 0.02) {
        let nextStart = Number.POSITIVE_INFINITY
        for (const s of segments) {
          if (s.start > held.start + 0.02) nextStart = Math.min(nextStart, s.start)
        }
        if (videoTime < nextStart - 0.02) seg = held
      }
    }

    if (trackMute.dub || !seg?.audioUrl) {
      if (dubTokenRef.current) {
        dubAudioRef.current?.pause()
        dubTokenRef.current = ''
      }
      syncOriginalBg(videoTime, true, false, playRate, hardSync)
      return
    }

    const speed = dubPlaybackSpeed(seg)
    const vol = Math.min(1, Math.max(0, (seg.ttsVolume ?? 100) / 100))
    const wantTime = Math.max(0, ((videoTime - seg.start) / playRate) * speed)
    const token = `${seg.id}|${seg.audioUrl}`

    syncOriginalBg(videoTime, true, true, playRate, hardSync)

    let a = dubAudioRef.current
    if (!a) {
      a = new Audio()
      a.preload = 'auto'
      dubAudioRef.current = a
    }

    const ttsRate = speed

    if (dubTokenRef.current !== token) {
      dubTokenRef.current = token
      a.pause()
      a.src = seg.audioUrl
      a.playbackRate = ttsRate
      a.volume = vol
      const startAt = () => {
        // Đổi đoạn: luôn đọc từ đầu; scrub (hardSync) mới nhảy theo playhead
        try {
          a.currentTime = hardSync ? wantTime : 0
        } catch { /* ignore */ }
        void a.play().catch(() => { /* autoplay */ })
      }
      if (a.readyState >= 1) startAt()
      else a.addEventListener('loadedmetadata', startAt, { once: true })
      return
    }

    a.playbackRate = ttsRate
    a.volume = vol
    // Chỉ hard-sync khi scrub timeline — không kéo currentTime theo timeupdate
    if (hardSync && !a.ended) {
      try {
        a.currentTime = wantTime
      } catch { /* ignore */ }
    }
    if (a.ended) return
    if (a.paused) void a.play().catch(() => { /* autoplay */ })
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

  useEffect(() => {
    if (!fitMenuOpen) return
    const close = (e: MouseEvent) => {
      if (fitMenuRef.current && !fitMenuRef.current.contains(e.target as Node)) {
        setFitMenuOpen(false)
      }
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [fitMenuOpen])

  const aspectLabel = ASPECT_PRESETS.find((p) => p.id === aspectId)?.label ?? 'Bản gốc'
  const fitMenuLabel = previewZoom === 'fit' ? 'Fit' : `${Math.round(previewZoom * 100)}%`

  function seek(segment: Segment) {
    const video = videoRef.current
    focusCaption(segment)
    if (!video) return
    video.currentTime = segment.start
    setTime(segment.start)
    void video.play().catch(() => { /* requires explicit user gesture */ })
  }

  function beginDrag(event: ReactPointerEvent, segment: Segment, mode: 'move' | 'start' | 'end') {
    if (busy || trackLocked.caption) return
    event.preventDefault()
    event.stopPropagation()
    focusCaption(segment)
    pushHistory()
    const original = { start: segment.start, end: segment.end }
    // Chỉ kẹp theo cùng lane — vertical dài (0→cuối) không đè mid khi kéo
    const lane = captionLaneOf(segment)
    const laneSegs = segments
      .filter((s) => captionLaneOf(s) === lane)
      .slice()
      .sort((a, b) => a.start - b.start)
    const index = laneSegs.findIndex((s) => s.id === segment.id)
    const before = index > 0 ? laneSegs[index - 1] : undefined
    const after = index >= 0 && index < laneSegs.length - 1 ? laneSegs[index + 1] : undefined
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
        // Giữ playhead nếu còn trong clip; đừng tua về đầu / cuối lệch
        const t = videoRef.current?.currentTime ?? time
        if (t < current.start || t >= current.end) {
          const mid = current.start + Math.max(0.05, (current.end - current.start) / 2)
          if (videoRef.current) videoRef.current.currentTime = mid
          setTime(mid)
        }
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
    // Chỉ tua playhead — không đổi track focus (đang Âm thanh/TTS thì vẫn giữ)
    const colLeft = col.getBoundingClientRect().left
    const update = (clientX: number) => {
      const px = clientX - colLeft + scroller.scrollLeft
      const nextTime = Math.max(0, Math.min(timelineDuration, px / pxPerSec))
      video.currentTime = nextTime
      setTime(nextTime)
      if (trackFocus === 'caption' || trackFocus === 'dub') {
        const current = segmentAt(segments, nextTime)
        if (current) setSelectedId(current.id)
      }
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

  /** Thanh tiến độ preview / toàn màn hình — tua theo chiều ngang bar. */
  function beginPreviewSeek(event: ReactPointerEvent<HTMLElement>) {
    if (busy || timelineDuration <= 0) return
    const video = videoRef.current
    if (!video) return
    event.preventDefault()
    const bar = event.currentTarget
    const seekTo = (clientX: number) => {
      const rect = bar.getBoundingClientRect()
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)))
      const nextTime = ratio * timelineDuration
      video.currentTime = nextTime
      setTime(nextTime)
      if (trackFocus === 'caption' || trackFocus === 'dub') {
        const current = pickTimelineSeg(segments, nextTime, selectedId)
        const cov = segmentAtCover(segments, nextTime)
        if (current) {
          setSelectedId(current.id)
        } else if (cov) {
          const prev = selectedId ? segments.find((s) => s.id === selectedId) : null
          const prevLane = prev ? captionLaneOf(prev) : null
          if (!(captionLaneOf(cov) === 'vertical' && prevLane && prevLane !== 'vertical')) {
            setSelectedId(cov.id)
          }
        }
      }
      dubHardSyncRef.current = true
      syncDubAudio(nextTime, !video.paused)
    }
    seekTo(event.clientX)
    const move = (pointer: PointerEvent) => seekTo(pointer.clientX)
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
    targetSeg?: Segment | null,
  ) {
    const seg = targetSeg ?? selected
    if (!seg || busy || tool === 'text' || trackLocked.caption) return
    if (seg.id !== selected?.id) {
      setSelectedId(seg.id)
      setTrackFocus('caption')
    }
    const canvas = canvasRef.current
    if (!canvas) return
    event.preventDefault()
    event.stopPropagation()
    setPropTab('mask')
    setTool('cover')
    const rect = canvas.getBoundingClientRect()
    // Bắt đầu từ khung đang hiện (selectedBox), không nhảy về OCR raw / fitHardsub
    const original = clampCoverBox(
      bboxDraft ?? selectedBox ?? seg.bbox ?? fallbackBox,
      sourceWidth,
      sourceHeight,
    )
    const overDrag = overCoverMode && !!seg.translation.trim()
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
      // Snap tâm chỉ khi gần giữa — Alt giữ = tắt snap (kéo thật sự tự do)
      if (mode === 'move' && !move.altKey) {
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
        const sizeChanged =
          Math.abs(norm.w - original.w) > 2 || Math.abs(norm.h - original.h) > 2
        // Mid/dọc/nhãn: khi đổi cỡ khung → fit chữ theo khung mới (đừng giữ fontSize cũ → chữ tụt bé)
        if (
          isOcrOverlayLayout(seg.layout)
          && seg.translation.trim()
          && settings.burnSubs
        ) {
          const lockFs = resolveOverlayFontPreferred(seg)
          const preferred = sizeChanged
            ? lockFs
            : (lockFs || Number(seg.captionLayout?.fontSize) || 0)
          const laid = layoutOcrOverlay(
            seg.layout,
            norm,
            seg.translation,
            preferred,
            sourceWidth,
            sourceHeight,
          )
          onChange(segmentWithLayout(seg, {
            cover: norm,
            caption: laid.caption,
            lines: laid.lines,
            fontPx: laid.fontPx,
          }, laid.fontPx))
          return
        }
        if (overDrag) {
          const fontPx = resolveCaptionFontSize(seg, settings, sourceWidth, sourceHeight)
          const layout = manualCoverLayout(norm, seg.translation, fontPx, sourceWidth, sourceHeight, true)
          onChange(segmentWithLayout(seg, { ...layout, cover: norm }, fontPx))
        } else {
          onChange({ ...seg, bbox: norm, captionLayout: seg.captionLayout ?? null })
        }
      }
    }
    window.addEventListener('pointermove', update)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function beginOverlayDrag(event: ReactPointerEvent, overlay: TextOverlay) {
    if (busy || tool === 'text' || trackLocked.text) return
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

  function applyFontSize(scope: 'one' | 'all', sizeOverride?: number) {
    const size = sizeOverride !== undefined ? sizeOverride : fontSizeDraft
    setFontSizeDraft(size)
    const relayout = (seg: Segment): Segment => {
      if (!seg.translation.trim() || !(settings.coverHardsubs && settings.burnSubs)) {
        return { ...seg, fontSize: size, captionLayout: null }
      }
      if (isOcrOverlayLayout(seg.layout)) {
        const preferred = size > 0 ? size : 0
        const seed = overlayCoverSeed({ ...seg, fontSize: preferred }, sourceWidth, sourceHeight)
        if (!seed) return { ...seg, fontSize: preferred }
        const laid = layoutOcrOverlay(seg.layout, seed, seg.translation, preferred, sourceWidth, sourceHeight)
        return segmentWithLayout({ ...seg, fontSize: preferred, captionLayout: null }, {
          cover: laid.cover,
          caption: laid.caption,
          lines: laid.lines,
          fontPx: laid.fontPx,
        }, laid.fontPx)
      }
      const fontPx = resolveCaptionFontSize({ ...seg, fontSize: size }, settings, sourceWidth, sourceHeight)
      const base = seg.bbox
        ? clampCoverBox(seg.bbox, sourceWidth, sourceHeight)
        : resolveSegmentCover(seg, settings, sourceWidth, sourceHeight)
          ?? seedCoverBox(seg, sourceWidth, sourceHeight, fontPx)
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

  /** Tốc độ video: draft + Áp dụng bake lại toàn bộ preview. */
  async function applyVideoSpeed(_scope: 'one' | 'all', speed?: number) {
    const v = Math.max(0.5, Math.min(2, speed ?? speedDraft))
    setSpeedDraft(v)
    if (speedBusy || busy) return
    setSpeedBusy(true)
    setSpeedError(null)
    try {
      const res = await api.rebakeSpeed(projectId, v)
      onPreviewRebaked?.(res)
      if (!onPreviewRebaked) {
        void onSegmentsReplace(res.segments.map((s, i) => ({ ...s, index: i, videoSpeed: v })))
      }
    } catch (e) {
      setSpeedError(e instanceof Error ? e.message : String(e))
    } finally {
      setSpeedBusy(false)
    }
  }

  async function previewTts(forSeg?: Segment) {
    const target = forSeg ?? selected
    if (!target || ttsBusy) return
    if (forSeg) setSelectedId(forSeg.id)
    setTtsBusy(true); setTtsError(null)
    pauseDubAudio()
    try {
      const voice = target.voice || settings.defaultVoice
      const result = await api.previewTts(projectId, target.id, {
        text: target.translation,
        voice,
        lang: settings.targetLang === 'none' ? 'vi' : settings.targetLang,
      })
      onChange({ ...target, audioUrl: result.audioUrl, audioDuration: result.duration })
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
    setSelectedOverlayId(overlay.id)
    setTrackFocus('text')
    setTool('select')
    setPropTab('overlay')
    pushHistory()
    onOverlayChange(overlay, true)
  }

  function focusCaption(seg: Segment) {
    setSelectedOverlayId(null)
    setSelectedMediaId(null)
    setSelectedId(seg.id)
    setTrackFocus('caption')
    setPropTab('caption')
  }

  function focusDub(seg: Segment) {
    setSelectedOverlayId(null)
    setSelectedMediaId(null)
    setSelectedId(seg.id)
    setTrackFocus('dub')
    setPropTab('audio')
  }

  function focusBg(clipId?: string) {
    setSelectedOverlayId(null)
    setTrackFocus('bg')
    setPropTab('audio')
    const clip = (clipId ? bgClips.find((c) => c.id === clipId) : null)
      ?? clipAtTime(bgClips, time)
      ?? bgClips[0]
    setSelectedMediaId(clip?.id ?? null)
  }

  function focusVideo(clipId?: string) {
    setSelectedOverlayId(null)
    setSelectedId(null)
    setTrackFocus('video')
    setPropTab('video')
    if (tool === 'cover') setTool('select')
    const clip = (clipId ? videoClips.find((c) => c.id === clipId) : null)
      ?? clipAtTime(videoClips, time)
      ?? videoClips[0]
    setSelectedMediaId(clip?.id ?? null)
  }

  function focusText(overlayId: string) {
    setSelectedOverlayId(overlayId)
    setSelectedMediaId(null)
    setTrackFocus('text')
    setPropTab('overlay')
  }

  /** Chọn clip: giữ playhead nếu đã trong [start,end) hoặc cover pad (mid/OCR). */
  function selectClipKeepPlayhead(start: number, end: number, cover?: { start: number; end: number }) {
    const lo = cover ? Math.min(start, cover.start) : start
    const hi = cover ? Math.max(end, cover.end) : end
    if (time < lo || time >= hi) {
      const mid = start + Math.max(SPLIT_EDGE, Math.min((end - start) / 2, end - start - SPLIT_EDGE))
      seekPlayhead(mid)
    }
  }

  function rangeUnderPlayhead(start: number, end: number) {
    return time > start + SPLIT_EDGE && time < end - SPLIT_EDGE
  }

  type ToolTarget =
    | { kind: 'seg'; seg: Segment }
    | { kind: 'ov'; ov: TextOverlay }
    | { kind: 'media'; track: 'video' | 'bg'; clip: MediaClip }

  /** Target = đúng track đang focus (Video / Âm gốc / Caption / TTS / Text độc lập) */
  const editTarget: ToolTarget | null = (() => {
    if (trackFocus === 'video') {
      const byId = selectedMediaId ? videoClips.find((c) => c.id === selectedMediaId) : undefined
      const under = clipAtTime(videoClips, time)
      const clip = (byId && rangeUnderPlayhead(byId.start, byId.end) ? byId : null) || under || byId
      return clip ? { kind: 'media', track: 'video', clip } : null
    }
    if (trackFocus === 'bg') {
      const byId = selectedMediaId ? bgClips.find((c) => c.id === selectedMediaId) : undefined
      const under = clipAtTime(bgClips, time)
      const clip = (byId && rangeUnderPlayhead(byId.start, byId.end) ? byId : null) || under || byId
      return clip ? { kind: 'media', track: 'bg', clip } : null
    }
    if (trackFocus === 'text') {
      return selectedOverlay ? { kind: 'ov', ov: selectedOverlay } : null
    }
    if (trackFocus === 'caption' || trackFocus === 'dub') {
      if (selected && rangeUnderPlayhead(selected.start, selected.end)) {
        return { kind: 'seg', seg: selected }
      }
      const at = segmentAt(segments, time)
      if (at && rangeUnderPlayhead(at.start, at.end)) return { kind: 'seg', seg: at }
      return selected ? { kind: 'seg', seg: selected } : null
    }
    return null
  })()

  function clipRange(target: NonNullable<typeof editTarget>) {
    if (target.kind === 'seg') return { start: target.seg.start, end: target.seg.end }
    if (target.kind === 'ov') return { start: target.ov.start, end: target.ov.end }
    return { start: target.clip.start, end: target.clip.end }
  }

  const playheadInClip = (() => {
    if (!editTarget) return false
    const { start, end } = clipRange(editTarget)
    return rangeUnderPlayhead(start, end)
  })()
  const canTrimLeft = (() => {
    if (!editTarget || busy) return false
    const { start, end } = clipRange(editTarget)
    return time > start + 0.02 && time <= end - MIN_CLIP_SEC
  })()
  const canTrimRight = (() => {
    if (!editTarget || busy) return false
    const { start, end } = clipRange(editTarget)
    return time >= start + MIN_CLIP_SEC && time < end - 0.02
  })()
  const canSplit = Boolean(
    editTarget &&
      !busy &&
      playheadInClip &&
      clipRange(editTarget).end - clipRange(editTarget).start > SPLIT_EDGE * 2 + 0.02,
  )
  const canDuplicate = Boolean(editTarget && !busy)
  const canDeleteClip = Boolean(
    editTarget &&
      !busy &&
      !(editTarget.kind === 'media' && (editTarget.track === 'video' ? videoClips : bgClips).length <= 1),
  )
  const bookmarkActive = bookmarks.some((b) => Math.abs(b - time) <= BOOKMARK_EPS)

  const splitDisabledReason = !editTarget
    ? 'Click chọn clip trên track (Video / Caption / TTS / Âm gốc / Text) trước'
    : !playheadInClip
      ? 'Đặt playhead vào giữa clip đang chọn rồi Split'
      : ''

  function seekPlayhead(next: number) {
    const video = videoRef.current
    const clamped = Math.max(0, Math.min(timelineDuration, next))
    if (video) video.currentTime = clamped
    setTime(clamped)
    const current = segmentAt(segments, clamped)
    if (current) setSelectedId(current.id)
  }

  function splitAtPlayhead() {
    if (!editTarget || !canSplit) return
    pushHistory()
    const t = time
    if (editTarget.kind === 'ov') {
      const ov = editTarget.ov
      onOverlayChange({ ...ov, end: t })
      onOverlayChange(
        { ...ov, id: crypto.randomUUID(), start: t, end: ov.end },
        true,
      )
      return
    }
    if (editTarget.kind === 'media') {
      if (editTarget.track === 'video') {
        const next = splitMediaList(videoClips, editTarget.clip.id, t)
        setVideoClips(next)
        setSelectedMediaId(next.find((c) => c.start === t)?.id ?? null)
      } else {
        const next = splitMediaList(bgClips, editTarget.clip.id, t)
        setBgClips(next)
        setSelectedMediaId(next.find((c) => c.start === t)?.id ?? null)
      }
      return
    }
    const seg = editTarget.seg
    const left: Segment = { ...seg, end: t }
    const right: Segment = {
      ...seg,
      id: crypto.randomUUID(),
      start: t,
      end: seg.end,
      audioUrl: undefined,
      audioFile: undefined,
      audioDuration: undefined,
      captionLayout: null,
    }
    if (trackFocus === 'dub') {
      left.dub = seg.dub
      right.dub = true
    }
    void onSegmentsReplace(
      reindexSegments(segments.flatMap((s) => (s.id === seg.id ? [left, right] : [s]))),
    )
    setSelectedId(right.id)
  }

  function trimLeftToPlayhead() {
    if (!editTarget || !canTrimLeft) return
    pushHistory()
    const t = time
    if (editTarget.kind === 'ov') {
      onOverlayChange({ ...editTarget.ov, start: Math.min(t, editTarget.ov.end - MIN_CLIP_SEC) })
      return
    }
    if (editTarget.kind === 'media') {
      const start = Math.min(t, editTarget.clip.end - MIN_CLIP_SEC)
      const patch = (list: MediaClip[]) =>
        list.map((c) => (c.id === editTarget.clip.id ? { ...c, start } : c))
      if (editTarget.track === 'video') setVideoClips(patch)
      else setBgClips(patch)
      return
    }
    const seg = editTarget.seg
    void onChange({ ...seg, start: Math.min(t, seg.end - MIN_CLIP_SEC), captionLayout: null })
  }

  function trimRightToPlayhead() {
    if (!editTarget || !canTrimRight) return
    pushHistory()
    const t = time
    if (editTarget.kind === 'ov') {
      onOverlayChange({ ...editTarget.ov, end: Math.max(t, editTarget.ov.start + MIN_CLIP_SEC) })
      return
    }
    if (editTarget.kind === 'media') {
      const end = Math.max(t, editTarget.clip.start + MIN_CLIP_SEC)
      const patch = (list: MediaClip[]) =>
        list.map((c) => (c.id === editTarget.clip.id ? { ...c, end } : c))
      if (editTarget.track === 'video') setVideoClips(patch)
      else setBgClips(patch)
      return
    }
    const seg = editTarget.seg
    void onChange({
      ...seg,
      end: Math.max(t, seg.start + MIN_CLIP_SEC),
      captionLayout: null,
      audioUrl: undefined,
      audioFile: undefined,
      audioDuration: undefined,
    })
  }

  function duplicateClip() {
    if (!editTarget || !canDuplicate) return
    pushHistory()
    if (editTarget.kind === 'ov') {
      const ov = editTarget.ov
      const dur = ov.end - ov.start
      const start = Math.min(timelineDuration - MIN_CLIP_SEC, ov.end)
      onOverlayChange(
        { ...ov, id: crypto.randomUUID(), start, end: Math.min(timelineDuration, start + dur) },
        true,
      )
      return
    }
    if (editTarget.kind === 'media') {
      const c = editTarget.clip
      const dur = c.end - c.start
      const start = Math.min(timelineDuration - MIN_CLIP_SEC, c.end)
      const copy: MediaClip = {
        id: crypto.randomUUID(),
        start,
        end: Math.min(timelineDuration, start + dur),
      }
      if (editTarget.track === 'video') {
        setVideoClips((list) => [...list, copy].sort((a, b) => a.start - b.start))
      } else {
        setBgClips((list) => [...list, copy].sort((a, b) => a.start - b.start))
      }
      setSelectedMediaId(copy.id)
      return
    }
    const seg = editTarget.seg
    const dur = seg.end - seg.start
    const start = Math.min(timelineDuration - MIN_CLIP_SEC, seg.end)
    const copy: Segment = {
      ...seg,
      id: crypto.randomUUID(),
      start,
      end: Math.min(timelineDuration, start + dur),
      audioUrl: undefined,
      audioFile: undefined,
      audioDuration: undefined,
      captionLayout: null,
    }
    void onSegmentsReplace(reindexSegments([...segments, copy]))
    setSelectedId(copy.id)
  }

  function deleteSelectedClip() {
    if (!editTarget || !canDeleteClip) return
    pushHistory()
    if (editTarget.kind === 'ov') {
      onOverlayDelete(editTarget.ov.id)
      setSelectedOverlayId(null)
      return
    }
    if (editTarget.kind === 'media') {
      const id = editTarget.clip.id
      if (editTarget.track === 'video') {
        const next = videoClips.filter((c) => c.id !== id)
        setVideoClips(next.length ? next : [fullMediaClip(timelineDuration)])
        setSelectedMediaId(next[0]?.id ?? null)
      } else {
        const next = bgClips.filter((c) => c.id !== id)
        setBgClips(next.length ? next : [fullMediaClip(timelineDuration)])
        setSelectedMediaId(next[0]?.id ?? null)
      }
      return
    }
    const id = editTarget.seg.id
    const next = segments.filter((s) => s.id !== id)
    void onSegmentsReplace(reindexSegments(next))
    setSelectedId(next[0]?.id ?? null)
  }

  function extractAudioFromVideo() {
    pushHistory()
    onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
    setPropTab('audio')
  }

  function toggleBookmarkAtPlayhead() {
    pushHistory()
    const t = Math.round(time * 1000) / 1000
    setBookmarks((prev) => {
      const hit = prev.find((b) => Math.abs(b - t) <= BOOKMARK_EPS)
      if (hit !== undefined) return prev.filter((b) => b !== hit)
      return [...prev, t].sort((a, b) => a - b)
    })
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
        case 'KeyZ':
          if (event.ctrlKey || event.metaKey) {
            event.preventDefault()
            if (event.shiftKey) redoEdit()
            else undoEdit()
          }
          break
        case 'KeyY':
          if (event.ctrlKey || event.metaKey) {
            event.preventDefault()
            redoEdit()
          }
          break
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
        case 'KeyS':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); splitAtPlayhead() }
          break
        case 'KeyB':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); toggleBookmarkAtPlayhead() }
          break
        case 'KeyF':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); toggleFullscreen() }
          break
        case 'Escape':
          setSelectedOverlayId(null); setTool('select')
          break
        case 'Delete':
        case 'Backspace':
          if (canDeleteClip) {
            event.preventDefault()
            deleteSelectedClip()
          }
          break
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  })

  /* Effective properties tab: overlay tab only valid while an overlay is selected */
  const effectivePropTab: PropTab = (() => {
    let t: PropTab = propTab === 'overlay' && !selectedOverlay ? 'caption' : propTab
    // Caption/overlay cần đoạn chọn; mask + video + audio luôn dùng được (kiểu mặt nạ project)
    if (!selected && (t === 'caption' || t === 'overlay')) t = 'video'
    return t
  })()
  const isOverlaySeg = isOcrOverlayLayout(selected?.layout)
  const dubOn = selected?.layout === 'vertical' || selected?.layout === 'label'
    ? selected?.dub === true
    : selected?.dub !== false
  const focusCaptionSeg = timelineSeg ?? selected
  const overlayLaidFont =
    captionOverLayout?.fontPx
    ?? (timelineSeg && isOcrOverlayLayout(timelineSeg.layout) && captionOverLayout
      ? fitOverlayFontPx(
          timelineSeg.layout,
          captionOverLayout.cover,
          timelineSeg.translation,
          resolveOverlayFontPreferred(timelineSeg),
        )
      : undefined)
  const activeCaptionPx = overlayLaidFont
    ?? resolveCaptionFontSize(focusCaptionSeg ?? undefined, settings, sourceWidth, sourceHeight)
  const placement = captionPlacement(settings)
  // below/above: chỉ caption ngang — không đụng mid/dọc (tránh chữ đáy trong khi OCR ở giữa)
  const activeCaptionBox =
    overlayBurnOn
    && timelineSeg?.translation.trim()
    && placement !== 'over'
    && !isOcrOverlayLayout(timelineSeg.layout)
    && !effectiveOverlayLayout(timelineSeg, sourceHeight)
    // CJK chưa OCR bbox → không đoán chỗ chữ (tránh emerald bay lệch)
    && !(isCjkHardsubSource(timelineSeg.source) && !timelineSeg.bbox)
    && activeOcrBox
      ? estimatePreviewCaptionBox(
          activeOcrBox,
          timelineSeg.translation,
          activeCaptionPx,
          sourceWidth,
          sourceHeight,
          crop,
          placement,
        )
      : null
  const showCoverBlur = settings.burnSubs && maskBoxes.length > 0
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
    const prev = clampCoverBox(selectedBoxSource, sourceWidth, sourceHeight)
    const sizeChanged =
      Math.abs(norm.w - prev.w) > 2 || Math.abs(norm.h - prev.h) > 2
    if (
      isOcrOverlayLayout(selected.layout)
      && selected.translation.trim()
      && settings.burnSubs
    ) {
      const lockFs = resolveOverlayFontPreferred(selected)
      const preferred = sizeChanged
        ? lockFs
        : (lockFs || Number(selected.captionLayout?.fontSize) || 0)
      const laid = layoutOcrOverlay(
        selected.layout,
        norm,
        selected.translation,
        preferred,
        sourceWidth,
        sourceHeight,
      )
      onChange(segmentWithLayout(selected, {
        cover: norm,
        caption: laid.caption,
        lines: laid.lines,
        fontPx: laid.fontPx,
      }, laid.fontPx))
      return
    }
    if (overCoverMode && selected.translation.trim()) {
      const layout = manualCoverLayout(norm, selected.translation, selectedFontPx, sourceWidth, sourceHeight, true)
      onChange(segmentWithLayout(selected, { ...layout, cover: norm }, selectedFontPx))
      return
    }
    onChange({ ...selected, bbox: norm, captionLayout: null })
  }

  /** Kéo vùng che full ngang (~96% khung), giữ Y/Cao hiện tại. */
  function stretchCoverFullWidth() {
    if (!selected || sourceWidth <= 0) return
    const cur = selectedBox
    const w = Math.min(sourceWidth, Math.round(sourceWidth * 0.96))
    const x = Math.round((sourceWidth - w) / 2)
    commitCoverBox({ x, w, y: cur.y, h: cur.h })
  }

  /** Áp dụng khung che hiện tại cho mọi caption ngang (bỏ label/dọc). */
  function applyCoverMaskToAll() {
    const srcSeg = selected ?? bboxSeg
    if (!srcSeg || sourceWidth <= 0 || sourceHeight <= 0) return
    const box = clampCoverBox(
      (selected && selected.id === srcSeg.id ? selectedBox : null)
        ?? resolveCoverMaskOnly(srcSeg, sourceWidth, sourceHeight, crop)
        ?? (srcSeg.bbox ? clampCoverBox(srcSeg.bbox, sourceWidth, sourceHeight) : null)
        ?? selectedBoxSource,
      sourceWidth,
      sourceHeight,
    )
    const next = segments.map((seg) => {
      if (!seg.translation.trim()) return seg
      if (isOcrOverlayLayout(seg.layout)) return seg
      const fontPx = resolveCaptionFontSize(seg, settings, sourceWidth, sourceHeight)
      if (overCoverMode) {
        const layout = manualCoverLayout(box, seg.translation, fontPx, sourceWidth, sourceHeight, true)
        return segmentWithLayout(seg, layout, fontPx)
      }
      return { ...seg, bbox: { ...box }, captionLayout: null }
    })
    void onSegmentsReplace(next)
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
          <span
            className="text-xs text-muted-foreground max-w-[220px] truncate"
            title="Quy tắc khớp thời lượng từ cài đặt Sidebar (đầu vào trước khi mở xem/sửa)"
          >
            {settings.matchDuration === 'preferVideo'
              ? (bakedPreferVideo ? 'Khớp: đã chậm video 0.80× (bake)' : 'Khớp: chậm video 0.80×')
              : settings.matchDuration === 'stretch'
                ? 'Khớp: kéo TTS'
                : settings.matchDuration === 'natural'
                  ? 'Khớp: tốc độ tự nhiên'
                  : settings.matchDuration === 'none'
                    ? 'Khớp: không'
                    : 'Khớp: theo cài đặt'}
          </span>
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
          <ResizablePanel id="main" defaultSize={62} minSize={40} maxSize={82} className="min-h-0">
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
                        <p className="px-2 py-6 text-center text-[12px] text-muted-foreground">
                          Media sắp ra mắt...
                        </p>
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
                <div ref={previewRef} className="panel preview-panel bg-background relative flex size-full min-h-0 min-w-0 flex-col rounded-sm border border-border overflow-hidden">

                  {/* Viewport — Fit = vừa panel; % = scale + scroll */}
                  <div
                    className={cn(
                      'flex-1 min-h-0 w-full flex items-center justify-center px-3 pt-2',
                      previewZoom === 'fit' ? 'overflow-hidden' : 'overflow-auto',
                    )}
                  >
                    <div
                      className={cn(
                        previewZoom === 'fit' && 'flex h-full w-full min-h-0 min-w-0 items-center justify-center',
                      )}
                      style={
                        previewZoom === 'fit'
                          ? undefined
                          : {
                              transform: `scale(${previewZoom})`,
                              transformOrigin: 'center center',
                              flexShrink: 0,
                            }
                      }
                    >
                    <div
                      ref={canvasRef}
                      className={cn(
                        // Không gắn container-type lên root — containment giết backdrop-filter → mask «Làm mờ» mất tác dụng
                        'relative shadow-lg',
                        previewZoom === 'fit' && 'max-h-full max-w-full',
                        previewZoom === 'fit' && (cropPortrait ? 'h-full w-auto' : 'w-full h-auto'),
                        tool === 'text' ? 'cursor-crosshair' : tool === 'cover' ? 'cursor-cell' : 'cursor-default',
                      )}
                      style={{
                        aspectRatio: `${crop.w} / ${crop.h}`,
                        ...(previewZoom !== 'fit'
                          ? (() => {
                              const base = 480
                              if (cropPortrait) {
                                const h = Math.min(base, crop.h)
                                return { height: h, width: Math.round(h * (crop.w / crop.h)) }
                              }
                              const w = Math.min(base, crop.w)
                              return { width: w, height: Math.round(w * (crop.h / crop.w)) }
                            })()
                          : {}),
                      }}
                      onPointerDown={(event) => {
                        if (tool === 'text') addTextOverlay(event.clientX, event.clientY)
                      }}
                    >
                      <div className="absolute inset-0 overflow-hidden bg-black">
                        <video
                          key={videoUrl}
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
                            // Preview clip: đứng ở đầu cửa sổ làm việc
                            if (workClipSec > 0 && event.currentTarget.currentTime > workClipSec) {
                              event.currentTarget.currentTime = 0
                            }
                          }}
                          onTimeUpdate={(event) => {
                            let current = event.currentTarget.currentTime
                            // Không cho chạy quá cửa sổ preview (xuất cũng chỉ đoạn này)
                            if (workClipSec > 0 && current >= workClipSec - 0.04) {
                              current = workClipSec
                              event.currentTarget.pause()
                              event.currentTarget.currentTime = workClipSec
                              setPlaying(false)
                              pauseDubAudio()
                            }
                            setTime(current)
                            // Focus Video/BG: xem clip — không nhảy chọn Mid/Dọc (tránh hiện khung kéo)
                            if (trackFocus === 'caption' || trackFocus === 'dub') {
                              const now = pickTimelineSeg(segments, current, selectedId)
                              const cov = segmentAtCover(segments, current)
                              if (now) {
                                setSelectedId(now.id)
                              } else if (cov) {
                                const prev = selectedId ? segments.find((s) => s.id === selectedId) : null
                                const prevLane = prev ? captionLaneOf(prev) : null
                                if (captionLaneOf(cov) === 'vertical' && prevLane && prevLane !== 'vertical') {
                                  /* keep */
                                } else {
                                  setSelectedId(cov.id)
                                }
                              }
                            }
                            const playRate = previewVideoRate(settings.matchDuration, bakedPreferVideo)
                            event.currentTarget.playbackRate = playRate < 1
                              ? playRate
                              : bakedPreferVideo
                                ? 1
                                : (pickTimelineSeg(segments, current, selectedId)?.videoSpeed ?? 1)
                            syncDubAudio(current, !event.currentTarget.paused)
                          }}
                          onSeeked={(event) => {
                            const t = event.currentTarget.currentTime
                            if (trackFocus === 'caption' || trackFocus === 'dub') {
                              const current = pickTimelineSeg(segments, t, selectedId)
                              const cov = segmentAtCover(segments, t)
                              if (current) {
                                setSelectedId(current.id)
                              } else if (cov) {
                                const prev = selectedId ? segments.find((s) => s.id === selectedId) : null
                                const prevLane = prev ? captionLaneOf(prev) : null
                                if (captionLaneOf(cov) === 'vertical' && prevLane && prevLane !== 'vertical') {
                                  /* keep */
                                } else {
                                  setSelectedId(cov.id)
                                }
                              }
                            }
                            // Scrub timeline → ép TTS/stem khớp lại một lần
                            dubHardSyncRef.current = true
                            syncDubAudio(event.currentTarget.currentTime, !event.currentTarget.paused)
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

                      {/* Blur che chữ — khớp kiểu Làm mờ xuất */}
                      {showCoverBlur && tool !== 'text' && maskBoxes.map((box, i) => (
                        <div
                          key={`mask-${i}-${box.x}-${box.y}`}
                          className="absolute z-[9] pointer-events-none overflow-hidden"
                          style={{
                            ...sourceToDisplayStyle(box, crop),
                            ...coverMaskPreviewStyle(coverMaskStyle, coverMaskColor, coverMaskOpacity),
                          }}
                          aria-hidden
                        />
                      ))}

                      {/* Bbox kéo — suốt thanh vàng Mid [start,end); không ẩn sớm vì selected cũ */}
                      {bboxSeg && selectedBox && showBboxAtPlayhead && tool !== 'text' && (
                        <div
                          className={cn(
                            'absolute border-2 border-violet-400 cursor-move z-10 overflow-hidden',
                            !showCoverBlur && 'bg-violet-900/10 border-dashed',
                            draggingBox && 'opacity-80 ring-2 ring-violet-300',
                            (tool === 'cover' || effectivePropTab === 'mask') && 'border-yellow-400 ring-1 ring-yellow-400/50',
                          )}
                          style={{
                            ...sourceToDisplayStyle(selectedBox, crop),
                            ...(!maskBoxes.length && showCoverBlur
                              ? coverMaskPreviewStyle(coverMaskStyle, coverMaskColor, coverMaskOpacity)
                              : {}),
                          }}
                          onPointerDown={(e) => beginBboxDrag(e, 'move', bboxSeg)}
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
                              onPointerDown={(e) => { e.stopPropagation(); beginBboxDrag(e, handle, bboxSeg) }}
                            />
                          ))}
                          {(effectivePropTab === 'mask' || draggingBox) && (
                            <span className="absolute -top-5 left-0 bg-violet-600/90 text-white text-[10px] px-1.5 py-0.5 rounded pointer-events-none whitespace-nowrap z-30">
                              Vùng che · kéo góc/cạnh tự do
                            </span>
                          )}
                        </div>
                      )}

                      {/* Phụ đề dịch — mọi clip overlapping (mid + dọc + đáy cùng lúc) */}
                      {captionLayers.map(({ seg: layerSeg, layout: layerLayout }) => {
                        const overlayLay = effectiveOverlayLayout(layerSeg, sourceHeight)
                        const fontPx = layerLayout.fontPx
                          ?? (overlayLay
                            ? fitOverlayFontPx(
                                overlayLay,
                                layerLayout.cover,
                                layerSeg.translation,
                                resolveOverlayFontPreferred(layerSeg),
                              )
                            : activeCaptionPx)
                        const lines = layerLayout.lines.length
                          ? layerLayout.lines
                          : [layerSeg.translation]
                        return (
                        <div
                          key={layerSeg.id}
                          className={cn(
                            '@container [container-type:size] absolute z-20 pointer-events-none flex items-center justify-center',
                            overlayLay === 'vertical' && 'px-0.5 overflow-hidden',
                            overlayLay === 'mid' && 'overflow-visible',
                            overlayLay !== 'vertical' && overlayLay !== 'mid' && 'overflow-hidden',
                          )}
                          style={sourceToDisplayStyle(
                            // Mid/dọc: chữ trong caption (đã pad); mask tím = cover
                            overlayLay === 'vertical' || overlayLay === 'mid'
                              ? layerLayout.caption
                              : layerLayout.cover,
                            crop,
                          )}
                        >
                          {overlayLay === 'vertical' ? (
                            <div
                              className="text-white font-bold drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)]"
                              style={{
                                display: 'flex',
                                flexDirection: 'column',
                                alignItems: 'center',
                                justifyContent: 'center',
                                gap: '0.12em',
                                width: '100%',
                                height: '100%',
                                overflow: 'hidden',
                                margin: 0,
                                padding: '0.08em 0.06em',
                                boxSizing: 'border-box',
                                ...overlayDisplayFontStyle('vertical', layerLayout.caption, fontPx, lines.length),
                              }}
                            >
                              {lines.map((unit, i) => (
                                <span
                                  key={i}
                                  style={{
                                    fontSize: 'inherit',
                                    lineHeight: 1,
                                    whiteSpace: 'nowrap',
                                    maxWidth: '100%',
                                    overflow: 'hidden',
                                    writingMode: 'horizontal-tb',
                                  }}
                                >
                                  {unit}
                                </span>
                              ))}
                            </div>
                          ) : overlayLay === 'label' || overlayLay === 'mid' ? (
                            <p
                              className={cn(
                                'w-full h-full text-center text-white font-bold drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)] flex flex-col items-center justify-center',
                                overlayLay === 'mid' ? 'overflow-visible' : 'overflow-hidden break-words',
                              )}
                              style={{
                                ...overlayDisplayFontStyle(
                                  overlayLay,
                                  overlayLay === 'mid' ? layerLayout.caption : layerLayout.cover,
                                  fontPx,
                                  lines.length,
                                ),
                                whiteSpace: overlayLay === 'mid' ? 'normal' : 'pre-wrap',
                                padding: overlayLay === 'mid' ? '0' : '0.04em 0.08em',
                                boxSizing: 'border-box',
                                margin: 0,
                              }}
                            >
                              {lines.map((line, i) => (
                                <span
                                  key={i}
                                  className="block w-full text-center"
                                >
                                  {line}
                                </span>
                              ))}
                            </p>
                          ) : (
                            <p
                              className={cn(
                                'w-full text-center text-white font-bold drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)]',
                                layerLayout.lines.length === 1 && 'whitespace-nowrap',
                              )}
                              style={{
                                ...captionFontStyle(
                                  fontPx,
                                  layerLayout.lines.length === 1
                                    ? layerLayout.cover.w
                                    : layerLayout.cover.h,
                                  layerLayout.lines.length === 1 ? 'w' : 'h',
                                ),
                                lineHeight: 1.12,
                                margin: 0,
                              }}
                            >
                              {layerLayout.lines.length === 1
                                ? layerSeg.translation
                                : layerLayout.lines.map((line, i) => (
                                  <span key={i} className="whitespace-nowrap">{i > 0 && <br />}{line}</span>
                                ))}
                            </p>
                          )}
                        </div>
                        )
                      })}
                      {activeCaptionBox && timelineSeg && !isOcrOverlayLayout(timelineSeg.layout) && (
                        <div
                          className="@container [container-type:size] absolute z-20 pointer-events-none flex items-center justify-center border border-dashed border-emerald-400/70 bg-black/35"
                          style={sourceToDisplayStyle(activeCaptionBox, crop)}
                        >
                          <p
                            className="w-full text-center text-white font-bold leading-none drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)]"
                            style={captionFontStyle(activeCaptionPx, activeCaptionBox.h)}
                          >
                            {timelineSeg.translation}
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
                  </div>

                  {/* Thanh tiến độ — preview + toàn màn hình (kéo tua) */}
                  {timelineDuration > 0 && (
                    <div className="preview-seek-wrap shrink-0 px-4 pb-1 pt-2">
                      <div
                        role="slider"
                        aria-label="Tiến độ phát"
                        aria-valuemin={0}
                        aria-valuemax={timelineDuration}
                        aria-valuenow={time}
                        className="group relative h-2 cursor-pointer rounded-full bg-muted/80 touch-none"
                        onPointerDown={beginPreviewSeek}
                      >
                        <div
                          className="pointer-events-none absolute inset-y-0 left-0 rounded-full bg-primary/90"
                          style={{ width: `${Math.min(100, (time / timelineDuration) * 100)}%` }}
                        />
                        <div
                          className="pointer-events-none absolute top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-background bg-primary opacity-0 shadow transition-opacity group-hover:opacity-100"
                          style={{ left: `calc(${Math.min(100, (time / timelineDuration) * 100)}% - 6px)` }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Preview toolbar — OpenCut: grid-cols-[1fr_auto_1fr] pb-3 pt-5 px-5 */}
                  <div className="preview-toolbar grid grid-cols-[1fr_auto_1fr] items-center pb-2 pt-2 px-4 shrink-0">
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
                      <div ref={fitMenuRef} className="relative flex items-center gap-0.5">
                        {fitMenuOpen && (
                          <div className="absolute bottom-full right-0 mb-2 w-[120px] rounded-lg border border-border bg-popover py-1 shadow-lg text-popover-foreground text-[13px] z-50">
                            <button
                              type="button"
                              className={cn(
                                'flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent',
                                previewZoom === 'fit' && 'text-primary',
                              )}
                              onClick={() => { setPreviewZoom('fit'); setFitMenuOpen(false) }}
                            >
                              <span className="w-4 shrink-0 text-primary">{previewZoom === 'fit' ? '✓' : ''}</span>
                              Fit
                            </button>
                            <div className="my-1 border-t border-border" />
                            {PREVIEW_ZOOM_PRESETS.map((z) => (
                              <button
                                key={z}
                                type="button"
                                className={cn(
                                  'flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent',
                                  previewZoom === z && 'text-primary',
                                )}
                                onClick={() => { setPreviewZoom(z); setFitMenuOpen(false) }}
                              >
                                <span className="w-4 shrink-0 text-primary">{previewZoom === z ? '✓' : ''}</span>
                                {Math.round(z * 100)}%
                              </button>
                            ))}
                          </div>
                        )}
                        <button
                          type="button"
                          className={cn(
                            'flex h-8 items-center gap-1 rounded-md px-2.5 text-xs transition-colors',
                            fitMenuOpen
                              ? 'bg-accent text-accent-foreground'
                              : 'bg-muted/60 text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                          )}
                          onClick={() => { setFitMenuOpen((o) => !o); setAspectMenuOpen(false) }}
                          title="Zoom preview"
                        >
                          {fitMenuLabel}
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
                            <polyline points="6 9 12 15 18 9" />
                          </svg>
                        </button>
                        <div className="w-px h-4 bg-border mx-0.5" />
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
                </div>
              </ResizablePanel>

              <ResizableHandle withHandle />

              {/* ── RIGHT: Properties panel — icon rail + content (OpenCut PropertiesPanel) ── */}
              <ResizablePanel id="properties" defaultSize={25} minSize={15} maxSize={45} className="min-w-0 pl-1">
                  <div className="panel bg-background flex h-full overflow-hidden rounded-sm border border-border">

                    {/* Vertical tab rail — luôn hiện đủ tab (Âm thanh + Vùng che chữ) */}
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
                          {selected
                            ? `${PROP_TABS.find((t) => t.key === effectivePropTab)?.label} — Đoạn #${String(selected.index).padStart(2, '0')}`
                            : `${PROP_TABS.find((t) => t.key === effectivePropTab)?.label ?? 'Thuộc tính'} — Tất cả`}
                        </div>

                        {effectivePropTab === 'caption' && selected && (
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
                              onClick={() => void previewTts()}
                            >
                              {ttsBusy ? 'Đang tạo TTS…' : <><IconHeadphones size={13} /> Nghe TTS</>}
                            </button>
                            {ttsError && <p className="text-xs text-destructive">{ttsError}</p>}

                            <div className="border-t border-border pt-3 flex flex-col gap-2">
                              <PropLabel label={`Cỡ chữ (xem trước ~${activeCaptionPx}px)`}>
                                <select
                                  className="w-full rounded-md border border-border bg-input px-2 py-1 text-xs outline-none focus:border-ring"
                                  value={String(fontSizeDraft)}
                                  disabled={busy}
                                  onChange={(e) => applyFontSize('one', Number(e.target.value))}
                                >
                                  <option value="0">
                                    {isOverlaySeg
                                      ? 'Tự động theo khung (đủ đọc)'
                                      : `Tự động (${AUTO_SUBTITLE_FONT}px${settings.subtitleFontSize > 0 ? ` · dự án ${settings.subtitleFontSize}px` : ''})`}
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
                                  onClick={() => applyFontSize('one', 0)}
                                >
                                  Reset đoạn này về tự động
                                </button>
                              )}
                              {isOverlaySeg && (
                                <p className="text-[11px] text-muted-foreground leading-snug">
                                  Đổi cỡ → áp dụng ngay. Khung dọc/mid/nhãn nới theo chữ.
                                </p>
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
                          const idx = selected ? segments.findIndex((s) => s.id === selected.id) : -1
                          const prevEnd = idx > 0 ? segments[idx - 1].end : 0
                          const nextStart = idx >= 0 ? (segments[idx + 1]?.start ?? timelineDuration) : timelineDuration
                          const minDur = 0.15
                          return (
                            <>
                              <PropLabel label={`Tốc độ video: ${speedDraft.toFixed(2)}×`}>
                                <input type="range" min={0.5} max={2} step={0.05}
                                  className="w-full accent-primary"
                                  value={speedDraft} disabled={busy || speedBusy}
                                  onChange={(e) => setSpeedDraft(Number(e.target.value))}
                                />
                              </PropLabel>
                              <div className="flex gap-1">
                                {[0.5, 0.75, 1, 1.5, 2].map((v) => (
                                  <button
                                    key={v}
                                    type="button"
                                    className={cn(
                                      'flex-1 rounded-sm border px-1 py-1 text-[10px] transition-colors',
                                      Math.abs(speedDraft - v) < 0.001
                                        ? 'border-primary text-primary bg-primary/10'
                                        : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
                                    )}
                                    disabled={busy || speedBusy}
                                    onClick={() => setSpeedDraft(v)}
                                  >
                                    {v}×
                                  </button>
                                ))}
                              </div>
                              <button
                                type="button"
                                className="w-full rounded-md border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50"
                                disabled={busy || speedBusy}
                                title="Bake lại toàn bộ preview theo tốc độ đã chọn"
                                onClick={() => void applyVideoSpeed('all')}
                              >
                                {speedBusy ? 'Đang bake preview…' : 'Áp dụng tốc độ cho tất cả'}
                              </button>
                              {speedError && (
                                <p className="text-[10px] text-destructive leading-snug">{speedError}</p>
                              )}
                              <p className="text-[10px] text-muted-foreground leading-snug">
                                Kéo tốc độ rồi nhấn Áp dụng để render lại toàn bộ preview.
                              </p>

                              {selected && (
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
                              )}
                            </>
                          )
                        })()}

                        {effectivePropTab === 'audio' && (
                          <>
                            <div className="space-y-2 pb-2 border-b border-border">
                              <label className="flex items-center justify-between gap-2 text-xs cursor-pointer">
                                <span className="font-medium text-foreground">Lọc âm thanh gốc</span>
                                <input
                                  type="checkbox"
                                  className="accent-primary"
                                  checked={settings.processOriginalAudio}
                                  disabled={busy}
                                  onChange={(e) => {
                                    const on = e.target.checked
                                    onSettings({
                                      ...settings,
                                      processOriginalAudio: on,
                                      originalAudioMode:
                                        on && settings.originalAudioMode === 'original'
                                          ? 'no_vocals'
                                          : settings.originalAudioMode,
                                    })
                                  }}
                                />
                              </label>
                              {settings.processOriginalAudio && (
                                <>
                                  <div className="flex gap-1" role="radiogroup" aria-label="Chế độ lọc âm gốc">
                                    {(
                                      [
                                        ['no_vocals', 'Xóa lời'],
                                        ['vocals', 'Chỉ giữ lời'],
                                      ] as const
                                    ).map(([value, label]) => (
                                      <button
                                        key={value}
                                        type="button"
                                        className={cn(
                                          'flex-1 rounded-sm border px-1 py-1.5 text-[10px] transition-colors',
                                          settings.originalAudioMode === value
                                            ? 'border-primary text-primary bg-primary/10'
                                            : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
                                        )}
                                        disabled={busy}
                                        onClick={() =>
                                          onSettings({ ...settings, originalAudioMode: value })
                                        }
                                      >
                                        {label}
                                      </button>
                                    ))}
                                  </div>
                                  <PropLabel label={`Âm lượng nền: ${Math.max(0, Math.min(100, settings.originalAudioVolume ?? 100))}%`}>
                                    <input
                                      type="range"
                                      min={0}
                                      max={100}
                                      className="w-full accent-primary"
                                      value={Math.max(0, Math.min(100, settings.originalAudioVolume ?? 100))}
                                      disabled={busy || settings.originalAudioMode === 'mute'}
                                      onChange={(e) =>
                                        onSettings({
                                          ...settings,
                                          originalAudioVolume: Math.max(0, Math.min(100, Number(e.target.value) || 0)),
                                        })
                                      }
                                    />
                                  </PropLabel>
                                  {wantNoVocals && stemStatus === 'loading' && (
                                    <p className="text-[10px] text-muted-foreground leading-snug">
                                      Đang tách stem xóa lời {Math.max(1, Math.min(99, stemProgress))}% (Demucs).
                                      Lần đầu có thể cài PyTorch — chờ đến khi cột «Âm gốc» hiện «Xóa lời».
                                    </p>
                                  )}
                                  {wantNoVocals && stemStatus === 'ready' && (
                                    <p className="text-[10px] text-emerald-600 dark:text-emerald-400 leading-snug">
                                      Preview = xuất: video đã mute, phát nền đã xóa lời (+ TTS nếu có).
                                    </p>
                                  )}
                                  {wantNoVocals && stemStatus === 'error' && (
                                    <div className="space-y-1.5">
                                      <p className="text-[10px] text-destructive leading-snug">
                                        Lỗi tách stem: {stemError || 'không rõ'} — tạm mute âm gốc (tránh còn lời).
                                      </p>
                                      <button
                                        type="button"
                                        className="w-full rounded-md border border-border bg-accent hover:bg-muted px-2 py-1.5 text-[11px] transition-colors"
                                        disabled={busy}
                                        onClick={() => setStemRetry((n) => n + 1)}
                                      >
                                        Thử tách lại
                                      </button>
                                    </div>
                                  )}
                                  {settings.originalAudioMode === 'vocals' && (
                                    <p className="text-[10px] text-muted-foreground leading-snug">
                                      «Chỉ giữ lời» áp dụng khi xuất (preview vẫn nghe bản gốc).
                                    </p>
                                  )}
                                </>
                              )}
                              {!settings.processOriginalAudio && (
                                <p className="text-[10px] text-muted-foreground leading-snug">
                                  Chưa bật lọc: bản xuất vẫn trộn âm gốc (nhạc/lời) dưới TTS — xem cột «Âm gốc».
                                </p>
                              )}
                            </div>

                            {selected && (
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
                                    onClick={() => void previewTts()}
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
                          </>
                        )}

                        {effectivePropTab === 'mask' && (
                          <>
                            <p className="text-[11px] text-muted-foreground leading-relaxed">
                              Khung trên preview = vùng che chữ gốc. Xuất video dùng <strong className="text-foreground font-medium">cùng khung + kiểu mặt nạ</strong>.
                              <strong className="text-foreground font-medium"> Làm mờ</strong> = kính mờ CapCut (blur + tint mỏng);
                              <strong className="text-foreground font-medium"> Khối</strong> = phủ màu nền + texture (giống xuất);
                              nếu vẫn lộ chữ cũ, chọn <strong className="text-foreground font-medium">Màu nền</strong> hoặc kéo rộng khung.
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
                                Khối lấy màu nền quanh chữ + texture nhẹ — giống khi xuất; không dùng màu phủ.
                              </p>
                            )}
                            {coverMaskStyle === 'blur' && (
                              <p className="text-[10px] text-muted-foreground leading-snug">
                                Độ đậm = blur + tint mỏng (CapCut). Preview phải thấy kính mờ trên chữ gốc.
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
                            {selected || bboxSeg ? (
                              <>
                            <ul className="text-[10px] text-muted-foreground space-y-1 list-disc pl-4">
                              <li>Kéo <strong>giữa</strong> khung → di chuyển (Alt = tắt snap giữa)</li>
                              <li>Kéo <strong>góc/cạnh</strong> (chấm trắng) → phóng to/thu nhỏ tự do</li>
                              <li>Sau khi thả, khung được <strong>giữ nguyên</strong> — không auto reset</li>
                              <li>Phụ đề dịch fit trong khung đã kéo</li>
                            </ul>
                            <div className="grid grid-cols-2 gap-2">
                              <NumField label="X" value={selectedBox.x} disabled={busy || !selected}
                                onCommit={(v) => commitCoverBox({ x: Math.round(Math.max(0, Math.min(sourceWidth - selectedBox.w, v))) })} />
                              <NumField label="Y" value={selectedBox.y} disabled={busy || !selected}
                                onCommit={(v) => commitCoverBox({ y: Math.round(Math.max(0, Math.min(sourceHeight - selectedBox.h, v))) })} />
                              <NumField label="Rộng" value={selectedBox.w} disabled={busy || !selected}
                                onCommit={(v) => commitCoverBox({ w: Math.round(Math.max(12, Math.min(sourceWidth - selectedBox.x, v))) })} />
                              <NumField label="Cao" value={selectedBox.h} disabled={busy || !selected}
                                onCommit={(v) => commitCoverBox({
                                  h: Math.round(Math.max(12, Math.min(sourceHeight - selectedBox.y, v))),
                                })} />
                            </div>
                            {!selected && (
                              <p className="text-[10px] text-muted-foreground">
                                Đang hiện khung tại playhead — chọn đoạn để kéo/sửa số, hoặc Áp dụng tất cả.
                              </p>
                            )}
                            <p className="text-[10px] text-muted-foreground">
                              Kéo cạnh trên/dưới (hoặc nhập Cao) để chỉnh chiều cao vùng che.
                            </p>
                            <div className="grid grid-cols-2 gap-2">
                              <button
                                type="button"
                                className="rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
                                disabled={busy || !selected || sourceWidth <= 0}
                                title="Giữ Y/Cao, kéo ngang ~96% khung"
                                onClick={stretchCoverFullWidth}
                              >
                                Full ngang
                              </button>
                              <button
                                type="button"
                                className="rounded-md border border-violet-400/60 bg-violet-500/15 hover:bg-violet-500/25 px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
                                disabled={busy || !(selected || bboxSeg) || segments.length === 0}
                                title="Chép khung che hiện tại sang mọi đoạn ngang"
                                onClick={applyCoverMaskToAll}
                              >
                                Áp dụng tất cả
                              </button>
                            </div>
                            <button
                              type="button"
                              className="w-full rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
                              disabled={busy || !selected?.bbox}
                              onClick={() => selected && onChange({ ...selected, bbox: null, captionLayout: null })}
                            >
                              Reset vùng OCR
                            </button>
                              </>
                            ) : (
                              <p className="text-[11px] text-muted-foreground">
                                Chưa có vùng che tại playhead — chọn đoạn caption hoặc tua tới chỗ có chữ.
                              </p>
                            )}
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
              </ResizablePanel>

            </ResizablePanelGroup>
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* ── BOTTOM: Timeline (CapCut — rộng, track cao) ── */}
          <ResizablePanel id="timeline" defaultSize={38} minSize={26} maxSize={58} className="min-h-0 px-2 pb-2 pt-0.5">
            <div className="panel bg-background h-full flex flex-col rounded-sm border border-border overflow-hidden">

              {/* Timeline toolbar — CapCut: edit tools left / zoom right */}
              <div className="flex items-center justify-between h-10 border-b border-border shrink-0 px-2.5">
                <div className="flex items-center gap-0.5">
                  <TlButton title="Hoàn tác (Ctrl+Z)" disabled={!canUndo} onClick={undoEdit}>
                    <TabSvg><path d="M3 7v6h6" /><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6.7 2.9L3 13" /></TabSvg>
                  </TlButton>
                  <TlButton title="Làm lại (Ctrl+Shift+Z)" disabled={!canRedo} onClick={redoEdit}>
                    <TabSvg><path d="M21 7v6h-6" /><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6.7 2.9L21 13" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton
                    title={canSplit ? 'Split tại playhead' : (splitDisabledReason || 'Split')}
                    disabled={!canSplit}
                    onClick={splitAtPlayhead}
                  >
                    <TabSvg>
                      <path d="M8 4v16" /><path d="M16 4v16" />
                      <path d="M4 8h4" /><path d="M4 16h4" />
                      <path d="M16 8h4" /><path d="M16 16h4" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Xóa trái playhead (trim left)" disabled={!canTrimLeft} onClick={trimLeftToPlayhead}>
                    <TabSvg>
                      <path d="M12 4v16" /><path d="M12 8h6" /><path d="M12 16h6" />
                      <path d="M4 6l4 6-4 6" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Xóa phải playhead (trim right)" disabled={!canTrimRight} onClick={trimRightToPlayhead}>
                    <TabSvg>
                      <path d="M12 4v16" /><path d="M6 8h6" /><path d="M6 16h6" />
                      <path d="M20 6l-4 6 4 6" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Xóa clip đã chọn (Del)" disabled={!canDeleteClip} onClick={deleteSelectedClip}>
                    <TabSvg><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6M14 11v6" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton title="Nhân đôi clip" disabled={!canDuplicate} onClick={duplicateClip}>
                    <TabSvg><rect x="8" y="8" width="12" height="12" rx="1" /><path d="M4 16V5a1 1 0 0 1 1-1h11" /></TabSvg>
                  </TlButton>
                  <TlButton title="Tách âm thanh → Xóa lời" disabled={busy} onClick={extractAudioFromVideo}>
                    <TabSvg>
                      <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
                      <path d="M3 3l18 18" />
                    </TabSvg>
                  </TlButton>
                  <TlButton
                    title={bookmarkActive ? 'Xóa bookmark tại playhead' : 'Thêm bookmark'}
                    active={bookmarkActive}
                    onClick={toggleBookmarkAtPlayhead}
                  >
                    <TabSvg><path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton title="Thêm text overlay tại playhead (T)" onClick={() => addTextOverlay()}>
                    <TabSvg><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></TabSvg>
                  </TlButton>
                  <TlButton
                    title={'Phím tắt:\nCtrl+Z — Hoàn tác\nCtrl+Shift+Z / Ctrl+Y — Làm lại\nSpace / K — Phát / dừng\nJ / L — Tua −5s / +5s\n← / → — Lùi / tiến 1 frame (Shift = 1s)\n↑ / ↓ — Đoạn trước / sau\nHome / End — Về đầu / cuối\nT — Thêm text overlay\nS — Split\nB — Bookmark\nF — Toàn màn hình\nDelete — Xóa clip đang chọn\nEsc — Bỏ chọn'}
                  >
                    <TabSvg><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" /></TabSvg>
                  </TlButton>
                </div>

                {/* Zoom controls — mặc định fit full ngang */}
                <div className="flex items-center gap-1">
                  <TlButton title="Vừa khung (fit ngang)" onClick={zoomToFit}>
                    <TabSvg><path d="M8 3H5a2 2 0 0 0-2 2v3" /><path d="M16 3h3a2 2 0 0 1 2 2v3" /><path d="M8 21H5a2 2 0 0 1-2-2v-3" /><path d="M16 21h3a2 2 0 0 0 2-2v-3" /></TabSvg>
                  </TlButton>
                  <TlButton title="Thu nhỏ" onClick={() => setZoomManual((z) => +(z / 1.5).toFixed(3))}>
                    <TabSvg><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="8" y1="11" x2="14" y2="11" /></TabSvg>
                  </TlButton>
                  <input
                    type="range" min={ZOOM_MIN} max={ZOOM_MAX} step={0.01} value={zoom}
                    className="w-28 accent-primary"
                    onChange={(e) => setZoomManual(Number(e.target.value))}
                  />
                  <TlButton title="Phóng to" onClick={() => setZoomManual((z) => +(z * 1.5).toFixed(3))}>
                    <TabSvg><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="11" y1="8" x2="11" y2="14" /><line x1="8" y1="11" x2="14" y2="11" /></TabSvg>
                  </TlButton>
                </div>
              </div>

              {/* Timeline body: labels + tracks */}
              <div className="flex flex-1 min-h-0 overflow-hidden">

                {/* Track labels — spacer + rows; scroll Y khớp tracks */}
                <div className="w-[168px] shrink-0 flex flex-col border-r border-border bg-muted/20 min-h-0">
                  <div className="h-7 shrink-0 border-b border-border bg-background/70" />
                  <div
                    ref={labelsScrollRef}
                    className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                    onScroll={syncLabelsY}
                  >
                  <div className="pb-16">
                  {(
                    [
                      {
                        id: 'video' as const,
                        h: 'h-[72px]',
                        label: 'Video',
                        icon: (
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0" aria-hidden>
                            <polygon points="5 3 19 12 5 21 5 3" />
                          </svg>
                        ),
                        mute: false,
                        hide: true,
                        lock: true,
                        focus: 'video' as const,
                      },
                      ...captionLanes.map((lane) => ({
                        id: 'caption' as const,
                        h: 'h-10',
                        label: lane.label,
                        icon: <span className="text-xs leading-none shrink-0">◈</span>,
                        mute: false,
                        hide: true,
                        lock: true,
                        focus: 'caption' as const,
                        laneKey: lane.key,
                      })),
                      {
                        id: 'dub' as const,
                        h: 'h-10',
                        label: 'Lồng tiếng',
                        icon: <IconHeadphones size={13} className="shrink-0" />,
                        mute: true,
                        hide: true,
                        lock: true,
                        focus: 'dub' as const,
                      },
                      {
                        id: 'bg' as const,
                        h: 'h-10',
                        label: 'Âm gốc',
                        icon: (
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0" aria-hidden>
                            <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
                          </svg>
                        ),
                        mute: true,
                        hide: true,
                        lock: false,
                        focus: 'bg' as const,
                      },
                      {
                        id: 'text' as const,
                        h: 'h-10',
                        label: 'Text',
                        icon: <span className="text-xs font-semibold leading-none shrink-0">T</span>,
                        mute: false,
                        hide: true,
                        lock: true,
                        focus: 'text' as const,
                      },
                    ] as const
                  ).map((row, rowIdx) => {
                    const muted =
                      row.id === 'bg'
                        ? settings.processOriginalAudio && settings.originalAudioMode === 'mute'
                        : trackMute[row.id]
                    return (
                      <div
                        key={`${row.id}-${'laneKey' in row ? row.laneKey : rowIdx}`}
                        className={cn(
                          row.h,
                          'box-border flex items-center gap-1 px-2 border-b border-border/80 shrink-0 cursor-pointer',
                          trackHidden[row.id] && 'opacity-50',
                          trackFocus === row.focus && 'bg-primary/10',
                        )}
                        onClick={() => {
                          if (row.id === 'video') focusVideo()
                          else if (row.id === 'bg') focusBg()
                          else if (row.id === 'caption') {
                            const laneKey = 'laneKey' in row ? row.laneKey : 'horizontal'
                            const under = segments.find(
                              (s) => captionLaneOf(s) === laneKey && time >= s.start && time < s.end,
                            )
                            const hit = under ?? segments.find((s) => captionLaneOf(s) === laneKey)
                            if (hit) focusCaption(hit)
                            else setTrackFocus('caption')
                          }
                          else if (row.id === 'dub' && selected) focusDub(selected)
                          else if (row.id === 'text' && selectedOverlay) focusText(selectedOverlay.id)
                          else setTrackFocus(row.focus)
                        }}
                        onContextMenu={(e) => openCtxMenu({ kind: 'track', track: row.id, x: e.clientX, y: e.clientY }, e)}
                      >
                        <span className="text-muted-foreground shrink-0 w-4 flex justify-center">{row.icon}</span>
                        <span className="text-[11px] text-muted-foreground truncate flex-1 min-w-0">{row.label}</span>
                        {row.mute && (
                          <TrackCtrl
                            title={muted ? 'Bật tiếng' : 'Tắt tiếng'}
                            active={muted}
                            onClick={() => {
                              if (row.id === 'bg') {
                                if (muted) {
                                  onSettings({
                                    ...settings,
                                    processOriginalAudio: false,
                                    originalAudioMode: 'original',
                                  })
                                } else {
                                  onSettings({
                                    ...settings,
                                    processOriginalAudio: true,
                                    originalAudioMode: 'mute',
                                  })
                                }
                              } else {
                                toggleTrackFlag(setTrackMute, row.id)
                              }
                            }}
                          >
                            {muted ? (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><line x1="23" y1="9" x2="17" y2="15" /><line x1="17" y1="9" x2="23" y2="15" /></svg>
                            ) : (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" /><path d="M15.54 8.46a5 5 0 0 1 0 7.07" /></svg>
                            )}
                          </TrackCtrl>
                        )}
                        {row.hide && (
                          <TrackCtrl
                            title={trackHidden[row.id] ? 'Bỏ làm mờ track' : 'Làm mờ track'}
                            active={trackHidden[row.id]}
                            onClick={() => toggleTrackFlag(setTrackHidden, row.id)}
                          >
                            {trackHidden[row.id] ? (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg>
                            ) : (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
                            )}
                          </TrackCtrl>
                        )}
                        {row.lock && (
                          <TrackCtrl
                            title={trackLocked[row.id] ? 'Mở khóa' : 'Khóa'}
                            active={trackLocked[row.id]}
                            onClick={() => toggleTrackFlag(setTrackLocked, row.id)}
                          >
                            {trackLocked[row.id] ? (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
                            ) : (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 9.9-1" /></svg>
                            )}
                          </TrackCtrl>
                        )}
                      </div>
                    )
                  })}
                  </div>
                  </div>
                </div>

                {/* Ruler + tracks */}
                <div className="flex flex-col flex-1 min-w-0 relative overflow-hidden" ref={tracksColRef}>

                  {/* Ruler */}
                  <div className="h-7 overflow-hidden shrink-0 border-b border-border bg-background/70" ref={rulerScrollRef}>
                    <div
                      className="relative h-full cursor-crosshair select-none"
                      style={{ width: trackWidth }}
                      onPointerDown={beginScrub}
                    >
                      {ticks.map((tick) => (
                        <React.Fragment key={tick}>
                          <span
                            className="absolute bottom-0 w-px h-2 bg-border pointer-events-none"
                            style={{ left: tick * pxPerSec }}
                          />
                          <span
                            className="absolute top-1 text-[10px] text-muted-foreground translate-x-[-50%] pointer-events-none whitespace-nowrap tabular-nums"
                            style={{ left: tick * pxPerSec }}
                          >
                            {formatTime(tick)}
                          </span>
                        </React.Fragment>
                      ))}
                      {bookmarks.map((mark) => (
                        <button
                          key={mark}
                          type="button"
                          title={`Bookmark ${formatTime(mark)}`}
                          className="absolute top-0 z-[2] w-0 h-0 border-l-[5px] border-r-[5px] border-t-[8px] border-l-transparent border-r-transparent border-t-sky-400 -translate-x-1/2 hover:border-t-sky-300"
                          style={{ left: mark * pxPerSec }}
                          onPointerDown={(e) => {
                            e.stopPropagation()
                            e.preventDefault()
                            seekPlayhead(mark)
                          }}
                        />
                      ))}
                    </div>
                  </div>

                  {/* Master scroll area — nền tối + khoảng trống dưới track */}
                  <div
                    className="flex-1 overflow-x-auto overflow-y-auto scrollbar-thin bg-black/25"
                    ref={tracksScrollRef}
                    onScroll={syncFollowers}
                  >
                    <div className="flex flex-col min-h-full pb-16" style={{ width: trackWidth }}>

                      {/* Video track — clip riêng (split độc lập) */}
                      <div
                        ref={trackRef}
                        className={cn(
                          'relative h-[72px] box-border border-b border-border/80 bg-black/50',
                          trackHidden.video && 'opacity-30',
                        )}
                        onPointerDown={(e) => {
                          if ((e.target as HTMLElement).closest('[data-media-clip]')) return
                          focusVideo()
                          beginScrub(e)
                        }}
                        onContextMenu={(e) => openCtxMenu({ kind: 'track', track: 'video', x: e.clientX, y: e.clientY }, e)}
                      >
                        {videoUrl && videoSpan > 0 && (
                          <div
                            className="absolute top-2 left-0 h-[calc(100%-16px)] rounded-md overflow-hidden pointer-events-none ring-1 ring-white/15"
                            style={{ width: Math.max(2, videoSpan * pxPerSec) }}
                          >
                            <TimelineFilmstrip
                              videoUrl={videoUrl}
                              duration={videoSpan}
                              widthPx={videoSpan * pxPerSec}
                              heightPx={56}
                              className="absolute inset-0"
                            />
                          </div>
                        )}
                        {videoClips.map((clip) => {
                          const isSelected = trackFocus === 'video' && selectedMediaId === clip.id
                          return (
                            <button
                              key={clip.id}
                              type="button"
                              data-media-clip="video"
                              title={`Video ${formatTime(clip.start)}–${formatTime(clip.end)}`}
                              className={cn(
                                'absolute top-2 h-[calc(100%-16px)] rounded-md border-0 cursor-pointer z-[1] bg-transparent',
                                isSelected ? 'ring-[1.5px] ring-primary shadow-sm bg-primary/15' : 'ring-1 ring-white/30 hover:bg-white/10',
                              )}
                              style={{
                                left: clip.start * pxPerSec,
                                width: Math.max(2, (clip.end - clip.start) * pxPerSec),
                              }}
                              onPointerDown={(e) => e.stopPropagation()}
                              onClick={(e) => {
                                e.stopPropagation()
                                focusVideo(clip.id)
                                selectClipKeepPlayhead(clip.start, clip.end)
                              }}
                            />
                          )
                        })}
                      </div>

                      {/* Caption lanes — đáy / CAP-MID / Dọc / Nhãn tách track khi chồng thời gian */}
                      {captionLanes.map((lane) => (
                      <div
                        key={lane.key}
                        className={cn('relative h-10 box-border border-b border-border/80', trackHidden.caption && 'opacity-30')}
                        style={{ backgroundColor: 'var(--background)' }}
                        onPointerDown={(e) => {
                          if ((e.target as HTMLElement).closest('[data-caption-clip]')) return
                          setTrackFocus('caption')
                          beginScrub(e)
                        }}
                      >
                        {layoutSegs.filter((seg) => captionLaneOf(seg, sourceHeight || 1920) === lane.key).map((seg) => {
                          const display = draft?.id === seg.id ? { ...seg, ...draft } : seg
                          const isSelected = trackFocus === 'caption' && seg.id === selected?.id
                          // Một thanh kéo [start,end) — không ghost cover 2 màu; sai thì user kéo đầu/đuôi
                          return (
                            <button
                              key={seg.id}
                              type="button"
                              data-caption-clip=""
                              title={`${formatTime(display.start)}–${formatTime(display.end)}`}
                              className={cn(
                                'absolute top-1.5 h-[calc(100%-12px)] rounded-md text-[11px] text-white whitespace-nowrap overflow-hidden px-2 flex items-center justify-center text-center cursor-pointer border-0 transition-opacity hover:opacity-90',
                                isSelected && 'ring-[1.5px] ring-primary',
                                trackLocked.caption && 'cursor-not-allowed',
                              )}
                              style={{
                                left: display.start * pxPerSec,
                                width: Math.max(2, (display.end - display.start) * pxPerSec),
                                boxSizing: 'border-box',
                                background: isSelected ? lane.selected : lane.color,
                              }}
                              onClick={() => {
                                focusCaption(seg)
                                selectClipKeepPlayhead(display.start, display.end)
                              }}
                              onContextMenu={(e) => {
                                focusCaption(seg)
                                openCtxMenu({ kind: 'segment', segId: seg.id, x: e.clientX, y: e.clientY }, e)
                              }}
                              onPointerDown={(e) => beginDrag(e, seg, 'move')}
                            >
                              <span
                                className="absolute inset-y-0 left-0 w-2.5 cursor-ew-resize rounded-l-md hover:bg-white/25 transition-colors z-10"
                                onPointerDown={(e) => beginDrag(e, seg, 'start')}
                              />
                              <span className="truncate relative z-[1] pointer-events-none">{seg.translation || lane.label}</span>
                              <span
                                className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize rounded-r-md hover:bg-white/25 transition-colors z-10"
                                onPointerDown={(e) => beginDrag(e, seg, 'end')}
                              />
                            </button>
                          )
                        })}
                      </div>
                      ))}

                      {/* Dub / TTS track */}
                      <div className={cn('relative h-10 box-border border-b border-border/80', trackHidden.dub && 'opacity-30')} style={{ backgroundColor: 'var(--background)' }}>
                        {(() => {
                          const dubs = segments.filter((seg) => segmentHasDub(seg) && seg.audioUrl)
                          if (!dubs.length) {
                            return (
                              <button
                                type="button"
                                title={busy ? 'Đang xử lý…' : 'Bấm để lồng tiếng'}
                                disabled={busy || !onDub}
                                className={cn(
                                  'absolute top-1.5 h-[calc(100%-12px)] rounded-md text-[11px] text-white whitespace-nowrap overflow-hidden px-2 flex items-center cursor-pointer border-0 hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed',
                                  trackFocus === 'dub' && 'ring-[1.5px] ring-amber-200',
                                )}
                                style={{
                                  left: 0,
                                  width: 108,
                                  boxSizing: 'border-box',
                                  background: '#E8A045',
                                }}
                                onClick={() => {
                                  setTrackFocus('dub')
                                  setPropTab('audio')
                                  onDub?.()
                                }}
                              >
                                <IconHeadphones size={11} className="shrink-0 mr-1 opacity-90" />
                                Lồng tiếng
                              </button>
                            )
                          }
                          return dubs.map((seg) => {
                            const clipSec = dubClipSeconds(
                              seg,
                              segments,
                              previewVideoRate(settings.matchDuration, bakedPreferVideo),
                            )
                            const isSelected = trackFocus === 'dub' && seg.id === selected?.id
                            return (
                              <button
                                key={seg.id}
                                type="button"
                                title={`TTS ${(seg.audioDuration ?? 0).toFixed(2)}s`}
                                className={cn(
                                  'absolute top-1.5 h-[calc(100%-12px)] rounded-md text-[11px] text-white whitespace-nowrap overflow-hidden px-2 flex items-center cursor-pointer border-0 transition-opacity hover:opacity-90',
                                  isSelected && 'ring-[1.5px] ring-amber-200',
                                )}
                                style={{
                                  left: seg.start * pxPerSec,
                                  width: Math.max(2, clipSec * pxPerSec),
                                  boxSizing: 'border-box',
                                  background: isSelected ? '#c2780a' : '#E8A045',
                                }}
                                onClick={() => {
                                  focusDub(seg)
                                  selectClipKeepPlayhead(seg.start, seg.end)
                                }}
                                onContextMenu={(e) => {
                                  focusDub(seg)
                                  openCtxMenu({ kind: 'dub', segId: seg.id, x: e.clientX, y: e.clientY }, e)
                                }}
                              >
                                <IconHeadphones size={11} className="shrink-0 mr-1 opacity-90" />
                                {(seg.ttsSpeed ?? 1) !== 1 ? `${seg.ttsSpeed}×` : 'TTS'}
                              </button>
                            )
                          })
                        })()}
                      </div>

                      {/* Âm gốc / nền — clip riêng (split độc lập) */}
                      <div className={cn('relative h-10 box-border border-b border-border/80', trackHidden.bg && 'opacity-30')} style={{ backgroundColor: 'var(--background)' }}>
                        {(() => {
                          const on = settings.processOriginalAudio
                          const mode = settings.originalAudioMode
                          let baseLabel = workClipSec > 0 ? `Âm gốc (${workClipSec}s)` : 'Âm gốc'
                          let bg = '#5B8DEF'
                          if (on && mode === 'no_vocals') {
                            if (stemStatus === 'loading') {
                              baseLabel = `Xóa lời… đang tách ${Math.max(1, Math.min(99, stemProgress))}%`
                              bg = '#7a8eb0'
                            } else if (stemStatus === 'error') {
                              baseLabel = 'Xóa lời — lỗi tách (bấm Âm thanh → Thử lại)'
                              bg = '#c44'
                            } else if (stemStatus === 'ready') {
                              baseLabel = `Xóa lời · nền ${Math.max(0, Math.min(100, settings.originalAudioVolume ?? 100))}%`
                              bg = '#3D7AE5'
                            } else {
                              baseLabel = 'Xóa lời'
                              bg = '#5B8DEF'
                            }
                          } else if (on && mode === 'vocals') {
                            baseLabel = 'Chỉ giữ lời (khi xuất)'
                            bg = '#6B5B95'
                          } else if (on && mode === 'mute') {
                            baseLabel = 'Tắt âm gốc'
                            bg = '#666'
                          }
                          return bgClips.map((clip) => {
                            const isSelected = trackFocus === 'bg' && selectedMediaId === clip.id
                            return (
                              <button
                                key={clip.id}
                                type="button"
                                data-media-clip="bg"
                                title={`${baseLabel} · ${formatTime(clip.start)}–${formatTime(clip.end)}`}
                                className={cn(
                                  'absolute top-1.5 h-[calc(100%-12px)] rounded-md text-[11px] text-white whitespace-nowrap overflow-hidden px-2 flex items-center cursor-pointer border-0 hover:opacity-90',
                                  isSelected && 'ring-[1.5px] ring-sky-300',
                                )}
                                style={{
                                  left: clip.start * pxPerSec,
                                  width: Math.max(2, (clip.end - clip.start) * pxPerSec),
                                  boxSizing: 'border-box',
                                  background: bg,
                                  opacity: on && mode === 'mute' ? 0.45 : 0.92,
                                }}
                                onClick={() => {
                                  focusBg(clip.id)
                                  selectClipKeepPlayhead(clip.start, clip.end)
                                }}
                                onContextMenu={(e) => {
                                  focusBg(clip.id)
                                  openCtxMenu({ kind: 'bg', x: e.clientX, y: e.clientY }, e)
                                }}
                              >
                                {baseLabel}
                              </button>
                            )
                          })
                        })()}
                      </div>

                      {/* Text overlay track */}
                      <div className={cn('relative h-10 box-border border-b border-border/80', trackHidden.text && 'opacity-30')} style={{ backgroundColor: 'var(--background)' }}>
                        {overlays.map((overlay) => (
                          <button
                            key={overlay.id}
                            type="button"
                            className={cn(
                              'absolute top-1.5 h-[calc(100%-12px)] rounded-md border-0 text-[11px] text-white whitespace-nowrap overflow-hidden px-2 cursor-pointer flex items-center transition-opacity hover:opacity-90',
                              trackFocus === 'text' && overlay.id === selectedOverlayId && 'ring-[1.5px] ring-yellow-300',
                              trackLocked.text && 'cursor-not-allowed',
                            )}
                            style={{
                              left: overlay.start * pxPerSec,
                              width: Math.max(2, (overlay.end - overlay.start) * pxPerSec),
                              boxSizing: 'border-box',
                              background: trackFocus === 'text' && overlay.id === selectedOverlayId ? '#d97706' : '#E8913A',
                            }}
                            onClick={() => focusText(overlay.id)}
                            onContextMenu={(e) => {
                              focusText(overlay.id)
                              openCtxMenu({ kind: 'overlay', overlayId: overlay.id, x: e.clientX, y: e.clientY }, e)
                            }}
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

      {ctxMenu && (
        <div
          className="fixed z-[100] min-w-[200px] rounded-md border border-border bg-background text-foreground shadow-lg py-1 text-xs"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {ctxMenu.kind === 'segment' && (() => {
            const seg = segments.find((s) => s.id === ctxMenu.segId)
            if (!seg) return null
            const dub = segmentHasDub(seg)
            return (
              <>
                <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('caption'); setCtxMenu(null) }}>Mở Phụ đề</CtxItem>
                <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('video'); setCtxMenu(null) }}>Mở Video</CtxItem>
                <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                <CtxSep />
                <CtxItem
                  disabled={busy}
                  onClick={() => {
                    onChange({
                      ...seg,
                      dub: !dub,
                      ...(dub ? { audioUrl: undefined, audioFile: undefined, audioDuration: undefined } : {}),
                    })
                    setCtxMenu(null)
                  }}
                >
                  {dub ? 'Tắt lồng tiếng' : 'Bật lồng tiếng'}
                </CtxItem>
                <CtxItem
                  disabled={busy || ttsBusy || !seg.translation.trim()}
                  onClick={() => { setCtxMenu(null); void previewTts(seg) }}
                >
                  Tạo lại TTS
                </CtxItem>
                <CtxItem
                  disabled={!seg.audioUrl}
                  onClick={() => { playSegmentDub(seg); setCtxMenu(null) }}
                >
                  Phát với timeline
                </CtxItem>
                <CtxSep />
                {[1, 1.25, 1.5].map((v) => (
                  <CtxItem
                    key={v}
                    onClick={() => { onChange({ ...seg, videoSpeed: v }); setCtxMenu(null) }}
                  >
                    Tốc độ video {v}×{(seg.videoSpeed ?? 1) === v ? ' ✓' : ''}
                  </CtxItem>
                ))}
                {(seg.bbox || seg.captionLayout) && (
                  <>
                    <CtxSep />
                    <CtxItem
                      onClick={() => {
                        onChange({ ...seg, bbox: null, captionLayout: null })
                        setCtxMenu(null)
                      }}
                    >
                      Reset layout caption
                    </CtxItem>
                  </>
                )}
              </>
            )
          })()}

          {ctxMenu.kind === 'dub' && (() => {
            const seg = segments.find((s) => s.id === ctxMenu.segId)
            if (!seg) return null
            return (
              <>
                <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                <CtxSep />
                {[0, 50, 100, 150].map((v) => (
                  <CtxItem key={v} onClick={() => { onChange({ ...seg, ttsVolume: v }); setCtxMenu(null) }}>
                    Âm lượng TTS {v === 0 ? 'Tắt' : `${v}%`}{(seg.ttsVolume ?? 100) === v ? ' ✓' : ''}
                  </CtxItem>
                ))}
                <CtxSep />
                {[0.9, 1, 1.15].map((v) => (
                  <CtxItem key={v} onClick={() => { onChange({ ...seg, ttsSpeed: v }); setCtxMenu(null) }}>
                    Tốc độ TTS {v}×{(seg.ttsSpeed ?? 1) === v ? ' ✓' : ''}
                  </CtxItem>
                ))}
                <CtxSep />
                <CtxItem
                  disabled={busy || ttsBusy || !seg.translation.trim()}
                  onClick={() => { setCtxMenu(null); void previewTts(seg) }}
                >
                  Tạo lại TTS
                </CtxItem>
                <CtxItem
                  onClick={() => {
                    onChange({
                      ...seg,
                      dub: false,
                      audioUrl: undefined,
                      audioFile: undefined,
                      audioDuration: undefined,
                    })
                    setCtxMenu(null)
                  }}
                >
                  Tắt lồng tiếng đoạn này
                </CtxItem>
              </>
            )
          })()}

          {ctxMenu.kind === 'bg' && (
            <>
              <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
              <CtxSep />
              <CtxItem
                onClick={() => {
                  onSettings({ ...settings, processOriginalAudio: false, originalAudioMode: 'original' })
                  setCtxMenu(null)
                }}
              >
                Tắt lọc âm gốc{!settings.processOriginalAudio ? ' ✓' : ''}
              </CtxItem>
              <CtxItem
                onClick={() => {
                  onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
                  setCtxMenu(null)
                }}
              >
                Xóa lời{settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals' ? ' ✓' : ''}
              </CtxItem>
              <CtxItem
                onClick={() => {
                  onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'vocals' })
                  setCtxMenu(null)
                }}
              >
                Chỉ giữ lời{settings.processOriginalAudio && settings.originalAudioMode === 'vocals' ? ' ✓' : ''}
              </CtxItem>
              <CtxSep />
              {[0, 50, 100].map((v) => (
                <CtxItem
                  key={v}
                  onClick={() => {
                    onSettings({ ...settings, originalAudioVolume: v })
                    setCtxMenu(null)
                  }}
                >
                  Âm lượng nền {v}%{(settings.originalAudioVolume ?? 100) === v ? ' ✓' : ''}
                </CtxItem>
              ))}
              {stemStatus === 'error' && (
                <>
                  <CtxSep />
                  <CtxItem onClick={() => { setStemRetry((n) => n + 1); setCtxMenu(null) }}>Thử tách lại</CtxItem>
                </>
              )}
            </>
          )}

          {ctxMenu.kind === 'track' && (() => {
            const tid = ctxMenu.track
            const muted =
              tid === 'bg'
                ? settings.processOriginalAudio && settings.originalAudioMode === 'mute'
                : trackMute[tid]
            return (
              <>
                {tid === 'video' && (
                  <>
                    <CtxItem onClick={() => { setPropTab('video'); setCtxMenu(null) }}>Mở Video</CtxItem>
                    <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                    <CtxSep />
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
                        setPropTab('audio')
                        setCtxMenu(null)
                      }}
                    >
                      Tách âm thanh → Xóa lời
                      {settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals' ? ' ✓' : ''}
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'vocals' })
                        setPropTab('audio')
                        setCtxMenu(null)
                      }}
                    >
                      Tách âm thanh → Chỉ giữ lời
                      {settings.processOriginalAudio && settings.originalAudioMode === 'vocals' ? ' ✓' : ''}
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: false, originalAudioMode: 'original' })
                        setCtxMenu(null)
                      }}
                    >
                      Tắt tách âm{!settings.processOriginalAudio ? ' ✓' : ''}
                    </CtxItem>
                    {stemStatus === 'error' && (
                      <CtxItem onClick={() => { setStemRetry((n) => n + 1); setCtxMenu(null) }}>Thử tách lại</CtxItem>
                    )}
                    <CtxSep />
                  </>
                )}
                {tid === 'bg' && (
                  <>
                    <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
                        setCtxMenu(null)
                      }}
                    >
                      Tách âm thanh → Xóa lời
                      {settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals' ? ' ✓' : ''}
                    </CtxItem>
                    <CtxSep />
                  </>
                )}
                {tid === 'caption' && (
                  <CtxItem onClick={() => { setPropTab('caption'); setCtxMenu(null) }}>Mở Phụ đề</CtxItem>
                )}
                {tid === 'dub' && (
                  <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                )}
                {tid === 'text' && (
                  <CtxItem onClick={() => { setPropTab('overlay'); setCtxMenu(null) }}>Mở Text</CtxItem>
                )}
                {(tid === 'caption' || tid === 'dub' || tid === 'text') && <CtxSep />}
                {(tid === 'dub' || tid === 'bg') && (
                  <CtxItem
                    onClick={() => {
                      if (tid === 'bg') {
                        if (muted) {
                          onSettings({ ...settings, processOriginalAudio: false, originalAudioMode: 'original' })
                        } else {
                          onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'mute' })
                        }
                      } else {
                        toggleTrackFlag(setTrackMute, tid)
                      }
                      setCtxMenu(null)
                    }}
                  >
                    {muted ? 'Bật tiếng' : 'Tắt tiếng'}
                  </CtxItem>
                )}
                <CtxItem
                  onClick={() => {
                    toggleTrackFlag(setTrackHidden, tid)
                    setCtxMenu(null)
                  }}
                >
                  {trackHidden[tid] ? 'Bỏ làm mờ track' : 'Làm mờ track'}
                </CtxItem>
                {tid !== 'bg' && (
                  <CtxItem
                    onClick={() => {
                      toggleTrackFlag(setTrackLocked, tid)
                      setCtxMenu(null)
                    }}
                  >
                    {trackLocked[tid] ? 'Mở khóa' : 'Khóa track'}
                  </CtxItem>
                )}
              </>
            )
          })()}

          {ctxMenu.kind === 'overlay' && (() => {
            const ov = overlays.find((o) => o.id === ctxMenu.overlayId)
            if (!ov) return null
            return (
              <>
                <CtxItem onClick={() => { setSelectedOverlayId(ov.id); setPropTab('overlay'); setCtxMenu(null) }}>
                  Mở Text
                </CtxItem>
                <CtxSep />
                <CtxItem
                  onClick={() => {
                    onOverlayDelete(ov.id)
                    if (selectedOverlayId === ov.id) setSelectedOverlayId(null)
                    setCtxMenu(null)
                  }}
                >
                  Xóa overlay
                </CtxItem>
              </>
            )
          })()}
        </div>
      )}
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

function TrackCtrl({
  title,
  active,
  onClick,
  children,
}: {
  title: string
  active?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      className={cn(
        'w-[18px] h-[18px] rounded-sm flex items-center justify-center shrink-0 transition-colors',
        active
          ? 'text-primary bg-primary/15'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent',
      )}
      onClick={(e) => { e.stopPropagation(); onClick() }}
    >
      {children}
    </button>
  )
}

function CtxItem({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      className="w-full text-left px-3 py-1.5 hover:bg-accent disabled:opacity-40 disabled:pointer-events-none"
      onClick={onClick}
    >
      {children}
    </button>
  )
}

function CtxSep() {
  return <div className="my-1 border-t border-border" role="separator" />
}

function TlButton({ title, onClick, disabled, active, children }: {
  title: string
  onClick?: () => void
  disabled?: boolean
  active?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'w-7 h-7 rounded-sm flex items-center justify-center transition-colors disabled:opacity-40 disabled:pointer-events-none',
        active
          ? 'text-primary bg-primary/15 hover:bg-primary/20'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent',
      )}
    >
      {children}
    </button>
  )
}
