import type { Segment } from '@/features/project/project.types'
import type { MediaClip } from './mediaClips'
import { segmentHasDub } from './segmentQuery'

/** TTS manual speed từng câu (không gồm bake global). */
export function dubManualSpeed(seg: Segment): number {
  return Math.max(0.75, Math.min(1.5, seg.ttsSpeed ?? 1))
}

/**
 * playbackRate TTS thật khi preview.
 * File wav luôn 1×; timeline đã scale theo bake → phải * bakedSpeed
 * (0.8 bake → TTS chậm 0.8×, dài gấp 1.25; 2× bake → TTS nhanh 2×).
 */
export function dubPlaybackSpeed(seg: Segment, bakedSpeed = 1): number {
  const bake =
    typeof bakedSpeed === 'number' && bakedSpeed > 0.2
      ? Math.max(0.5, Math.min(2, bakedSpeed))
      : 1
  return Math.max(0.5, Math.min(2, dubManualSpeed(seg) * bake))
}

/**
 * Playback rate preview — khớp thước + xuất.
 * Chỉ videoSpeed câu; không soft 0.8 ngầm. Muốn 0.80×: bấm Áp dụng (bake).
 */
export function previewVideoRate(
  _matchDuration: string | undefined,
  _bakedPreferVideo?: boolean,
  segSpeed?: number,
  _bakedSpeed?: number,
  _hasBakedSpeed?: boolean,
): number {
  const vs =
    typeof segSpeed === 'number' && segSpeed > 0.2
      ? Math.max(0.35, Math.min(2, segSpeed))
      : 1
  return vs
}

/** Tốc độ bake file thật (1 = file 1×). Soft preferVideo không tính. */
export function fileBakedSpeed(
  bakedSpeed?: number,
  bakedPreferVideo?: boolean,
  hasBakedSpeed?: boolean,
): number {
  if (typeof bakedSpeed === 'number' && bakedSpeed > 0.2 && (hasBakedSpeed || Math.abs(bakedSpeed - 1) > 0.02)) {
    return Math.max(0.5, Math.min(2, bakedSpeed))
  }
  if (bakedPreferVideo) return 0.8
  return 1
}

/** Slider = tốc độ file đã áp dụng (1.00× nếu chưa bake). Không giả soft 0.8. */
export function displaySpeedDraft(
  _matchDuration: string | undefined,
  bakedSpeed?: number,
  bakedPreferVideo?: boolean,
  hasBakedSpeed?: boolean,
): number {
  return fileBakedSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed)
}

/** Format 0.80× / 1.00× / 1.15× */
export function formatSpeedX(speed: number): string {
  const v = Math.round(Math.max(0.5, Math.min(2, speed)) * 100) / 100
  return `${v.toFixed(2)}×`
}

/** Tốc độ file trên đĩa sau Áp dụng (1.00 nếu chưa). */
export function appliedFileSpeed(
  bakedSpeed?: number,
  bakedPreferVideo?: boolean,
  hasBakedSpeed?: boolean,
): number {
  return fileBakedSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed)
}

/**
 * Nhãn rõ: đầu vào (file) vs đã áp dụng vs xuất = thước.
 */
export function speedStatusLines(
  matchDuration: string | undefined,
  draftSpeed: number,
  bakedSpeed?: number,
  bakedPreferVideo?: boolean,
  hasBakedSpeed?: boolean,
): { inputLine: string; appliedLine: string; exportLine: string; matchLabel: string } {
  const draft = Math.round(Math.max(0.5, Math.min(2, draftSpeed)) * 100) / 100
  const applied = appliedFileSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed)
  const locked =
    Boolean(hasBakedSpeed)
    || Boolean(bakedPreferVideo)
    || Math.abs(applied - 1) > 0.02

  let matchLabel = 'Khớp: theo cài đặt'
  if (matchDuration === 'preferVideo') {
    matchLabel = locked
      ? `Khớp preferVideo · file ${formatSpeedX(applied)}`
      : 'Khớp preferVideo · file 1.00× (chưa Áp dụng tốc độ)'
  } else if (matchDuration === 'stretch') matchLabel = 'Khớp: kéo TTS · file theo Áp dụng'
  else if (matchDuration === 'natural') matchLabel = 'Khớp: tự nhiên · file theo Áp dụng'
  else if (matchDuration === 'none') matchLabel = 'Khớp: không · file theo Áp dụng'

  const inputLine = locked
    ? `Đầu vào (file đang phát): ${formatSpeedX(applied)}`
    : 'Đầu vào (file đang phát): 1.00×'

  const appliedLine = locked
    ? `Đã áp dụng cho tất cả: ${formatSpeedX(applied)} (file + timeline + xuất)`
    : Math.abs(draft - 1) > 0.02
      ? `Đã áp dụng cho tất cả: chưa — chọn ${formatSpeedX(draft)}, bấm Áp dụng`
      : 'Đã áp dụng cho tất cả: chưa — file 1.00×'

  const exportLine = locked
    ? `Xuất = thước timeline @ file ${formatSpeedX(applied)}`
    : `Xuất = thước timeline @ 1.00× (bấm Áp dụng ${formatSpeedX(draft)} nếu muốn chậm/nhanh)`

  return { inputLine, appliedLine, exportLine, matchLabel }
}

