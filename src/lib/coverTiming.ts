import type { Segment } from '../types'

/** Cửa sổ che chữ — khớp server/pipeline/ocr/cover_timing.py + burn cues.
 * Lead nhỏ ở đầu (tránh mask hiện trước chữ). Tail giữ dài hơn.
 */

export type CoverWindow = { start: number; end: number }

export function coverLeadTail(
  layout: string,
  start: number,
  end: number,
  source = '',
): { lead: number; tail: number } {
  const lay = layout || 'horizontal'
  const dur = Math.max(0, end - start)
  if (lay === 'vertical') {
    // watermark: OCR hay trễ nhẹ
    return { lead: dur >= 2.5 ? 0.28 : 0.15, tail: 0 }
  }
  if (lay === 'label') return { lead: 0.28, tail: 0.12 }
  if (lay === 'mid') return { lead: 0.35, tail: 0.22 }
  let srcCjk = 0
  for (const c of source) {
    if (c >= '\u4e00' && c <= '\u9fff') srcCjk += 1
  }
  const lead = 0.25
  const tail = end - start <= 0.75 && srcCjk <= 4 ? 1.05 : 0.45
  return { lead, tail: Math.max(0.4, tail) }
}

export function defaultCoverWindow(seg: Pick<Segment, 'start' | 'end' | 'layout' | 'source'>): CoverWindow {
  const s0 = seg.start
  const e0 = seg.end
  const layout = seg.layout || 'horizontal'
  const { lead, tail } = coverLeadTail(layout, s0, e0, seg.source || '')
  let start = Math.max(0, s0 - lead)
  // mid/label: cho lead ~0.35 (OCR first-hit hay trễ); không kẹp 80ms
  if (layout === 'mid' || layout === 'label') {
    start = Math.max(0, Math.min(start, s0 - 0.08))
  }
  if (layout === 'vertical') {
    return { start, end: Math.max(e0, start + 0.04) }
  }
  if (layout === 'label') {
    return { start, end: Math.max(e0 + tail, start + 0.2) }
  }
  if (layout === 'mid') {
    return { start, end: Math.max(e0 + tail, start + 0.12) }
  }
  return { start, end: Math.max(e0 + Math.max(0.4, tail), start + 0.2) }
}

/** Ưu tiên coverStart/coverEnd đã lưu; fallback default. */
export function resolveCoverWindow(seg: Segment): CoverWindow {
  const cs = seg.coverStart
  const ce = seg.coverEnd
  if (
    typeof cs === 'number' &&
    typeof ce === 'number' &&
    Number.isFinite(cs) &&
    Number.isFinite(ce) &&
    ce > cs + 1e-6
  ) {
    // segment đã lưu quá sớm (OCR cũ kéo về mốc trống) → kẹp mid/label
    const layout = seg.layout || 'horizontal'
    let start = Math.max(0, cs)
    if ((layout === 'mid' || layout === 'label') && start < seg.start - 0.45) {
      start = Math.max(0, seg.start - 0.35)
    }
    const { tail } = coverLeadTail(layout, seg.start, seg.end, seg.source || '')
    // ponytail: coverEnd lưu có thể ngắn hơn clip timeline — luôn phủ hết [start,end)
    let end = Math.max(ce, seg.end, start + 0.04)
    if (layout === 'mid' || layout === 'label') {
      end = Math.max(end, seg.end + tail)
    }
    return { start, end }
  }
  return defaultCoverWindow(seg)
}

/** True khi cửa sổ che khác rõ so với dịch (hiện dòng "Che …"). */
export function coverDiffersFromDub(seg: Segment): boolean {
  const w = resolveCoverWindow(seg)
  return Math.abs(w.start - seg.start) > 0.04 || Math.abs(w.end - seg.end) > 0.04
}
