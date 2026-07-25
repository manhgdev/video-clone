import type React from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import { cn } from '@/shared/lib/cn'

export function formatTimecode(value: number) {
  const h = Math.floor(value / 3600)
  const m = Math.floor((value % 3600) / 60)
  const s = Math.floor(value % 60)
  const f = Math.floor((value % 1) * 30)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(f)}`
}

/** Parse HH:MM:SS:FF | MM:SS:FF | số giây thuần. */
export function parseTimecode(raw: string): number | null {
  const t = raw.trim().replace(',', '.')
  if (!t) return null
  if (/^-?\d+(\.\d+)?$/.test(t)) {
    const n = Number(t)
    return Number.isFinite(n) ? n : null
  }
  const parts = t.split(':').map((p) => p.trim())
  if (parts.length < 2 || parts.length > 4) return null
  const nums = parts.map((p) => Number(p))
  if (nums.some((n) => !Number.isFinite(n))) return null
  let h = 0
  let m = 0
  let s = 0
  let f = 0
  if (parts.length === 4) {
    ;[h, m, s, f] = nums
  } else if (parts.length === 3) {
    // MM:SS:FF (timeline ngắn) hoặc HH:MM:SS
    if (nums[2] >= 30 && nums[2] % 1 === 0 && nums[1] < 60) {
      ;[h, m, s] = nums
    } else {
      ;[m, s, f] = nums
    }
  } else {
    ;[m, s] = nums
  }
  return Math.max(0, h * 3600 + m * 60 + s + f / 30)
}

export function parseHexColor(hex: string): [number, number, number] {
  const h = (hex || '#4c1d95').replace('#', '')
  if (h.length !== 6) return [76, 29, 149]
  const n = (i: number) => parseInt(h.slice(i, i + 2), 16)
  return [Number.isNaN(n(0)) ? 76 : n(0), Number.isNaN(n(2)) ? 29 : n(2), Number.isNaN(n(4)) ? 149 : n(4)]
}

/** Preview mask «Làm mờ» — kính CapCut (blur + tint mỏng); xuất pad-blur khớp. */
export function coverMaskPreviewStyle(
  style: ProjectSettings['coverMaskStyle'],
  color: string,
  opacity: number,
): React.CSSProperties {
  const [r, g, b] = parseHexColor(color)
  const pct = Math.max(0, Math.min(100, opacity))
  const a = Math.max(0.05, Math.min(1, pct / 100))
  if (style === 'solid') {
    return { backgroundColor: `rgba(${r},${g},${b},${a})` }
  }
  if (style === 'mosaic') {
    return {
      backgroundColor: 'rgba(42,42,48,0.72)',
      backdropFilter: 'blur(22px) saturate(0.4) contrast(0.92) brightness(0.92)',
      WebkitBackdropFilter: 'blur(22px) saturate(0.4) contrast(0.92) brightness(0.92)',
      // isolation giúp backdrop-filter không bị layer text che
      isolation: 'isolate' as const,
    }
  }
  // Làm mờ CapCut: blur phía sau + tint mỏng (bản đẹp — không đậm thêm)
  const tintA = Math.min(0.22, Math.max(0.06, a * 0.28))
  const blurPx = Math.round(22 + a * 20) // ~22–42px
  return {
    backgroundColor: `rgba(${r},${g},${b},${tintA})`,
    backdropFilter: `blur(${blurPx}px) saturate(0.88)`,
    WebkitBackdropFilter: `blur(${blurPx}px) saturate(0.88)`,
    isolation: 'isolate' as const,
  }
}

export type PixelBox = { x: number; y: number; w: number; h: number }
export type CropRect = { x: number; y: number; w: number; h: number }

export const COVER_MASK_STYLES: { id: ProjectSettings['coverMaskStyle']; label: string }[] = [
  { id: 'blur', label: 'Làm mờ' },
  { id: 'solid', label: 'Màu nền' },
  { id: 'mosaic', label: 'Khối' },
]

export const CAPTION_FONT_PRESETS: { id: string; label: string; css: string }[] = [
  { id: 'system', label: 'Noto Sans', css: '"VC Noto Sans", sans-serif' },
  { id: 'segoe', label: 'Inter', css: '"VC Inter", sans-serif' },
  { id: 'arial', label: 'Arimo', css: '"VC Arimo", sans-serif' },
  { id: 'bold', label: 'Archivo Black', css: '"VC Archivo Black", sans-serif' },
  { id: 'helvetica', label: 'Roboto', css: '"VC Roboto", sans-serif' },
  { id: 'verdana', label: 'Open Sans', css: '"VC Open Sans", sans-serif' },
  { id: 'tahoma', label: 'Carlito', css: '"VC Carlito", sans-serif' },
  { id: 'trebuchet', label: 'Fira Sans', css: '"VC Fira Sans", sans-serif' },
  { id: 'rounded', label: 'Nunito / tròn', css: '"VC Nunito", sans-serif' },
  { id: 'impact', label: 'Anton', css: '"VC Anton", sans-serif' },
  { id: 'georgia', label: 'Merriweather', css: '"VC Merriweather", serif' },
  { id: 'times', label: 'Tinos', css: '"VC Tinos", serif' },
  { id: 'palatino', label: 'Literata', css: '"VC Literata", serif' },
  { id: 'garamond', label: 'EB Garamond', css: '"VC EB Garamond", serif' },
  { id: 'courier', label: 'Cousine', css: '"VC Cousine", monospace' },
  { id: 'mono', label: 'Noto Sans Mono', css: '"VC Noto Sans Mono", monospace' },
  { id: 'comic', label: 'Patrick Hand', css: '"VC Patrick Hand", cursive' },
  { id: 'cjk', label: 'Noto Sans SC', css: '"VC Noto Sans SC", sans-serif' },
  { id: 'meiryo', label: 'Noto Sans JP', css: '"VC Noto Sans JP", sans-serif' },
  { id: 'malgun', label: 'Noto Sans KR', css: '"VC Noto Sans KR", sans-serif' },
]

export const CAPTION_COLORS = [
  '#ffffff', '#f8fafc', '#e2e8f0', '#000000', '#1e293b',
  '#ffd166', '#f59e0b', '#ef476f', '#e11d48',
  '#06d6a0', '#10b981', '#118ab2', '#3b82f6', '#8b5cf6',
] as const

export function captionFontCss(family?: string): string {
  return CAPTION_FONT_PRESETS.find((f) => f.id === family)?.css
    ?? CAPTION_FONT_PRESETS[0].css
}

/**
 * Style chữ phụ đề.
 * Mặc định = bản đẹp cũ: trắng + soft drop-shadow (không stroke dày, không nền).
 * Chỉ bật nền/viền nặng khi user chọn trong panel.
 */
export function captionChromeStyle(
  settings: ProjectSettings,
  segment?: Pick<Segment, 'fontFamily' | 'textColor'>,
): React.CSSProperties {
  const color = segment?.textColor || settings.captionTextColor || '#ffffff'
  const bg = settings.captionBgStyle || 'none'
  const customColor = color.toLowerCase() !== '#ffffff'
  const family = segment?.fontFamily || settings.subtitleFontFamily || 'system'
  const style: React.CSSProperties = {
    color,
    fontFamily: captionFontCss(family),
    // Soft shadow CapCut — khớp class drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)]
    textShadow:
      settings.captionStroke === false
        ? 'none'
        : '0 2px 4px rgba(0,0,0,0.9)',
  }
  if (bg === 'solid' || bg === 'box' || bg === 'blur') {
    const bgColor = settings.captionBgColor || '#000000'
    const op = Math.max(0, Math.min(100, settings.captionBgOpacity ?? 55)) / 100
    const [r, g, b] = parseHexColor(bgColor)
    if (bg === 'solid') {
      style.backgroundColor = `rgba(${r},${g},${b},${Math.max(0.2, op)})`
      style.borderRadius = 4
      style.padding = '0.12em 0.28em'
    } else if (bg === 'box') {
      style.backgroundColor = `rgba(${r},${g},${b},${Math.max(0.35, op)})`
      style.borderRadius = 6
      style.padding = '0.18em 0.4em'
      style.border = '1px solid rgba(255,255,255,0.12)'
    } else {
      style.backgroundColor = `rgba(${r},${g},${b},${Math.max(0.15, op * 0.55)})`
      style.backdropFilter = 'blur(10px) saturate(0.9)'
      style.WebkitBackdropFilter = 'blur(10px) saturate(0.9)'
      style.borderRadius = 6
      style.padding = '0.14em 0.32em'
    }
  }
  // Không WebkitTextStroke — làm chữ «cứng» xấu hơn bản drop-shadow
  void customColor
  return style
}

/** Preset hiệu ứng kéo vào video (tab Effects) */
export const EFFECT_PRESETS: {
  id: string
  label: string
  desc: string
  maskStyle: 'blur' | 'solid' | 'mosaic'
  maskColor: string
  maskOpacity: number
}[] = [
  { id: 'blur', label: 'Làm mờ', desc: 'Kính mờ CapCut — che vùng tự do', maskStyle: 'blur', maskColor: '#4c1d95', maskOpacity: 45 },
  { id: 'solid', label: 'Màu nền', desc: 'Phủ màu đặc lên vùng chọn', maskStyle: 'solid', maskColor: '#1e1b4b', maskOpacity: 70 },
  { id: 'mosaic', label: 'Khối', desc: 'Làm mờ pixel / che hardsub', maskStyle: 'mosaic', maskColor: '#2a2a30', maskOpacity: 80 },
]

export type AspectPreset =
  | { id: 'original' | 'custom'; label: string; disabled?: boolean }
  | { id: string; label: string; w: number; h: number; orient: 'landscape' | 'portrait' | 'square' }

export const ASPECT_PRESETS: AspectPreset[] = [
  { id: 'original', label: 'Gốc (không cắt)' },
  { id: 'custom', label: 'Cắt tự do' },
  { id: '16:9', label: '16:9', w: 16, h: 9, orient: 'landscape' },
  { id: '4:3', label: '4:3', w: 4, h: 3, orient: 'landscape' },
  { id: '2.35:1', label: '2.35:1', w: 235, h: 100, orient: 'landscape' },
  { id: '2:1', label: '2:1', w: 2, h: 1, orient: 'landscape' },
  { id: '1.85:1', label: '1.85:1', w: 185, h: 100, orient: 'landscape' },
  { id: '9:16', label: '9:16', w: 9, h: 16, orient: 'portrait' },
  { id: '3:4', label: '3:4', w: 3, h: 4, orient: 'portrait' },
  { id: '58inch', label: '5.8-inch', w: 108, h: 234, orient: 'portrait' },
  { id: '1:1', label: '1:1', w: 1, h: 1, orient: 'square' },
]

/** Cửa sổ crop chuẩn hóa (0–1) theo tỷ lệ — full chiều hẹp, cắt chiều rộng. */
export function aspectWindowNorm(
  sourceW: number,
  sourceH: number,
  presetId: string,
): { w: number; h: number } | null {
  if (sourceW <= 0 || sourceH <= 0) return null
  if (!presetId || presetId === 'original' || presetId === 'custom') return null
  const preset = ASPECT_PRESETS.find((p) => p.id === presetId && 'w' in p) as
    | Extract<AspectPreset, { w: number }>
    | undefined
  if (!preset) return null
  const target = preset.w / preset.h
  const source = sourceW / sourceH
  if (source >= target) {
    // source rộng hơn → full height, crop ngang
    const w = (sourceH * target) / sourceW
    return { w: Math.min(1, w), h: 1 }
  }
  // source cao hơn → full width, crop dọc
  const h = sourceW / target / sourceH
  return { w: 1, h: Math.min(1, h) }
}

/** Crop mặc định giữa khung (normalized). */
export function centeredAspectCrop(
  sourceW: number,
  sourceH: number,
  presetId: string,
): { x: number; y: number; w: number; h: number } | null {
  const win = aspectWindowNorm(sourceW, sourceH, presetId)
  if (!win) return null
  return {
    x: Math.max(0, (1 - win.w) / 2),
    y: Math.max(0, (1 - win.h) / 2),
    w: win.w,
    h: win.h,
  }
}

export function resolveCropRect(
  sourceW: number,
  sourceH: number,
  presetId: string,
  custom?: { x: number; y: number; w: number; h: number } | null,
): CropRect {
  if (sourceW <= 0 || sourceH <= 0) return { x: 0, y: 0, w: 1, h: 1 }
  // Cắt tự do: dùng đủ x,y,w,h
  if (presetId === 'custom' && custom) {
    const x = Math.max(0, Math.min(0.95, custom.x))
    const y = Math.max(0, Math.min(0.95, custom.y))
    const w = Math.max(0.05, Math.min(1 - x, custom.w))
    const h = Math.max(0.05, Math.min(1 - y, custom.h))
    return { x: x * sourceW, y: y * sourceH, w: w * sourceW, h: h * sourceH }
  }
  if (!presetId || presetId === 'original' || presetId === 'custom') {
    return { x: 0, y: 0, w: sourceW, h: sourceH }
  }
  const win = aspectWindowNorm(sourceW, sourceH, presetId)
  if (!win) return { x: 0, y: 0, w: sourceW, h: sourceH }
  // Preset cố định tỷ lệ: w/h khóa theo aspect; x/y từ previewCrop (kéo pan) hoặc giữa
  let nx: number
  let ny: number
  if (custom && Number.isFinite(custom.x) && Number.isFinite(custom.y)) {
    nx = Math.max(0, Math.min(1 - win.w, custom.x))
    ny = Math.max(0, Math.min(1 - win.h, custom.y))
  } else {
    nx = (1 - win.w) / 2
    ny = (1 - win.h) / 2
  }
  return {
    x: nx * sourceW,
    y: ny * sourceH,
    w: win.w * sourceW,
    h: win.h * sourceH,
  }
}

export function sourceToDisplayStyle(
  box: { x: number; y: number; w: number; h: number },
  crop: CropRect,
): React.CSSProperties {
  return {
    left: `${((box.x - crop.x) / crop.w) * 100}%`,
    top: `${((box.y - crop.y) / crop.h) * 100}%`,
    width: `${(box.w / crop.w) * 100}%`,
    height: `${(box.h / crop.h) * 100}%`,
  }
}

export function videoCropStyle(sourceW: number, sourceH: number, crop: CropRect): React.CSSProperties {
  return {
    width: `${(sourceW / crop.w) * 100}%`,
    height: `${(sourceH / crop.h) * 100}%`,
    left: `${(-crop.x / crop.w) * 100}%`,
    top: `${(-crop.y / crop.h) * 100}%`,
    objectFit: 'fill',
  }
}

export function AspectIcon({ orient }: { orient: 'landscape' | 'portrait' | 'square' }) {
  const cls = 'border border-current rounded-[2px] opacity-70'
  if (orient === 'portrait') return <span className={cn(cls, 'inline-block h-3.5 w-2')} aria-hidden />
  if (orient === 'square') return <span className={cn(cls, 'inline-block size-2.5')} aria-hidden />
  return <span className={cn(cls, 'inline-block h-2 w-3.5')} aria-hidden />
}