/** Scale media clip list theo bake speed (Video / Âm gốc local). */
export function scaleMediaClips(list: MediaClip[], scale: number): MediaClip[] {
  if (!list.length || Math.abs(scale - 1) < 1e-9) return list
  return list.map((c) => ({
    ...c,
    start: Math.max(0, c.start * scale),
    end: Math.max(0.05, c.end * scale),
  }))
}

function _roundUs(t: number): number {
  return Math.round(t * 1_000_000) / 1_000_000
}

/** Quy clip display → mốc 1× (baseline bất biến). */
export function mediaClipsTo1xBaseline(list: MediaClip[], currentSpeed: number): MediaClip[] {
  const s = Math.max(0.5, Math.min(2, currentSpeed || 1))
  if (!list.length || Math.abs(s - 1) < 1e-9) {
    return list.map((c) => ({ ...c, start: _roundUs(c.start), end: _roundUs(c.end) }))
  }
  return list.map((c) => ({
    ...c,
    start: _roundUs(c.start * s),
    end: _roundUs(Math.max(0.05, c.end * s)),
    // sourceStart giữ toạ độ file nguồn 1×
  }))
}

/** Từ baseline 1× → display @ speed (mọi lần từ cùng gốc — không cascade). */
export function mediaClipsFrom1xBaseline(baseline: MediaClip[], speed: number): MediaClip[] {
  const s = Math.max(0.5, Math.min(2, speed || 1))
  const scale = 1 / s
  if (!baseline.length) return []
  if (Math.abs(scale - 1) < 1e-9) {
    return baseline.map((c) => ({ ...c, start: _roundUs(c.start), end: _roundUs(c.end) }))
  }
  return baseline.map((c) => ({
    ...c,
    start: _roundUs(c.start * scale),
    end: _roundUs(Math.max(0.05, c.end * scale)),
  }))
}

/**
 * Media-time cần để phát hết TTS khi video chạy `videoRate`.
 * wall = ad / (ttsManual * bake); media = wall * videoRate.
 * bake≠1 → TTS thật nhanh/chậm + clip timeline co giãn khớp caption.
 */
export function dubAudioAbsEnd(
  seg: Segment,
  _segments: Segment[],
  videoRate = 1,
  bakedSpeed = 1,
): number {
  const ttsSpeed = dubPlaybackSpeed(seg, bakedSpeed)
  const ad = seg.audioDuration ?? 0
  const rate = Math.max(0.2, videoRate)
  if (ad > 0.05) {
    return seg.start + (ad / Math.max(0.5, ttsSpeed)) * rate + 0.04
  }
  return Math.max(seg.end, seg.start + 0.05)
}

/** Segment TTS dưới playhead — bỏ qua id đã đọc xong (tránh lặp). */
export function segmentForDub(
  segments: Segment[],
  time: number,
  videoRate = 1,
  finishedIds?: Set<string>,
  bakedSpeed = 1,
): Segment | null {
  let best: Segment | null = null
  for (const s of segments) {
    if (!segmentHasDub(s) || !s.audioUrl) continue
    if (finishedIds?.has(s.id)) continue
    if (time + 0.03 < s.start) continue
    if (time >= dubAudioAbsEnd(s, segments, videoRate, bakedSpeed)) continue
    // Ưu tiên câu bắt đầu gần playhead nhất (không nhảy lung tung)
    if (
      !best
      || Math.abs(s.start - time) < Math.abs(best.start - time)
      || (Math.abs(s.start - time) === Math.abs(best.start - time) && s.start > best.start)
    ) {
      best = s
    }
  }
  return best
}

/** Chiều rộng clip TTS trên timeline (giây media) */
export function dubClipSeconds(
  seg: Segment,
  segments: Segment[],
  videoRate = 1,
  bakedSpeed = 1,
): number {
  return Math.max(0.05, dubAudioAbsEnd(seg, segments, videoRate, bakedSpeed) - seg.start)
}
