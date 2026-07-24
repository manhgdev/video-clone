import React, { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

interface CoverPickerModalProps {
  isOpen: boolean
  onClose: () => void
  onConfirm: (dataUrl: string) => void
  videoUrl?: string | null
}

const FRAME_COUNT = 10

function useCapturedFrames(isOpen: boolean, videoUrl: string | null | undefined) {
  const [frames, setFrames] = useState<string[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!isOpen || !videoUrl) {
      setFrames([])
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setFrames([])

    const vid = document.createElement('video')
    vid.crossOrigin = 'anonymous'
    vid.src = videoUrl
    vid.muted = true
    vid.preload = 'metadata'

    const run = async () => {
      try {
        await new Promise<void>((resolve, reject) => {
          vid.addEventListener('loadedmetadata', () => resolve(), { once: true })
          vid.addEventListener('error', () => reject(new Error('video error')), { once: true })
          vid.load()
        })
        const dur = Math.max(1, vid.duration)
        const times = Array.from({ length: FRAME_COUNT }, (_, i) =>
          (i / (FRAME_COUNT - 1)) * Math.max(0.1, dur - 0.3),
        )
        const results: string[] = []
        for (const t of times) {
          if (cancelled) return
          await new Promise<void>((resolve) => {
            vid.addEventListener('seeked', () => resolve(), { once: true })
            vid.currentTime = t
          })
          if (cancelled) return
          try {
            // ponytail: scale giữ đúng tỷ lệ — cập cả 2 chiều độc lập bị méo video dọc
            const MAX = 640
            const scale = Math.min(MAX / (vid.videoWidth || 320), MAX / (vid.videoHeight || 180))
            const cw = Math.round((vid.videoWidth || 320) * scale)
            const ch = Math.round((vid.videoHeight || 180) * scale)
            const canvas = document.createElement('canvas')
            canvas.width = cw
            canvas.height = ch
            canvas.getContext('2d')?.drawImage(vid, 0, 0, cw, ch)
            results.push(canvas.toDataURL('image/jpeg', 0.78))
          } catch {
            results.push('')
          }
          if (!cancelled) setFrames([...results])
        }
      } catch {
        /* ignore load errors */
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    run()
    return () => {
      cancelled = true
      vid.src = ''
    }
  }, [isOpen, videoUrl])

  return { frames, loading }
}

export function CoverPickerModal({ isOpen, onClose, onConfirm, videoUrl }: CoverPickerModalProps) {
  const [tab, setTab] = useState<'video' | 'device'>('video')
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [deviceDataUrl, setDeviceDataUrl] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const filmstripRef = useRef<HTMLDivElement>(null)
  const { frames, loading } = useCapturedFrames(isOpen, videoUrl)

  // Drag-to-scroll filmstrip
  function onFilmstripMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    const el = filmstripRef.current
    if (!el) return
    const startX = e.pageX
    const scrollLeft = el.scrollLeft
    const onMove = (ev: MouseEvent) => { el.scrollLeft = scrollLeft - (ev.pageX - startX) }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  useEffect(() => {
    if (isOpen) {
      setTab('video')
      setSelectedIdx(0)
      setDeviceDataUrl(null)
    }
  }, [isOpen])

  if (!isOpen) return null

  const previewSrc = tab === 'device' ? deviceDataUrl : (frames[selectedIdx] ?? null)
  const canConfirm = !!previewSrc
  const scrubPct = frames.length > 1 ? (selectedIdx / (frames.length - 1)) * 100 : 0

  function handleDeviceFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      const r = ev.target?.result
      if (typeof r === 'string') setDeviceDataUrl(r)
    }
    reader.readAsDataURL(file)
    e.target.value = ''
  }

  function handleConfirm() {
    if (previewSrc) { onConfirm(previewSrc); onClose() }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center backdrop-blur-sm"
      style={{ backgroundColor: 'rgba(0,0,0,0.8)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="flex flex-col w-[680px] max-w-[95vw] rounded-xl overflow-hidden font-sans select-none shadow-2xl border"
        style={{
          backgroundColor: 'var(--card, #18191c)',
          color: 'var(--foreground, #e4e4e7)',
          borderColor: 'var(--border, #27272a)',
        }}
      >
        {/* ── Header ── */}
        <div
          className="flex items-center justify-between px-5 py-3 border-b"
          style={{ backgroundColor: 'var(--surface, #131417)', borderColor: 'var(--border, #27272a)' }}
        >
          <span className="text-sm font-semibold" style={{ color: 'var(--foreground, #ffffff)' }}>
            Chọn ảnh bìa
          </span>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md opacity-60 hover:opacity-100 transition-opacity"
            style={{ color: 'var(--muted-foreground, #a1a1aa)' }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* ── Preview ── */}
        <div
          className="flex items-center justify-center py-5 px-6"
          style={{ minHeight: '300px', backgroundColor: 'var(--preview-workspace-bg, #000000)' }}
        >
          {loading && frames.length === 0 ? (
            <div className="flex flex-col items-center gap-2 opacity-40" style={{ color: 'var(--muted-foreground, #a1a1aa)' }}>
              <svg className="animate-spin" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 12a9 9 0 1 1-6.22-8.56" />
              </svg>
              <span className="text-xs">Đang tải khung hình…</span>
            </div>
          ) : previewSrc ? (
            <img
              src={previewSrc}
              alt="Ảnh bìa xem trước"
              className="max-h-[300px] max-w-full object-contain rounded-md"
              style={{ boxShadow: '0 4px 24px rgba(0,0,0,0.6)' }}
            />
          ) : (
            <div className="text-xs opacity-30" style={{ color: 'var(--muted-foreground, #a1a1aa)' }}>
              Chưa có ảnh bìa
            </div>
          )}
        </div>

        {/* ── Tabs ── */}
        <div
          className="flex items-center justify-center gap-2 py-3"
          style={{ backgroundColor: 'var(--card, #18191c)' }}
        >
          {(['video', 'device'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className="px-4 py-1.5 rounded-md text-xs font-medium transition-colors"
              style={{
                backgroundColor: tab === t ? 'var(--muted, #2d2e34)' : 'transparent',
                color: tab === t ? 'var(--foreground, #ffffff)' : 'var(--muted-foreground, #a1a1aa)',
              }}
            >
              {t === 'video' ? 'Chọn từ video' : 'Trên thiết bị'}
            </button>
          ))}
        </div>

        {/* ── Filmstrip (video tab) ── */}
        {tab === 'video' && (
          <div className="px-4 pb-4" style={{ backgroundColor: 'var(--card, #18191c)' }}>
            {/* Scrubber */}
            <div
              className="relative h-[3px] rounded-full mb-2.5 mx-1"
              style={{ backgroundColor: 'var(--muted, #2d2e34)' }}
            >
              <div
                className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-[3px] h-5 rounded-sm"
                style={{ left: `${scrubPct}%`, backgroundColor: 'var(--foreground, #ffffff)' }}
              />
            </div>
            {/* Frames */}
            <div
              ref={filmstripRef}
              className="flex gap-1 overflow-x-auto pb-1 cursor-grab active:cursor-grabbing"
              style={{ scrollbarWidth: 'none' }}
              onMouseDown={onFilmstripMouseDown}
            >
              {Array.from({ length: FRAME_COUNT }).map((_, i) => {
                const f = frames[i]
                const isSelected = i === selectedIdx && tab === 'video'
                return (
                  <button
                    key={i}
                    type="button"
                    onClick={() => { setSelectedIdx(i); setTab('video') }}
                    className="shrink-0 w-[72px] h-[48px] rounded overflow-hidden transition-all"
                    style={{
                      outline: isSelected ? '2px solid #00c4cc' : '2px solid transparent',
                      outlineOffset: '1px',
                      backgroundColor: 'var(--muted, #2d2e34)',
                      opacity: loading && !f ? 0.4 : 1,
                    }}
                  >
                    {f
                      ? <img src={f} alt={`Frame ${i + 1}`} className="w-full h-full object-cover" />
                      : <div className="w-full h-full animate-pulse" style={{ backgroundColor: 'var(--border, #27272a)' }} />
                    }
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* ── Device tab ── */}
        {tab === 'device' && (
          <div
            className="flex flex-col items-center justify-center py-8 gap-3"
            style={{ backgroundColor: 'var(--card, #18191c)' }}
          >
            <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleDeviceFile} />
            {deviceDataUrl ? (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="text-xs underline transition-opacity hover:opacity-70"
                style={{ color: '#00c4cc' }}
              >
                Đổi ảnh khác
              </button>
            ) : (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="px-5 py-2 rounded-md text-sm font-medium transition-colors hover:opacity-80"
                style={{ backgroundColor: 'var(--muted, #2d2e34)', color: 'var(--foreground, #e4e4e7)' }}
              >
                Chọn ảnh từ thiết bị
              </button>
            )}
            <span className="text-xs" style={{ color: 'var(--muted-foreground, #71717a)' }}>
              JPG, PNG, WEBP…
            </span>
          </div>
        )}

        {/* ── Footer ── */}
        <div
          className="flex items-center justify-between px-5 py-3 border-t"
          style={{ backgroundColor: 'var(--surface, #131417)', borderColor: 'var(--border, #27272a)' }}
        >
          <button
            type="button"
            className="px-4 py-1.5 rounded-md text-xs transition-colors hover:opacity-80"
            style={{ color: 'var(--foreground, #e4e4e7)' }}
            onClick={() => {
              // ponytail: "Tạo ảnh bìa mới" = upload từ thiết bị; editor ảnh sẽ implement sau
              setTab('device')
              fileInputRef.current?.click()
            }}
          >
            Tạo ảnh bìa mới
          </button>
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={!canConfirm}
              onClick={handleConfirm}
              className="px-5 py-1.5 rounded-md text-xs font-semibold transition-colors disabled:opacity-40 cursor-pointer"
              style={{ backgroundColor: '#00c4cc', color: '#000000' }}
            >
              Chỉnh sửa ảnh bìa
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-1.5 rounded-md text-xs transition-colors hover:opacity-80"
              style={{ color: 'var(--muted-foreground, #a1a1aa)' }}
            >
              Hủy
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
