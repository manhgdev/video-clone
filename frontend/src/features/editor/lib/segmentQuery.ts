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
      }
      // Không xử lý nhánh "đã chồng nhau": hai dòng clamp cuối (s ≤ seg.start,
      // e ≥ seg.end) luôn phủ lại kết quả — cửa sổ tối thiểu là [start,end).
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
  // Hot path (pointermove khi scrub): coverWindow là O(n) nên vòng lặp thô
  // thành O(n²) mỗi frame. Cache theo tham chiếu mảng — segments là immutable
  // (mọi sửa tạo mảng mới) nên cache tự vô hiệu khi timeline đổi.
  const windows = coverWindowsOf(segments)
  let best: Segment | null = null
  for (let i = 0; i < segments.length; i++) {
    const w = windows[i]
    if (time >= w.start && time < w.end) {
      const s = segments[i]
      if (!best || s.start > best.start) best = s
    }
  }
  // Lấp khe < 0.45s giữa 2 câu hardsub (cùng logic xuất)
  if (!best) {
    const order = windows.map((_, i) => i).sort((a, b) => segments[a].start - segments[b].start)
    for (let k = 0; k < order.length - 1; k++) {
      const a = segments[order[k]]
      const b = segments[order[k + 1]]
      if ((a.layout || 'horizontal') !== 'horizontal') continue
      if ((b.layout || 'horizontal') !== 'horizontal') continue
      const wa = windows[order[k]]
      const wb = windows[order[k + 1]]
      const gap = wb.start - wa.end
      if (gap > 0 && gap < 0.45 && time >= wa.end && time < wb.start) {
        return time < (wa.end + wb.start) / 2 ? a : b
      }
    }
  }
  return best
}

let _coverWindowCache: { key: Segment[]; value: { start: number; end: number }[] } | null = null

/** coverWindow cho cả list, cache theo tham chiếu mảng (tránh O(n²) mỗi frame). */
export function coverWindowsOf(segments: Segment[]): { start: number; end: number }[] {
  if (_coverWindowCache && _coverWindowCache.key === segments) return _coverWindowCache.value
  const value = segments.map((s) => coverWindow(s, segments))
  _coverWindowCache = { key: segments, value }
  return value
}

export function segmentHasDub(seg: Segment | undefined): boolean {
  if (!seg) return false
  const isOverlay = seg.layout === 'vertical' || seg.layout === 'label'
  return isOverlay ? seg.dub === true : seg.dub !== false
}

export function isOcrOverlayLayout(layout: Segment['layout']): layout is 'vertical' | 'label' | 'mid' {
  return layout === 'vertical' || layout === 'label' || layout === 'mid'
}

/** Must match backend overlay_cover.mid_bottom_cutoff(). */
export function captionMidBottomCutoff(frameW: number, frameH: number): number {
  if (frameW <= 0 || frameH <= 0) return 0.78
  const aspect = frameW / frameH
  const cutoff = 0.75 - 0.03 * Math.log2(aspect) / Math.log2(16 / 9)
  return Math.max(0.70, Math.min(0.80, cutoff))
}

/**
 * Engine xếp chữ cho overlay. `horizontal` vẫn thuộc lane Caption, nhưng dùng
 * chung engine bbox của mid; khác nhau chỉ ở tag/tọa độ fallback.
 */
