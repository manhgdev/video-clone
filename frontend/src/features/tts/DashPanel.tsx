import { useEffect, useRef, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react'
import {
  type DashId,
  type DashItem,
  type DashLayout,
  type ResizeHandle,
  DASH_COLS,
  DASH_ROWS,
  itemStyle,
  moveDashItem,
  resizeFromHandle,
} from './ttsDashboardLayout'

const HANDLES: { id: ResizeHandle; label: string }[] = [
  { id: 'n', label: 'Resize trên' },
  { id: 's', label: 'Resize dưới' },
  { id: 'e', label: 'Resize phải' },
  { id: 'w', label: 'Resize trái' },
  { id: 'ne', label: 'Resize góc trên-phải' },
  { id: 'nw', label: 'Resize góc trên-trái' },
  { id: 'se', label: 'Resize góc dưới-phải' },
  { id: 'sw', label: 'Resize góc dưới-trái' },
]

type Props = {
  id: DashId
  item: DashItem
  active: boolean
  children: ReactNode
  onChange: (next: DashLayout | ((prev: DashLayout) => DashLayout)) => void
  onActive: (id: DashId | null) => void
  gridRef: RefObject<HTMLDivElement | null>
}

type DragState = {
  kind: 'resize' | 'move'
  handle?: ResizeHandle
  origin: DashItem
  startX: number
  startY: number
  cellW: number
  cellH: number
  pointerId: number
  lastDx: number
  lastDy: number
}

/** Grid panel: edge/corner resize + title move; free size via fine grid + peer push. */
export default function DashPanel({
  id,
  item,
  active,
  children,
  onChange,
  onActive,
  gridRef,
}: Props) {
  const dragRef = useRef<DragState | null>(null)
  const rafRef = useRef(0)
  const pendingRef = useRef<{ dx: number; dy: number } | null>(null)

  useEffect(() => {
    const flush = () => {
      rafRef.current = 0
      const drag = dragRef.current
      const pending = pendingRef.current
      if (!drag || !pending) return
      pendingRef.current = null
      const { dx, dy } = pending
      if (dx === drag.lastDx && dy === drag.lastDy) return
      drag.lastDx = dx
      drag.lastDy = dy
      if (drag.kind === 'resize' && drag.handle) {
        onChange((prev) => resizeFromHandle(prev, id, drag.origin, drag.handle!, dx, dy))
        return
      }
      onChange((prev) => moveDashItem(prev, id, drag.origin, dx, dy))
    }

    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current
      if (!drag || e.pointerId !== drag.pointerId) return
      e.preventDefault()
      const dx = Math.round((e.clientX - drag.startX) / drag.cellW)
      const dy = Math.round((e.clientY - drag.startY) / drag.cellH)
      pendingRef.current = { dx, dy }
      if (!rafRef.current) rafRef.current = requestAnimationFrame(flush)
    }

    const onUp = (e: PointerEvent) => {
      const drag = dragRef.current
      if (!drag || e.pointerId !== drag.pointerId) return
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = 0
      }
      if (pendingRef.current) flush()
      dragRef.current = null
      pendingRef.current = null
      document.body.classList.remove('tts-dash-resizing')
      document.body.style.removeProperty('cursor')
      onActive(null)
    }

    window.addEventListener('pointermove', onMove, { passive: false })
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [id, onActive, onChange])

  const begin = (kind: 'resize' | 'move', e: ReactPointerEvent, handle?: ResizeHandle) => {
    e.preventDefault()
    e.stopPropagation()
    const grid = gridRef.current?.getBoundingClientRect()
    if (!grid || grid.width < 1 || grid.height < 1) return
    dragRef.current = {
      kind,
      handle,
      origin: { ...item },
      startX: e.clientX,
      startY: e.clientY,
      cellW: Math.max(1, grid.width / DASH_COLS),
      cellH: Math.max(1, grid.height / DASH_ROWS),
      pointerId: e.pointerId,
      lastDx: 0,
      lastDy: 0,
    }
    document.body.classList.add('tts-dash-resizing')
    document.body.style.cursor = handle ? cursorFor(handle) : 'move'
    onActive(id)
  }

  const beginMove = (e: ReactPointerEvent) => {
    if (e.button !== 0) return
    const t = e.target as HTMLElement
    if (t.closest('input, textarea, select, button, a, label, .tts-dash-handle')) return
    if (!t.closest('.tts-card-title')) return
    begin('move', e)
  }

  return (
    <div
      className={`tts-dash-item${active ? ' is-active' : ''}`}
      style={itemStyle(item)}
      data-dash-id={id}
      onPointerDown={beginMove}
    >
      <div className="tts-dash-body">{children}</div>
      {HANDLES.map((h) => (
        <div
          key={h.id}
          role="separator"
          aria-label={h.label}
          title={h.label}
          className={`tts-dash-handle tts-dash-handle-${h.id}`}
          onPointerDown={(e) => begin('resize', e, h.id)}
        />
      ))}
    </div>
  )
}

function cursorFor(handle: ResizeHandle): string {
  switch (handle) {
    case 'n':
    case 's':
      return 'ns-resize'
    case 'e':
    case 'w':
      return 'ew-resize'
    case 'ne':
    case 'sw':
      return 'nesw-resize'
    case 'nw':
    case 'se':
      return 'nwse-resize'
  }
}
