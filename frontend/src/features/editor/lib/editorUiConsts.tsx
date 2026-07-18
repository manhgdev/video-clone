import React from 'react'

/* ── OpenCut assets-panel tab rail (same tabs as opencut.app) ── */
export type AssetsTab =
  | 'media' | 'sounds' | 'text' | 'stickers' | 'effects'
  | 'transitions' | 'captions' | 'filters' | 'adjustment' | 'settings'

export const ASSET_TABS: { key: AssetsTab; label: string; icon: React.ReactNode }[] = [
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

export function TabSvg({ children }: { children: React.ReactNode }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {children}
    </svg>
  )
}

export const FONT_SIZES = [16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96, 120]

export type PropTab = 'caption' | 'video' | 'audio' | 'mask' | 'overlay'
export type TrackId = 'video' | 'caption' | 'dub' | 'bg' | 'text'
export type CtxMenu =
  | { kind: 'segment'; segId: string; ids?: string[]; x: number; y: number }
  | { kind: 'dub'; segId: string; ids?: string[]; x: number; y: number }
  | { kind: 'bg'; x: number; y: number }
  | { kind: 'overlay'; overlayId: string; x: number; y: number }
  | { kind: 'track'; track: TrackId; x: number; y: number }

export function emptyTrackFlags(): Record<TrackId, boolean> {
  return { video: false, caption: false, dub: false, bg: false, text: false }
}

/** Video mặc định tắt tiếng — nghe từ Âm gốc / stem, tránh double audio */
export function defaultTrackMute(): Record<TrackId, boolean> {
  return { ...emptyTrackFlags(), video: true }
}
