import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'

const AUDIO_WAVEFORM_PATTERN = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Cpath d='M2 40V15m4 25V25m4 15V10m4 30V20m4 20V5m4 35V22m4 18V12m4 28V30m4 10V18m4 22V8' stroke='white' stroke-opacity='0.25' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E")`

export function AudioWaveformBg() {
  return (
    <div
      className="absolute inset-0 pointer-events-none opacity-50 mix-blend-plus-lighter"
      style={{ backgroundImage: AUDIO_WAVEFORM_PATTERN, backgroundPosition: 'bottom' }}
    />
  )
}

export function VolumeSlider({
  initialVolume,
  maxVolume = 100,
  onChangeEnd,
  className = '',
}: {
  initialVolume: number
  maxVolume?: number
  onChangeEnd: (vol: number) => void
  className?: string
}) {
  const [vol, setVol] = useState(initialVolume)
  const [dragging, setDragging] = useState(false)
  const [dragPos, setDragPos] = useState({ x: 0, y: 0 })
  const startYRef = useRef(0)
  const startVolRef = useRef(0)

  // Sync prop if changed externally while not dragging
  useEffect(() => {
    if (!dragging) {
      setVol(initialVolume)
    }
  }, [initialVolume, dragging])

  const topPct = 100 - (vol / maxVolume) * 100

  return (
    <div 
      className={`absolute left-0 right-0 h-4 -mt-2 cursor-ns-resize group z-20 ${className}`}
      style={{ top: `${topPct}%` }}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        e.stopPropagation() // Ngăn không cho clip bị di chuyển (move clip)
        e.currentTarget.setPointerCapture(e.pointerId)
        setDragging(true)
        setDragPos({ x: e.clientX, y: e.clientY })
        startYRef.current = e.clientY
        startVolRef.current = vol
      }}
      onPointerMove={(e) => {
        if (!dragging) return
        e.stopPropagation()
        setDragPos({ x: e.clientX, y: e.clientY })
        const deltaY = e.clientY - startYRef.current
        // Clip height is usually ~28px.
        // A full drag from top to bottom (28px) is 100% volume change.
        const height = e.currentTarget.parentElement?.clientHeight || 28
        const deltaPct = (deltaY / height) * 100
        const newVol = Math.max(0, Math.min(maxVolume, startVolRef.current - deltaPct * (maxVolume / 100)))
        setVol(newVol)
      }}
      onPointerUp={(e) => {
        if (!dragging) return
        e.stopPropagation()
        e.currentTarget.releasePointerCapture(e.pointerId)
        setDragging(false)
        onChangeEnd(Math.round(vol))
      }}
    >
      <div className="absolute left-0 right-0 top-1/2 h-[2px] bg-white/70 group-hover:bg-white shadow-[0_1px_2px_rgba(0,0,0,0.4)] pointer-events-none" />
      <div className="absolute left-1/2 -translate-x-1/2 top-1/2 -mt-1.5 size-3 rounded-full bg-white shadow-md opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
      
      {dragging && typeof document !== 'undefined' && createPortal(
        <div 
          className="fixed bg-black/85 text-white text-[10px] px-1.5 py-0.5 rounded shadow-xl whitespace-nowrap pointer-events-none font-medium z-[9999]"
          style={{ top: dragPos.y - 30, left: dragPos.x, transform: 'translateX(-50%)' }}
        >
          {Math.round(vol)}%
        </div>,
        document.body
      )}
    </div>
  )
}
