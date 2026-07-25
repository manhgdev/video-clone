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
 * Playback rate preview.
 *
 * - hasBakedSpeed / bakedPreferVideo / bakedSpeed≠1 → file đã bake (kể cả 1× user lock)
 *   → rate = 1 (× videoSpeed câu nếu TTS-fit)
 * - preferVideo + chưa user-bake → soft 0.80× qua playbackRate (file vẫn 1×)
 */
export function previewVideoRate(
  matchDuration: string | undefined,
  bakedPreferVideo?: boolean,
  segSpeed?: number,
  bakedSpeed?: number,
  hasBakedSpeed?: boolean,
): number {
  const speedOff1 =
    typeof bakedSpeed === 'number' && bakedSpeed > 0.2 && Math.abs(bakedSpeed - 1) > 0.02
  // User đã Áp dụng (1× hoặc ≠1) hoặc file bake ≠1 → không soft 0.8
  const fileOrLocked =
    Boolean(hasBakedSpeed) || Boolean(bakedPreferVideo) || speedOff1
  const vs =
    typeof segSpeed === 'number' && segSpeed > 0.2
      ? Math.max(0.35, Math.min(2, segSpeed))
      : 1
  if (fileOrLocked) return vs
  const base = matchDuration === 'preferVideo' ? 0.8 : 1
  return base * vs
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

/** Giá trị hiển thị slider: file bake, hoặc soft 0.8 preferVideo, hoặc 1. */
export function displaySpeedDraft(
  matchDuration: string | undefined,
  bakedSpeed?: number,
  bakedPreferVideo?: boolean,
  hasBakedSpeed?: boolean,
): number {
  const file = fileBakedSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed)
  if (hasBakedSpeed || Math.abs(file - 1) > 0.02) return file
  if (matchDuration === 'preferVideo') return 0.8
  return 1
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
