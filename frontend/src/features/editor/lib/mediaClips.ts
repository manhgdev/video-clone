import type { Segment, TextOverlay } from '@/features/project/project.types'
import { MIN_CLIP_SEC, SPLIT_EDGE } from './timelineBasics'

/** Clip Video / Âm gốc trên timeline (tách khỏi Caption·TTS) */
export type MediaClip = { id: string; start: number; end: number; sourceStart?: number }

export function fullMediaClip(end: number): MediaClip {
  return { id: crypto.randomUUID(), start: 0, end: Math.max(end, MIN_CLIP_SEC), sourceStart: 0 }
}

/**
 * Clamp media clips trong cửa sổ làm việc.
 * Không kéo 1 clip đã trim/xóa nửa về full span (lỗ trống giữ nguyên).
 * Chỉ stretch khi cửa sổ phình (preview N→full) và clip từng chạm mép cũ.
 */
export function normalizeMediaClips(clips: MediaClip[], durationSec: number, prevDuration = 0): MediaClip[] {
  if (!(durationSec > 0)) return []
  const next = clips
    .filter((c) => c && typeof c.start === 'number' && typeof c.end === 'number' && c.end > c.start)
    .map((c) => ({
      ...c,
      id: String(c.id || crypto.randomUUID()),
      start: Math.max(0, Math.min(c.start, durationSec - MIN_CLIP_SEC)),
      end: Math.max(MIN_CLIP_SEC, Math.min(c.end, durationSec)),
    } as MediaClip))
    .filter((c) => c.end - c.start >= SPLIT_EDGE)
    .sort((a, b) => a.start - b.start || a.end - b.end)
  if (!next.length) return [fullMediaClip(durationSec)]
  // Cửa sổ phình (5s -> 10s): tự động kéo nếu chỉ có 1 clip bao trùm từ đầu
  if (next.length === 1 && next[0].start === 0) {
    next[0].end = durationSec
    return next
  }
  // Cửa sổ phình (15s→full): kéo đuôi clip từng chạm mép duration cũ
  if (prevDuration > 0 && durationSec > prevDuration + 0.25) {
    return next.map((c) => {
      if (Math.abs(c.end - prevDuration) <= 0.51) {
        return { ...c, end: durationSec }
      }
      return c
    })
  }
  return next
}

export function mediaClipsKey(projectId: string, kind: 'video' | 'bg') {
  return `videoclone.${kind}Clips.${projectId}`
}

