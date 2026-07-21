import type { Segment } from '@/features/project/project.types'
import { resolveCoverWindow } from '@/features/editor/coverTiming'
import { expandSegmentsForPlayback } from './playbackExpand'

export function segmentAt(segments: Segment[], time: number) {
  return segments.find((s) => time >= s.start && time < s.end) ?? null
}

/**
 * Cửa sổ COVER khớp export (burn.py): hardsub hay lộ trước/sau ASR.
 * Caption/TTS vẫn dùng [start,end) chặt; chỉ mask che chữ dùng cửa sổ này.
 * peers: kẹp ngang/mid/label không đè clip kế.
 * Ưu tiên coverStart/coverEnd đã lưu từ OCR.
 */
export function coverWindow(seg: Segment, peers?: Segment[]): { start: number; end: number } {
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
export function clampOverlayPad(
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
export function segmentAtCover(segments: Segment[], time: number): Segment | null {
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

export function segmentHasDub(seg: Segment | undefined): boolean {
  if (!seg) return false
  const isOverlay = seg.layout === 'vertical' || seg.layout === 'label'
  return isOverlay ? seg.dub === true : seg.dub !== false
}

export function isOcrOverlayLayout(layout: Segment['layout']): layout is 'vertical' | 'label' | 'mid' {
  return layout === 'vertical' || layout === 'label' || layout === 'mid'
}

/** Caption ngang nhưng bbox OCR nằm giữa khung → xử lý như mid (không ép đáy). */
export function effectiveOverlayLayout(
  seg: Segment,
  frameH: number,
): 'vertical' | 'label' | 'mid' | null {
  // mid/dọc/nhãn đã tag — luôn mid path (keo tay cung khong roi caption day)
  if (isOcrOverlayLayout(seg.layout)) return seg.layout
  // Caption day keo tay: dung horizontal
  if (seg.bboxInherited === false) return null
  const b = seg.bbox
  if (!b || frameH <= 0) return null
  const cy = b.y + b.h / 2
  // horizontal + bbox giua khung → xu ly nhu mid
  if (cy > frameH * 0.18 && cy < frameH * 0.78) return 'mid'
  return null
}

export type CaptionLaneKey = 'horizontal' | 'mid' | 'vertical' | 'label'

export const CAPTION_LANE_DEFS: {
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
export function captionLaneOf(seg: Segment, frameH = 1920): CaptionLaneKey {
  const lay = seg.layout
  if (lay === 'mid' || lay === 'vertical' || lay === 'label') return lay
  if (seg.bboxInherited === false) return 'horizontal'
  const b = seg.bbox
  if (b && frameH > 0) {
    const cy = b.y + b.h / 2
    // khớp locate._layout_from_cy (0.18–0.78)
    if (cy > frameH * 0.18 && cy < frameH * 0.78) return 'mid'
  }
  return 'horizontal'
}

/** Chuẩn hoá layout theo bbox OCR — ghi đè horizontal sai khi chữ ở giữa khung. */
export function withInferredLayout(seg: Segment, frameH: number): Segment {
  if (seg.bboxInherited && seg.bbox && seg.bbox.w >= Math.max(1, seg.bbox.h) * 8) {
    return { ...seg, layout: 'horizontal' }
  }
  if (seg.layout === 'vertical' || seg.layout === 'label') return seg
  if (seg.bboxInherited === false) {
    return seg.layout ? seg : { ...seg, layout: 'horizontal' }
  }
  const lane = captionLaneOf({ ...seg, layout: undefined }, frameH)
  if (lane === 'mid') return seg.layout === 'mid' ? seg : { ...seg, layout: 'mid' }
  if (seg.layout === 'horizontal' || seg.layout === 'mid') return seg
  return { ...seg, layout: 'horizontal' }
}

/** Overlay Mid/Nhãn/Dọc đang dưới gạch theo [start,end) = đúng thanh timeline solid. */
export function solidOverlaysAt(segments: Segment[], time: number): Segment[] {
  return segments.filter(
    (s) => isOcrOverlayLayout(s.layout) && time >= s.start && time < s.end,
  )
}

export function solidMidAt(segments: Segment[], time: number, preferId?: string | null): Segment | null {
  const mids = solidOverlaysAt(segments, time).filter((s) => captionLaneOf(s) === 'mid')
  if (!mids.length) return null
  if (preferId) {
    const sel = mids.find((s) => s.id === preferId)
    if (sel) return sel
  }
  return mids.reduce((a, b) => (Math.abs(time - a.start) <= Math.abs(time - b.start) ? a : b))
}

/** OCR overlay dưới playhead — ưu tiên selected, rồi mid → label → vertical (cùng kiểu kéo bbox). */
export function solidOcrAt(segments: Segment[], time: number, preferId?: string | null): Segment | null {
  const hits = solidOverlaysAt(segments, time)
  if (!hits.length) return null
  if (preferId) {
    const sel = hits.find((s) => s.id === preferId)
    if (sel) return sel
  }
  for (const lane of ['mid', 'label', 'vertical'] as const) {
    const list = hits.filter((s) => captionLaneOf(s) === lane)
    if (!list.length) continue
    return list.reduce((a, b) =>
      (Math.abs(time - a.start) <= Math.abs(time - b.start) ? a : b))
  }
  return hits[0]
}

/** Mọi segment đang cháy tại t (có thể chồng mid+dọc). */
export function segmentsAt(segments: Segment[], time: number): Segment[] {
  return segments.filter((s) => time >= s.start && time < s.end)
}

export function pickTimelineSeg(segments: Segment[], time: number, selectedId: string | null): Segment | null {
  // selectedId có thể là compound shell (không có trong list đã bung) — bỏ prefer
  const prefer =
    selectedId && segments.some((s) => s.id === selectedId) ? selectedId : null
  // Quy tắc: thanh vàng Mid solid dưới gạch → chọn đúng Mid đó (không stick vertical/cover pad cũ)
  const mid = solidMidAt(segments, time, prefer)
  if (mid) return mid
  const labels = solidOverlaysAt(segments, time).filter((s) => captionLaneOf(s) === 'label')
  if (labels.length) {
    const sel = prefer ? labels.find((s) => s.id === prefer) : null
    return sel ?? labels[0]
  }
  const hits = segmentsAt(segments, time)
  if (!hits.length) return segmentAtCover(segments, time)
  if (prefer) {
    const sel = hits.find((s) => s.id === prefer)
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

/** Self-check: Alt+G compound — preview bung children, chữ giữ timing tuyệt đối. */
export function __checkCompoundExpandCaptions(): void {
  const shell = {
    id: 'cmp1',
    index: 0,
    start: 10,
    end: 20,
    source: '',
    translation: '',
    voice: '',
    isCompound: true,
    compoundChildren: [
      {
        id: 'c1',
        index: 0,
        start: 0,
        end: 2,
        source: 'a',
        translation: 'Một',
        voice: '',
        layout: 'horizontal' as const,
      },
      {
        id: 'c2',
        index: 1,
        start: 3,
        end: 5,
        source: 'b',
        translation: 'Hai',
        voice: '',
        layout: 'mid' as const,
      },
    ],
  } as Segment
  const exp = expandSegmentsForPlayback([shell])
  if (exp.length !== 2) throw new Error(`expected 2 children, got ${exp.length}`)
  if (exp[0].start !== 10 || exp[0].end !== 12 || exp[0].translation !== 'Một') {
    throw new Error(`child0 bad ${JSON.stringify(exp[0])}`)
  }
  if (exp[1].start !== 13 || exp[1].end !== 15 || exp[1].translation !== 'Hai') {
    throw new Error(`child1 bad ${JSON.stringify(exp[1])}`)
  }
  const at = pickTimelineSeg(exp, 13.5, 'cmp1')
  if (at?.id !== 'c2') throw new Error(`prefer shell id must not hide mid child, got ${at?.id}`)
}