export function effectiveOverlayLayout(
  seg: Segment,
  frameH: number,
  frameW = 1080,
): 'vertical' | 'label' | 'mid' | null {
  // mid/dọc/nhãn đã tag — luôn overlay path.
  if (isOcrOverlayLayout(seg.layout)) return seg.layout
  if (seg.layout === 'horizontal') return 'mid'
  const b = seg.bbox
  if (!b || frameH <= 0) return null
  const cy = b.y + b.h / 2
  if (cy > frameH * 0.18 && cy < frameH * captionMidBottomCutoff(frameW, frameH)) return 'mid'
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
export function captionLaneOf(seg: Segment, frameH = 1920, frameW = 1080): CaptionLaneKey {
  const lay = seg.layout
  if (lay === 'mid' || lay === 'vertical' || lay === 'label') return lay
  const b = seg.bbox
  if (b && frameH > 0) {
    const cy = b.y + b.h / 2
    if (cy > frameH * 0.18 && cy < frameH * captionMidBottomCutoff(frameW, frameH)) return 'mid'
  }
  return 'horizontal'
}

/** Chuẩn hoá layout theo bbox OCR — ghi đè horizontal sai khi chữ ở giữa khung. */
export function withInferredLayout(seg: Segment, frameH: number, frameW = 1080): Segment {
  if (seg.layout === 'vertical' || seg.layout === 'label') return seg
  const lane = captionLaneOf({ ...seg, layout: undefined }, frameH, frameW)
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

/**
 * Segment điều khiển playbackRate. Phải khớp backend _retime_spans():
 * chỉ [start,end), không cover padding và không phụ thuộc clip đang chọn.
 */
export function speedSegmentAt(segments: Segment[], time: number): Segment | null {
  const hits = segmentsAt(segments, time)
  if (!hits.length) return null
  const lane = (s: Segment) =>
    ({ mid: 0, label: 1, horizontal: 2, vertical: 3 } as const)[s.layout || 'horizontal'] ?? 4
  const rank = Math.min(...hits.map(lane))
  const candidates = hits.filter((s) => lane(s) === rank)
  if (rank !== 0) return candidates[0] ?? null
  return candidates.reduce((best, seg) =>
    seg.start >= best.start ? seg : best)
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

/** Self-check cho clock preview/export: không dính speed sang cover/gap. */
export function __checkSpeedSegmentPick(): void {
  const segments = [
    { id: 'v', start: 0, end: 8, layout: 'vertical', videoSpeed: 1.2 },
    { id: 'h', start: 1, end: 4, layout: 'horizontal', videoSpeed: 0.9 },
    { id: 'm', start: 2, end: 3, layout: 'mid', videoSpeed: 0.6 },
  ] as Segment[]
  if (speedSegmentAt(segments, 2.5)?.id !== 'm') {
    throw new Error('Mid speed must win over overlapping lanes')
  }
  if (speedSegmentAt(segments, 3.5)?.id !== 'h') {
    throw new Error('Horizontal speed must win after Mid ends')
  }
  if (speedSegmentAt(segments, 8.1) !== null) {
    throw new Error('Speed must stop outside [start,end)')
  }
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
  const legacyMid = {
    id: 'legacy', start: 0, end: 1, layout: 'horizontal' as const,
    bboxInherited: false, bbox: { x: 80, y: 860, w: 920, h: 70 }, source: 'x', translation: 'x',
  } as Segment
  if (withInferredLayout(legacyMid, 1920).layout !== 'mid') {
    throw new Error('middle legacy bbox must not stay on the bottom lane')
  }
  const bottom = {
    id: 'bottom', start: 0, end: 1, layout: 'horizontal' as const,
    source: '', translation: 'Caption đáy',
  } as Segment
  if (captionLaneOf(bottom, 1920) !== 'horizontal') {
    throw new Error('bottom caption must keep the Caption lane')
  }
  if (effectiveOverlayLayout(bottom, 1920) !== 'mid') {
    throw new Error('bottom caption must share the mid bbox engine')
  }
  const portrait = captionMidBottomCutoff(1080, 1920)
  const landscape = captionMidBottomCutoff(1920, 1080)
  const fourFive = captionMidBottomCutoff(4, 5)
  const fourThree = captionMidBottomCutoff(4, 3)
  if (
    Math.abs(portrait - 0.78) > 1e-9
    || Math.abs(landscape - 0.72) > 1e-9
    || !(fourFive > 0.75 && fourFive < portrait)
    || !(fourThree < 0.75 && fourThree > landscape)
  ) {
    throw new Error('caption bottom band must follow the input aspect ratio')
  }
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

