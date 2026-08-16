import { useEffect, useRef } from 'react'

/**
 * Plays a pre-rendered inpaint patch video in sync with the main video.
 * The patch video contains only the watermark region processed with cv2.inpaint,
 * giving a 100% match with the export result.
 *
 * Rendered at the same DOM level as the main video and positioned using
 * source-pixel coordinates via the crop rect.
 */
export function InpaintCanvas({
  videoEl,
  patchUrl,
  patchBox,
  crop,
}: {
  videoEl: HTMLVideoElement | null
  /** URL of the inpaint patch video */
  patchUrl: string
  /** Source-pixel placement of the patch (extended bbox with padding) */
  patchBox: {
    x: number; y: number; w: number; h: number
    origX?: number; origY?: number; origW?: number; origH?: number
  }
  /** Source-pixel crop rect (viewport) */
  crop: { x: number; y: number; w: number; h: number }
}) {
  const patchRef = useRef<HTMLVideoElement>(null)

  // Sync patch video playback with main video
  useEffect(() => {
    const main = videoEl
    const patch = patchRef.current
    if (!main || !patch) return

    const syncTime = () => {
      if (patch.readyState >= 1) {
        const diff = Math.abs(patch.currentTime - main.currentTime)
        if (diff > 0.08) {
          patch.currentTime = main.currentTime
        }
      }
    }

    const onPlay = () => {
      syncTime()
      patch.playbackRate = main.playbackRate
      patch.play().catch(() => {})
    }
    const onPause = () => {
      patch.pause()
      syncTime()
    }
    const onSeeked = () => syncTime()
    const onRateChange = () => {
      patch.playbackRate = main.playbackRate
    }
    const onTimeUpdate = () => {
      if (patch.readyState >= 1) {
        const diff = Math.abs(patch.currentTime - main.currentTime)
        if (diff > 0.15) {
          patch.currentTime = main.currentTime
        }
      }
    }

    main.addEventListener('play', onPlay)
    main.addEventListener('pause', onPause)
    main.addEventListener('seeked', onSeeked)
    main.addEventListener('ratechange', onRateChange)
    main.addEventListener('timeupdate', onTimeUpdate)

    // Initial sync
    syncTime()
    if (!main.paused) {
      patch.playbackRate = main.playbackRate
      patch.play().catch(() => {})
    }

    return () => {
      main.removeEventListener('play', onPlay)
      main.removeEventListener('pause', onPause)
      main.removeEventListener('seeked', onSeeked)
      main.removeEventListener('ratechange', onRateChange)
      main.removeEventListener('timeupdate', onTimeUpdate)
    }
  }, [videoEl])

  const ox = patchBox.origX ?? patchBox.x
  const oy = patchBox.origY ?? patchBox.y
  const ow = patchBox.origW ?? patchBox.w
  const oh = patchBox.origH ?? patchBox.h
  // Backend feather reaches only a few pixels outside the original mask.
  // Showing the entire encoded padding exposes H.264 colour differences as a
  // rectangle, so crop to the actual processed area plus that feather.
  const feather = Math.max(3, Math.round(Math.min(ow, oh) * 0.14))
  const vx = Math.max(patchBox.x, ox - feather)
  const vy = Math.max(patchBox.y, oy - feather)
  const vr = Math.min(patchBox.x + patchBox.w, ox + ow + feather)
  const vb = Math.min(patchBox.y + patchBox.h, oy + oh + feather)
  const vw = Math.max(1, vr - vx)
  const vh = Math.max(1, vb - vy)
  const topEdge = vy <= crop.y
  const bottomEdge = vb >= crop.y + crop.h

  return (
    <div
      className="absolute pointer-events-none select-none z-[14] overflow-hidden"
      style={{
        left: `${((vx - crop.x) / crop.w) * 100}%`,
        top: `${((vy - crop.y) / crop.h) * 100}%`,
        width: `${(vw / crop.w) * 100}%`,
        height: `${(vh / crop.h) * 100}%`,
        maskImage: `linear-gradient(to bottom, ${topEdge ? '#000' : 'transparent'} 0%, #000 14%, #000 86%, ${bottomEdge ? '#000' : 'transparent'} 100%)`,
        WebkitMaskImage: `linear-gradient(to bottom, ${topEdge ? '#000' : 'transparent'} 0%, #000 14%, #000 86%, ${bottomEdge ? '#000' : 'transparent'} 100%)`,
      }}
    >
      <video
        ref={patchRef}
        src={patchUrl}
        className="absolute max-w-none max-h-none pointer-events-none"
        style={{
          left: `${-((vx - patchBox.x) / vw) * 100}%`,
          top: `${-((vy - patchBox.y) / vh) * 100}%`,
          width: `${(patchBox.w / vw) * 100}%`,
          height: `${(patchBox.h / vh) * 100}%`,
          objectFit: 'fill',
        }}
        muted
        playsInline
        loop
        preload="auto"
      />
    </div>
  )
}

