import type { Segment, TextOverlay, ProjectSettings } from '@/features/project/project.types'
import type { MediaClip } from './mediaClips'

export type EditorSnap = {
  segments: Segment[]
  overlays: TextOverlay[]
  settings: ProjectSettings
  bookmarks: number[]
  selectedId: string | null
  selectedOverlayId: string | null
  trackFocus: 'video' | 'caption' | 'dub' | 'bg' | 'watermark' | 'text'
  videoClips: MediaClip[]
  bgClips: MediaClip[]
  selectedMediaId: string | null
  /** Bake tốc độ lúc snapshot — undo/redo gọi rebake nếu khác */
  bakedSpeed: number
  workClipSec: number
  mediaDuration: number
}

export const HISTORY_MAX = 40

export function cloneSnap(s: EditorSnap): EditorSnap {
  return {
    segments: s.segments.map((x) => ({ ...x, compoundChildren: x.compoundChildren?.map((c) => ({ ...c })) })),
    overlays: s.overlays.map((x) => ({ ...x })),
    settings: { ...s.settings },
    bookmarks: [...s.bookmarks],
    selectedId: s.selectedId,
    selectedOverlayId: s.selectedOverlayId,
    trackFocus: s.trackFocus,
    videoClips: s.videoClips.map((x) => ({ ...x })),
    bgClips: s.bgClips.map((x) => ({ ...x })),
    selectedMediaId: s.selectedMediaId,
    bakedSpeed: s.bakedSpeed,
    workClipSec: s.workClipSec,
    mediaDuration: s.mediaDuration,
  }
}
