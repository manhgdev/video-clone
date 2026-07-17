/** TTS overview dashboard: 12×2 CSS grid, resize/move, no overlap. */

import type { CSSProperties } from 'react'

export const TTS_DASH_LAYOUT_KEY = 'video-clone:tts-dash-layout:v4'
export const DASH_COLS = 12
export const DASH_ROWS = 2
export const DASH_MIN_W = 2
export const DASH_MIN_H = 1

export const DASH_IDS = ['input', 'voice', 'advanced', 'clone', 'preview', 'export'] as const
export type DashId = (typeof DASH_IDS)[number]

export type DashItem = { id: DashId; col: number; row: number; w: number; h: number }
export type DashLayout = Record<DashId, DashItem>
export type ResizeHandle = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'

/** Default = bố cục cũ 4 trên + 2 dưới. */
export const DEFAULT_DASH_LAYOUT: DashLayout = {
  input: { id: 'input', col: 0, row: 0, w: 3, h: 1 },
  voice: { id: 'voice', col: 3, row: 0, w: 3, h: 1 },
  advanced: { id: 'advanced', col: 6, row: 0, w: 3, h: 1 },
  clone: { id: 'clone', col: 9, row: 0, w: 3, h: 1 },
  preview: { id: 'preview', col: 0, row: 1, w: 8, h: 1 },
  export: { id: 'export', col: 8, row: 1, w: 4, h: 1 },
}

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n))
}

export function clampItem(item: DashItem): DashItem {
  const w = clamp(Math.round(item.w), DASH_MIN_W, DASH_COLS)
  const h = clamp(Math.round(item.h), DASH_MIN_H, DASH_ROWS)
  const col = clamp(Math.round(item.col), 0, DASH_COLS - w)
  const row = clamp(Math.round(item.row), 0, DASH_ROWS - h)
  return { id: item.id, col, row, w, h }
}

export function overlaps(a: DashItem, b: DashItem) {
  return a.col < b.col + b.w && a.col + a.w > b.col && a.row < b.row + b.h && a.row + a.h > b.row
}

function anyOverlap(layout: DashLayout, id: DashId, item: DashItem) {
  return DASH_IDS.some((other) => other !== id && overlaps(item, layout[other]))
}

/** Undo growth toward origin until no overlap (stop at neighbor). */
function shrinkGrownEdges(
  layout: DashLayout,
  id: DashId,
  candidate: DashItem,
  origin: DashItem,
  handle: ResizeHandle,
): DashItem {
  let next = clampItem(candidate)
  for (let guard = 0; guard < 48; guard++) {
    if (!anyOverlap(layout, id, next)) return next
    let changed = false
    if (handle.includes('e') && next.w > origin.w) {
      next = { ...next, w: next.w - 1 }
      changed = true
    } else if (handle.includes('w') && next.w > origin.w) {
      next = { ...next, col: next.col + 1, w: next.w - 1 }
      changed = true
    } else if (handle.includes('s') && next.h > origin.h) {
      next = { ...next, h: next.h - 1 }
      changed = true
    } else if (handle.includes('n') && next.h > origin.h) {
      next = { ...next, row: next.row + 1, h: next.h - 1 }
      changed = true
    }
    if (!changed) return clampItem(origin)
    next = clampItem(next)
  }
  return clampItem(origin)
}

export function parseDashLayout(raw: unknown): DashLayout {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return structuredClone(DEFAULT_DASH_LAYOUT)
  const src = raw as Record<string, unknown>
  const sample = src.input
  // Reject free-canvas {x,y} layouts
  if (sample && typeof sample === 'object' && 'x' in (sample as object) && !('col' in (sample as object))) {
    return structuredClone(DEFAULT_DASH_LAYOUT)
  }
  const next = structuredClone(DEFAULT_DASH_LAYOUT)
  for (const id of DASH_IDS) {
    const v = src[id]
    if (!v || typeof v !== 'object') continue
    const o = v as Record<string, unknown>
    next[id] = clampItem({
      id,
      col: Number(o.col),
      row: Number(o.row),
      w: Number(o.w),
      h: Number(o.h),
    })
  }
  return next
}

export function loadDashLayout(): DashLayout {
  try {
    const raw = localStorage.getItem(TTS_DASH_LAYOUT_KEY)
    if (!raw) return structuredClone(DEFAULT_DASH_LAYOUT)
    return parseDashLayout(JSON.parse(raw))
  } catch {
    return structuredClone(DEFAULT_DASH_LAYOUT)
  }
}