export function loadMediaClips(projectId: string, kind: 'video' | 'bg', durationSec: number): MediaClip[] {
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

export function persistMediaClips(projectId: string, kind: 'video' | 'bg', clips: MediaClip[]) {
  // ponytail: skip [] so projectId reset không ghi đè clip đã lưu
  if (!clips.length) return
  try {
    localStorage.setItem(mediaClipsKey(projectId, kind), JSON.stringify(clips))
  } catch { /* ignore */ }
}

export function splitMediaList(clips: MediaClip[], clipId: string, t: number): MediaClip[] {
  return clips.flatMap((c) => {
    if (c.id !== clipId) return [c]
    if (!(t > c.start + SPLIT_EDGE && t < c.end - SPLIT_EDGE)) return [c]
    return [
      { ...c, end: t },
      { ...c, id: crypto.randomUUID(), start: t, end: c.end, sourceStart: (c.sourceStart ?? c.start) + t - c.start },
    ]
  })
}

export function clipAtTime(clips: MediaClip[], t: number): MediaClip | null {
  return clips.find((c) => t >= c.start && t < c.end) ?? clips.find((c) => t >= c.start && t <= c.end) ?? null
}

/** Gộp khoảng [a,b) đã sort — dùng ripple delete. */
export function mergeTimeRanges(ranges: { start: number; end: number }[]): { start: number; end: number }[] {
  const sorted = ranges
    .filter((r) => r.end > r.start + 1e-6)
    .slice()
    .sort((a, b) => a.start - b.start)
  if (!sorted.length) return []
  const out: { start: number; end: number }[] = [{ ...sorted[0] }]
  for (let i = 1; i < sorted.length; i++) {
    const cur = sorted[i]
    const last = out[out.length - 1]
    if (cur.start <= last.end + 1e-4) last.end = Math.max(last.end, cur.end)
    else out.push({ ...cur })
  }
  return out
}

/** Tổng thời lượng bị xóa trước mốc t (để shift về 0). */
export function removedBefore(t: number, removed: { start: number; end: number }[]): number {
  let d = 0
  for (const r of removed) {
    if (r.end <= t) d += r.end - r.start
    else if (r.start < t) d += t - r.start
  }
  return d
}

/** Map mốc thời gian sau ripple — điểm nằm trong vùng xóa → mép trái vùng đó. */
export function mapTimeAfterRipple(t: number, removed: { start: number; end: number }[]): number {
  for (const r of removed) {
    if (t >= r.start && t < r.end) return Math.max(0, r.start - removedBefore(r.start, removed))
  }
  return Math.max(0, t - removedBefore(t, removed))
}

/** Xóa clip media + đóng gap (CapCut ripple): kéo phần sau về trước. */
export function rippleDeleteMediaClips(
  clips: MediaClip[],
  dropIds: Set<string>,
): { next: MediaClip[]; removed: { start: number; end: number }[] } {
  const removed = mergeTimeRanges(
    clips.filter((c) => dropIds.has(c.id)).map((c) => ({ start: c.start, end: c.end })),
  )
  if (!removed.length) {
    return { next: clips.filter((c) => !dropIds.has(c.id)), removed: [] }
  }
  const kept = clips
    .filter((c) => !dropIds.has(c.id))
    .map((c) => {
      const start = mapTimeAfterRipple(c.start, removed)
      const end = mapTimeAfterRipple(c.end, removed)
      return { ...c, start, end: Math.max(start + MIN_CLIP_SEC, end) }
    })
    .filter((c) => c.end - c.start >= SPLIT_EDGE)
    .sort((a, b) => a.start - b.start)
  return { next: kept, removed }
}

/** Shift segment/overlay theo vùng đã xóa (ripple toàn project). */
export function rippleShiftSegment(seg: Segment, removed: { start: number; end: number }[]): Segment | null {
  const start = mapTimeAfterRipple(seg.start, removed)
  const end = mapTimeAfterRipple(seg.end, removed)
  if (end - start < 0.04) return null
  const next: Segment = { ...seg, start, end }
  if (typeof seg.coverStart === 'number') {
    next.coverStart = mapTimeAfterRipple(seg.coverStart, removed)
  }
  if (typeof seg.coverEnd === 'number') {
    next.coverEnd = mapTimeAfterRipple(seg.coverEnd, removed)
  }
  if (seg.isCompound && seg.compoundChildren?.length) {
    // Children relative — chỉ scale nếu shell absolute times đổi span
    const oldSpan = Math.max(0.05, seg.end - seg.start)
    const newSpan = Math.max(0.05, end - start)
    const ratio = newSpan / oldSpan
    if (Math.abs(ratio - 1) > 1e-6) {
      next.compoundChildren = seg.compoundChildren.map((ch) => ({
        ...ch,
        start: (Number(ch.start) || 0) * ratio,
        end: (Number(ch.end) || 0) * ratio,
        coverStart:
          typeof ch.coverStart === 'number' ? ch.coverStart * ratio : undefined,
        coverEnd: typeof ch.coverEnd === 'number' ? ch.coverEnd * ratio : undefined,
      }))
    }
  }
  return next
}

export function rippleShiftOverlay(
  ov: TextOverlay,
  removed: { start: number; end: number }[],
): TextOverlay | null {
  const start = mapTimeAfterRipple(ov.start, removed)
  const end = mapTimeAfterRipple(ov.end, removed)
  if (end - start < 0.04) return null
  return { ...ov, start, end }
}
