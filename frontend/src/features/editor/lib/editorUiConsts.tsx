import React from 'react'

/** Project rail: actions affecting the project, not the selected clip. */
export type AssetsTab = 'workflow' | 'media' | 'captions' | 'speakers' | 'add' | 'logo'

export const ASSET_TABS: { key: AssetsTab; label: string; icon: React.ReactNode }[] = [
  { key: 'workflow', label: 'Quy trình', icon: <TabSvg><path d="M5 4h14M5 12h14M5 20h14" /><circle cx="8" cy="4" r="1" /><circle cx="16" cy="12" r="1" /><circle cx="10" cy="20" r="1" /></TabSvg> },
  { key: 'media', label: 'Media', icon: <TabSvg><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" /></TabSvg> },
  { key: 'captions', label: 'Phụ đề', icon: <TabSvg><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 15h4M15 15h2M7 11h2M13 11h4" /></TabSvg> },
  { key: 'speakers', label: 'Người nói', icon: <TabSvg><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></TabSvg> },
  { key: 'add', label: 'Thêm', icon: <TabSvg><path d="M12 5v14M5 12h14" /></TabSvg> },
  { key: 'logo', label: 'Logo', icon: <TabSvg><path d="M4 5h16v14H4z" /><path d="m7 15 3-3 2 2 3-4 2 5" /><circle cx="9" cy="9" r="1" /></TabSvg> },
]

export function TabSvg({ children }: { children: React.ReactNode }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{children}</svg>
}

export const FONT_SIZES = [16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96, 120]

export type PropTab = 'caption' | 'video' | 'audio' | 'mask' | 'overlay'
export type TrackId = 'video' | 'caption' | 'dub' | 'bg' | 'watermark' | 'ocr' | 'text'
export type CtxMenu =
  | { kind: 'segment'; segId: string; ids?: string[]; x: number; y: number }
  | { kind: 'dub'; segId: string; ids?: string[]; x: number; y: number }
  | { kind: 'bg'; x: number; y: number }
  | { kind: 'overlay'; overlayId: string; x: number; y: number }
  | { kind: 'track'; track: TrackId; x: number; y: number }

export function emptyTrackFlags(): Record<TrackId, boolean> {
  return { video: false, caption: false, dub: false, bg: false, watermark: false, ocr: false, text: false }
}

export function defaultTrackMute(): Record<TrackId, boolean> {
  return { ...emptyTrackFlags(), video: true }
}