export function persistDashLayout(layout: DashLayout) {
  try {
    localStorage.setItem(TTS_DASH_LAYOUT_KEY, JSON.stringify(layout))
  } catch {
    /* ignore */
  }
}

export function itemStyle(item: DashItem): CSSProperties {
  const c = clampItem(item)
  return {
    gridColumn: `${c.col + 1} / span ${c.w}`,
    gridRow: `${c.row + 1} / span ${c.h}`,
  }
}

/** Resize from origin snapshot + cell deltas; never overlaps peers. */
export function resizeFromHandle(
  layout: DashLayout,
  id: DashId,
  origin: DashItem,
  handle: ResizeHandle,
  dx: number,
  dy: number,
): DashLayout {
  let col = origin.col
  let row = origin.row
  let w = origin.w
  let h = origin.h

  if (handle.includes('e')) w = origin.w + dx
  if (handle.includes('w')) {
    col = origin.col + dx
    w = origin.w - dx
  }
  if (handle.includes('s')) h = origin.h + dy
  if (handle.includes('n')) {
    row = origin.row + dy
    h = origin.h - dy
  }

  if (w < DASH_MIN_W) {
    if (handle.includes('w')) col = origin.col + origin.w - DASH_MIN_W
    w = DASH_MIN_W
  }
  if (h < DASH_MIN_H) {
    if (handle.includes('n')) row = origin.row + origin.h - DASH_MIN_H
    h = DASH_MIN_H
  }

  const next = shrinkGrownEdges(layout, id, { id, col, row, w, h }, origin, handle)
  return { ...layout, [id]: next }
}

/** Move by cell delta from origin; swap with peer if would overlap. */
export function moveDashItem(
  layout: DashLayout,
  id: DashId,
  origin: DashItem,
  dCol: number,
  dRow: number,
): DashLayout {
  const tentative = clampItem({ ...origin, col: origin.col + dCol, row: origin.row + dRow })
  const hit = DASH_IDS.find((other) => other !== id && overlaps(tentative, layout[other]))
  if (!hit) return { ...layout, [id]: tentative }
  const peer = layout[hit]
  // Swap top-left only — sizes stay; skip if swap still overlaps others
  const a = clampItem({ ...origin, col: peer.col, row: peer.row })
  const b = clampItem({ ...peer, col: origin.col, row: origin.row })
  const trial = { ...layout, [id]: a, [hit]: b }
  if (anyOverlap(trial, id, a) || anyOverlap(trial, hit, b)) return layout
  return trial
}

export function __checkDashLayout() {
  const a = clampItem({ id: 'input', col: 99, row: -2, w: 1, h: 9 })
  if (a.w !== 2 || a.h !== 2 || a.col !== 10 || a.row !== 0) throw new Error('clamp failed')

  const west = resizeFromHandle(DEFAULT_DASH_LAYOUT, 'voice', DEFAULT_DASH_LAYOUT.voice, 'w', -1, 0)
  // Growing west into input must stop — no overlap
  if (anyOverlap(west, 'voice', west.voice)) throw new Error('west overlap')
  if (west.voice.col !== 3 || west.voice.w !== 3) throw new Error('west should not grow into neighbor')

  const east = resizeFromHandle(DEFAULT_DASH_LAYOUT, 'input', DEFAULT_DASH_LAYOUT.input, 'e', 1, 0)
  if (anyOverlap(east, 'input', east.input)) throw new Error('east overlap')
  if (east.input.w !== 3) throw new Error('east should not grow into voice')

  const room = {
    ...DEFAULT_DASH_LAYOUT,
    voice: { id: 'voice' as const, col: 5, row: 0, w: 2, h: 1 },
    advanced: { id: 'advanced' as const, col: 8, row: 0, w: 2, h: 1 },
    clone: { id: 'clone' as const, col: 10, row: 0, w: 2, h: 1 },
  }
  const grown = resizeFromHandle(room, 'input', room.input, 'e', 1, 0)
  if (grown.input.w !== 4 || grown.input.col !== 0) throw new Error('east grow into gap failed')

  const swapped = moveDashItem(DEFAULT_DASH_LAYOUT, 'input', DEFAULT_DASH_LAYOUT.input, 3, 0)
  if (swapped.input.col !== 3 || swapped.voice.col !== 0) throw new Error('swap move failed')
}