/**
 * Fill a watermark box with live pixels sampled from the nearest clean strip
 * of the same video. This looks much closer to content-aware removal than a
 * translucent blur plate, while staying perfectly live in the editor.
 */
export function SurroundingVideoFill({
  videoEl,
  box,
  crop,
}: {
  videoEl: HTMLVideoElement | null
  box: { x: number; y: number; w: number; h: number }
  crop: { x: number; y: number; w: number; h: number }
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const video = videoEl
    const canvas = canvasRef.current
    if (!video || !canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let raf = 0
    let stopped = false

    const paint = () => {
      if (video.readyState < 2 || stopped) return
      const vw = video.videoWidth || crop.w
      const vh = video.videoHeight || crop.h
      const sx = Math.max(0, Math.min(vw - 1, box.x))
      const sw = Math.max(1, Math.min(box.w, vw - sx))
      const sh = Math.max(1, Math.min(box.h, vh))
      // Sample the closest clean strip. Top watermarks borrow from below;
      // bottom watermarks borrow from above.
      const below = box.y + box.h * 2.15 <= vh
      const sy = Math.max(0, Math.min(vh - sh, box.y + (below ? box.h * 1.12 : -box.h * 1.12)))
      const cw = Math.max(2, Math.round(sw))
      const ch = Math.max(2, Math.round(sh))
      if (canvas.width !== cw || canvas.height !== ch) {
        canvas.width = cw
        canvas.height = ch
      }
      ctx.clearRect(0, 0, cw, ch)
      ctx.save()
      ctx.filter = `blur(${Math.max(1.5, Math.min(4, ch * 0.055))}px) saturate(0.96)`
      // Overscan slightly so blur never creates a dark rim.
      const bleed = Math.max(2, Math.round(ch * 0.08))
      ctx.drawImage(video, sx, sy, sw, sh, -bleed, -bleed, cw + bleed * 2, ch + bleed * 2)
      ctx.restore()

      // Feather all four sides in canvas alpha. This avoids the rectangular
      // seam that the old patch-video and CSS-only mask exposed.
      ctx.globalCompositeOperation = 'destination-in'
      const gy = ctx.createLinearGradient(0, 0, 0, ch)
      gy.addColorStop(0, 'rgba(0,0,0,0)')
      gy.addColorStop(0.16, 'rgba(0,0,0,1)')
      gy.addColorStop(0.84, 'rgba(0,0,0,1)')
      gy.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = gy
      ctx.fillRect(0, 0, cw, ch)
      const gx = ctx.createLinearGradient(0, 0, cw, 0)
      gx.addColorStop(0, 'rgba(0,0,0,0)')
      gx.addColorStop(0.025, 'rgba(0,0,0,1)')
      gx.addColorStop(0.975, 'rgba(0,0,0,1)')
      gx.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = gx
      ctx.fillRect(0, 0, cw, ch)
      ctx.globalCompositeOperation = 'source-over'
    }

    const tick = () => {
      paint()
      if (!stopped && !video.paused) raf = requestAnimationFrame(tick)
    }
    const start = () => { cancelAnimationFrame(raf); tick() }
    video.addEventListener('play', start)
    video.addEventListener('seeked', paint)
    video.addEventListener('loadeddata', paint)
    paint()
    if (!video.paused) start()
    return () => {
      stopped = true
      cancelAnimationFrame(raf)
      video.removeEventListener('play', start)
      video.removeEventListener('seeked', paint)
      video.removeEventListener('loadeddata', paint)
    }
  }, [videoEl, box.x, box.y, box.w, box.h, crop.w, crop.h])

  return (
    <canvas ref={canvasRef} className="absolute inset-0 size-full pointer-events-none" />
  )
}
