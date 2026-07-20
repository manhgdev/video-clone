import type { LogoKeyframe, Segment, TextOverlay } from '@/features/project/project.types'

function random(seed: number) {
  let state = seed >>> 0 || 1
  return () => ((state = Math.imul(state ^ state >>> 15, 1 | state), state ^= state + Math.imul(state ^ state >>> 7, 61 | state), ((state ^ state >>> 14) >>> 0) / 4294967296))
}

function overlaps(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
}

export function generateLogoKeyframes(
  logo: TextOverlay,
  duration: number,
  frameW: number,
  frameH: number,
  segments: Segment[],
  seed = logo.positionSeed ?? Date.now(),
): LogoKeyframe[] {
  const visible = Math.max(0.5, logo.visibleSec ?? 4)
  const hidden = Math.max(0, logo.hiddenSec ?? 2)
  const step = visible + hidden
  const margin = Math.max(4, Math.min(frameW, frameH) * ((logo.safeMargin ?? 4) / 100))
  const rng = random(seed)
  const out: LogoKeyframe[] = []
  for (let at = logo.start; at < Math.min(duration, logo.end) + 1e-6; at += step) {
    const blocks = segments.filter((s) => at >= s.start && at < s.end).flatMap((s) => {
      const b = s.bbox
      if (b) return [b]
      const cl = s.captionLayout
      return cl
        ? [{ x: cl.x, y: cl.y, w: cl.w, h: cl.h }]
        : (s.translation || '').trim()
          ? [{ x: frameW * .05, y: frameH * .78, w: frameW * .9, h: frameH * .18 }]
          : []
    })
    let point = { x: margin, y: margin }
    for (let tries = 0; tries < 24; tries += 1) {
      point = {
        x: margin + rng() * Math.max(0, frameW - logo.w - margin * 2),
        y: margin + rng() * Math.max(0, frameH - logo.h - margin * 2),
      }
      if (!blocks.some((b) => overlaps({ ...point, w: logo.w, h: logo.h }, b))) break
    }
    out.push({ at: Math.round(at * 1000) / 1000, x: Math.round(point.x), y: Math.round(point.y) })
  }
  return out.length ? out : [{ at: logo.start, x: logo.x, y: logo.y }]
}

export function logoFrame(logo: TextOverlay, time: number) {
  if (time < logo.start || time >= logo.end) return { x: logo.x, y: logo.y, opacity: 0 }
  if (logo.motion !== 'random') return { x: logo.x, y: logo.y, opacity: (logo.opacity ?? 85) / 100 }
  const frames = logo.positionKeyframes?.length ? logo.positionKeyframes : [{ at: logo.start, x: logo.x, y: logo.y }]
  let frame = frames[0]
  for (const item of frames) {
    if (item.at <= time) frame = item
    else break
  }
  const local = time - frame.at
  const visible = Math.max(0.5, logo.visibleSec ?? 4)
  const fade = Math.min(Math.max(0, logo.fadeSec ?? 0.5), visible / 2)
  let alpha = local < visible ? 1 : 0
  if (fade > 0 && local < fade) alpha = local / fade
  if (fade > 0 && local > visible - fade && local < visible) alpha = (visible - local) / fade
  return { x: frame.x, y: frame.y, opacity: alpha * (logo.opacity ?? 85) / 100 }
}

export function __checkLogoMotion() {
  const logo = { id: 'x', start: 0, end: 20, text: 'L', x: 0, y: 0, w: 100, h: 40, fontSize: 24, color: '#fff', kind: 'logo', motion: 'random', visibleSec: 4, hiddenSec: 2, fadeSec: .5 } as TextOverlay
  const frames = generateLogoKeyframes(logo, 20, 1000, 600, [], 7)
  if (frames.length !== 4 || logoFrame({ ...logo, positionKeyframes: frames }, 4.5).opacity !== 0) throw new Error('logo motion self-check failed')
}
