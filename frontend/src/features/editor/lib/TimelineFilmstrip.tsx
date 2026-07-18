import { useEffect, useMemo, useRef } from 'react'
import { cn } from '@/shared/lib/cn'

/** Filmstrip timeline — ít khung + URL ổn định (bỏ ?v=) để không storm Range → WinError 10055. */
export function TimelineFilmstrip({
  videoUrl,
  duration,
  widthPx,
  heightPx,
  className,
  startSec = 0,
  endSec,
}: {
  videoUrl: string
  duration: number
  widthPx: number
  heightPx: number
  className?: string
  /** Cửa sổ media (giây) — clip đã split/xóa chỉ vẽ đoạn này */
  startSec?: number
  endSec?: number
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  // Bỏ query cache-bust — cùng file MP4 không tải lại 48 lần mỗi poll
  const stableUrl = useMemo(() => (videoUrl || '').split('?')[0], [videoUrl])
  // Làm tròn width để zoom mượt không re-seek liên tục
  const stripW = Math.max(1, Math.round(widthPx / 64) * 64)
  const t0 = Math.max(0, startSec)
  const t1 = Math.max(t0 + 0.05, endSec ?? duration)

  useEffect(() => {
    if (!stableUrl || duration <= 0 || stripW <= 0) return
    let cancelled = false
    const video = document.createElement('video')
    video.src = stableUrl
    video.muted = true
    video.playsInline = true
    video.preload = 'metadata'
    const span = Math.max(0.05, t1 - t0)
    const mediaCap = Math.max(duration, t1)

    const seekTo = (t: number) => new Promise<void>((resolve) => {
      const done = () => { video.removeEventListener('seeked', done); resolve() }
      video.addEventListener('seeked', done)
      try {
        video.currentTime = Math.max(0, Math.min(mediaCap - 0.04, t))
      } catch {
        resolve()
      }
    })

    void (async () => {
      try {
        await new Promise<void>((resolve, reject) => {
          video.onloadeddata = () => resolve()
          video.onerror = () => reject(new Error('filmstrip'))
        })
        if (cancelled) return
        const canvas = canvasRef.current
        const ctx = canvas?.getContext('2d')
        if (!canvas || !ctx) return
        const w = Math.max(1, Math.round(widthPx))
        const h = Math.max(1, Math.round(heightPx))
        canvas.width = w
        canvas.height = h
        // Tối đa 16 khung — đủ filmstrip, tránh 48× Range request
        const n = Math.max(1, Math.min(16, Math.ceil(w / 80)))
        const tw = w / n
        const vW = video.videoWidth || 16
        const vH = video.videoHeight || 9
        const scale = Math.max(tw / vW, h / vH)
        const dw = vW * scale
        const dh = vH * scale
        for (let i = 0; i < n; i++) {
          if (cancelled) return
          await seekTo(t0 + ((i + 0.5) / n) * span)
          if (cancelled) return
          const dx = i * tw + (tw - dw) / 2
          const dy = (h - dh) / 2
          ctx.drawImage(video, dx, dy, dw, dh)
          if (i < n - 1) {
            ctx.fillStyle = 'rgba(0,0,0,0.35)'
            ctx.fillRect(i * tw + tw - 1, 0, 1, h)
          }
        }
      } catch { /* preview optional */ }
    })()

    return () => {
      cancelled = true
      try {
        video.pause()
        video.removeAttribute('src')
        video.load()
      } catch { /* ignore */ }
    }
  }, [stableUrl, duration, stripW, widthPx, heightPx, t0, t1])

  return (
    <canvas
      ref={canvasRef}
      className={cn('pointer-events-none select-none', className)}
      style={{ width: widthPx, height: heightPx }}
      aria-hidden
    />
  )
}

