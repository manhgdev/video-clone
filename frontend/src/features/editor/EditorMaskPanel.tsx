import { useEffect, useState } from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import { localize, useLocale } from '@/app/i18n'
import { cn } from '@/shared/lib/cn'
import {
  COVER_MASK_SHAPES,
  COVER_MASK_STYLES,
  type CoverMaskShape,
  NumField,
  formatTimecode,
  parseTimecode,
  type PixelBox,
} from '@/features/editor/lib'

export type CoverApplyRange = { mode: 'full' } | { mode: 'range'; fromSec: number; toSec: number }

type Props = {
  busy: boolean
  settings: ProjectSettings
  onSettings: (next: ProjectSettings) => void
  coverMaskStyle: string
  coverMaskColor: string
  coverMaskOpacity: number
  selected: Segment | null | undefined
  bboxSeg: Segment | null | undefined
  selectedBox: PixelBox | null
  sourceWidth: number
  sourceHeight: number
  segmentsLen: number
  /** Timeline duration in seconds */
  timelineDuration?: number
  /** Current playhead in seconds */
  playheadSec?: number
  commitCoverBox: (patch: Partial<PixelBox>) => void
  stretchCoverFullWidth: () => void
  applyCoverMaskToAll: (range?: CoverApplyRange) => void
  /** Reset bbox: one = selected clip; all = entire project */
  resetOcrRegion: (scope: 'one' | 'all') => void
  applyAllLaneLabel?: string
}

export function EditorMaskPanel({
  busy,
  settings,
  onSettings,
  coverMaskStyle,
  coverMaskColor,
  coverMaskOpacity,
  selected,
  bboxSeg,
  selectedBox,
  sourceWidth,
  sourceHeight,
  segmentsLen,
  timelineDuration = 0,
  playheadSec = 0,
  commitCoverBox,
  stretchCoverFullWidth,
  applyCoverMaskToAll,
  resetOcrRegion,
  applyAllLaneLabel = 'lane',
}: Props) {
  const { locale } = useLocale()
  const dur = Math.max(0, timelineDuration)
  const [topTab, setTopTab] = useState<'basic' | 'mask'>('mask')
  const [activeShape, setActiveShape] = useState<CoverMaskShape>('rectangle')
  const [maskFeather, setMaskFeather] = useState(20)
  const [maskRadius, setMaskRadius] = useState(4)
  const [applyMode, setApplyMode] = useState<'full' | 'range'>('full')
  const [fromSec, setFromSec] = useState(0)
  const [toSec, setToSec] = useState(0)

  useEffect(() => {
    if (dur <= 0) return
    setToSec((t) => (t <= 0 || t > dur ? Math.round(dur * 100) / 100 : t))
  }, [dur])

  useEffect(() => {
    if (applyMode !== 'range' || dur <= 0) return
    const ph = Math.max(0, Math.min(dur, playheadSec))
    setFromSec(Math.round(Math.max(0, ph - 2) * 100) / 100)
    setToSec(Math.round(Math.min(dur, ph + 8) * 100) / 100)
  }, [applyMode])

  function runApply() {
    if (applyMode === 'full') {
      applyCoverMaskToAll({ mode: 'full' })
      return
    }
    const a = Math.max(0, Math.min(fromSec, toSec))
    const b = Math.max(fromSec, toSec, a + 0.05)
    applyCoverMaskToAll({
      mode: 'range',
      fromSec: Math.round(a * 100) / 100,
      toSec: Math.round(Math.min(dur > 0 ? dur : b, b) * 100) / 100,
    })
  }

  function handleSelectShape(shapeId: CoverMaskShape) {
    setActiveShape(shapeId)
    if (shapeId === 'horizontal' || shapeId === 'text') {
      stretchCoverFullWidth()
    }
  }

  return (
    <div className="space-y-3">
      {/* Top Tabs: Cơ bản / Mặt nạ (CapCut PC style) */}
      <div className="grid grid-cols-2 p-0.5 rounded-lg bg-muted/60 border border-border/80 text-xs">
        <button
          type="button"
          className={cn(
            'py-1.5 font-medium rounded-md transition-all text-center',
            topTab === 'basic'
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          )}
          onClick={() => setTopTab('basic')}
        >
          {localize(locale, 'Cơ bản', 'Basic')}
        </button>
        <button
          type="button"
          className={cn(
            'py-1.5 font-medium rounded-md transition-all text-center',
            topTab === 'mask'
              ? 'bg-background text-cyan-400 shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          )}
          onClick={() => setTopTab('mask')}
        >
          {localize(locale, 'Mặt nạ', 'Mask')}
        </button>
      </div>

      {topTab === 'basic' ? (
        <div className="space-y-2 py-1 text-xs text-muted-foreground">
          <p className="leading-relaxed">
            {localize(
              locale,
              'Kéo thả vùng chọn trực tiếp trên video để định vị. Bấm sang tab "Mặt nạ" để tùy biến hiệu ứng làm mờ chuẩn CapCut.',
              'Drag the bounding box directly on video preview to position. Switch to "Mask" tab to customize CapCut blur effects.',
            )}
          </p>
        </div>
      ) : (
        <>
          {/* Header Mặt nạ checkbox + active badge chip */}
          <div className="flex items-center justify-between pt-0.5">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={true}
                readOnly
                className="w-3.5 h-3.5 rounded border-border text-cyan-500 accent-cyan-500"
              />
              <span className="text-xs font-semibold text-foreground flex items-center gap-1">
                {localize(locale, 'Mặt nạ', 'Mask')}
                <span className="text-[10px] text-cyan-400 font-normal">💎</span>
              </span>
            </label>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-medium">
                {localize(locale, `Mặt nạ: ${COVER_MASK_SHAPES.find((s) => s.id === activeShape)?.labelVi || 'Hình chữ nhật'}`, `Mask: ${COVER_MASK_SHAPES.find((s) => s.id === activeShape)?.labelEn || 'Rectangle'}`)}
              </span>
            </div>
          </div>

          {/* CapCut Mask Shape Icons Grid */}
          <div className="grid grid-cols-4 gap-1.5 pt-1">
            {COVER_MASK_SHAPES.map(({ id, labelVi, labelEn }) => {
              const isSelected = activeShape === id
              return (
                <button
                  key={id}
                  type="button"
                  disabled={busy}
                  className={cn(
                    'relative flex flex-col items-center justify-center p-2 rounded-lg border transition-all group',
                    isSelected
                      ? 'border-cyan-400 bg-cyan-500/15 text-cyan-200 shadow-[0_0_8px_rgba(34,211,238,0.2)]'
                      : 'border-border/70 bg-card hover:bg-accent text-muted-foreground hover:text-foreground',
                  )}
                  onClick={() => handleSelectShape(id)}
                >
                  <div className="w-8 h-8 flex items-center justify-center mb-1">
                    {id === 'split' && (
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="3 3" />
                        <line x1="3" y1="12" x2="21" y2="12" strokeWidth="2" />
                      </svg>
                    )}
                    {id === 'horizontal' && (
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="2 2" />
                        <line x1="3" y1="8" x2="21" y2="8" strokeWidth="1.8" />
                        <line x1="3" y1="16" x2="21" y2="16" strokeWidth="1.8" />
                      </svg>
                    )}
                    {id === 'circle' && (
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="2 2" />
                        <circle cx="12" cy="12" r="6" strokeWidth="1.8" />
                      </svg>
                    )}
                    {id === 'rectangle' && (
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="2 2" />
                        <rect x="6" y="6" width="12" height="12" rx="1.5" strokeWidth="1.8" />
                      </svg>
                    )}
                    {id === 'text' && (
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="2 2" />
                        <path d="M7 8h10M12 8v10M9 18h6" strokeWidth="1.8" />
                      </svg>
                    )}
                    {id === 'brush' && (
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <path d="m14 4 6 6M4 20l6-2 10-10-4-4L6 14l-2 6z" strokeWidth="1.8" />
                      </svg>
                    )}
                    {id === 'pen' && (
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <path d="M12 19l7-7 3 3-7 7-3-3zM18 13l-1.5-7.5L2 2l3.5 14.5L13 18" strokeWidth="1.8" />
                        <circle cx="11" cy="11" r="1.5" fill="currentColor" />
                      </svg>
                    )}
                  </div>
                  <span className="text-[10px] font-medium leading-tight">
                    {localize(locale, labelVi, labelEn)}
                  </span>
                  {isSelected && (
                    <span className="absolute bottom-1 right-1 w-1.5 h-1.5 rounded-full bg-cyan-400" />
                  )}
                </button>
              )
            })}
          </div>

          {/* Tùy chọn cài đặt mặt nạ (CapCut settings accordion) */}
          <div className="border-t border-border/80 pt-2.5 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                <span>⚡</span>
                {localize(locale, 'Tùy chọn cài đặt mặt nạ', 'Mask Options')}
              </span>
            </div>

            {/* Kiểu hiệu ứng: Làm mờ / Màu nền / Khối */}
            <div className="space-y-1">
              <span className="text-[11px] text-muted-foreground font-medium">
                {localize(locale, 'Kiểu hiệu ứng', 'Effect Style')}
              </span>
              <div className="grid grid-cols-3 gap-1">
                {COVER_MASK_STYLES.map(({ id, label }) => (
                  <button
                    key={id}
                    type="button"
                    disabled={busy}
                    className={cn(
                      'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                      coverMaskStyle === id
                        ? 'border-cyan-400 bg-cyan-500/20 text-cyan-200 font-medium'
                        : 'border-border bg-accent hover:bg-muted text-muted-foreground',
                    )}
                    onClick={() => onSettings({ ...settings, coverMaskStyle: id })}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Độ mờ viền (Feather) & Bo góc (Radius) Sliders */}
            <div className="space-y-2 bg-muted/40 p-2.5 rounded-lg border border-border/60">
              <div className="space-y-1">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-muted-foreground font-medium">
                    {localize(locale, 'Độ mờ viền (Feather)', 'Feather / Softness')}
                  </span>
                  <span className="text-cyan-400 tabular-nums font-medium">{maskFeather} px</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={50}
                  step={1}
                  className="w-full accent-cyan-400"
                  value={maskFeather}
                  disabled={busy}
                  onChange={(e) => setMaskFeather(Number(e.target.value))}
                />
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-muted-foreground font-medium">
                    {localize(locale, 'Bo góc (Radius)', 'Corner Radius')}
                  </span>
                  <span className="text-cyan-400 tabular-nums font-medium">{maskRadius} px</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={40}
                  step={1}
                  className="w-full accent-cyan-400"
                  value={maskRadius}
                  disabled={busy}
                  onChange={(e) => setMaskRadius(Number(e.target.value))}
                />
              </div>
            </div>

            {/* Màu phủ & Độ đậm */}
            {coverMaskStyle !== 'mosaic' && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-muted-foreground font-medium">
                    {localize(locale, 'Màu phủ & Độ đậm', 'Tint Color & Opacity')}
                  </span>
                  <span className="text-muted-foreground tabular-nums">{coverMaskOpacity}%</span>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    className="h-8 w-10 shrink-0 cursor-pointer rounded-md border border-border bg-input p-0.5"
                    value={coverMaskColor}
                    disabled={busy}
                    title={localize(locale, 'Màu phủ', 'Color')}
                    onChange={(e) => onSettings({ ...settings, coverMaskColor: e.target.value })}
                  />
                  <input
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    className="min-w-0 flex-1 accent-cyan-400"
                    value={coverMaskOpacity}
                    disabled={busy}
                    title={`Độ đậm ${coverMaskOpacity}%`}
                    onChange={(e) => onSettings({ ...settings, coverMaskOpacity: Number(e.target.value) })}
                  />
                </div>
              </div>
            )}

            {/* Vị trí X, Y & Kích thước Rộng, Cao */}
            {(selected || bboxSeg) && selectedBox ? (
              <div className="space-y-2 pt-1">
                <div className="grid grid-cols-4 gap-1.5">
                  <NumField
                    inline
                    label="X"
                    value={selectedBox.x}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        x: Math.round(Math.max(0, Math.min(sourceWidth - selectedBox.w, v))),
                      })
                    }
                  />
                  <NumField
                    inline
                    label="Y"
                    value={selectedBox.y}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        y: Math.round(Math.max(0, Math.min(sourceHeight - selectedBox.h, v))),
                      })
                    }
                  />
                  <NumField
                    inline
                    label={localize(locale, 'Rộng', 'W')}
                    value={selectedBox.w}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        w: Math.round(Math.max(12, Math.min(sourceWidth - selectedBox.x, v))),
                      })
                    }
                  />
                  <NumField
                    inline
                    label={localize(locale, 'Cao', 'H')}
                    value={selectedBox.h}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        h: Math.round(Math.max(12, Math.min(sourceHeight - selectedBox.y, v))),
                      })
                    }
                  />
                </div>

                <div className="flex items-center gap-2 pt-0.5">
                  <button
                    type="button"
                    className="flex-1 rounded-md border border-cyan-500/40 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-200 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
                    disabled={busy || !selected || sourceWidth <= 0}
                    title={localize(locale, 'Kéo ngang phủ kín toàn bộ 100% video', 'Stretch full width 100%')}
                    onClick={stretchCoverFullWidth}
                  >
                    {localize(locale, 'Kéo Full Ngang', 'Full Width Banner')}
                  </button>
                </div>

                {/* Apply Range Section */}
                <div className="border-t border-border pt-2 space-y-2">
                  <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                    {localize(locale, `Áp vị trí che · lane «${applyAllLaneLabel}»`, `Apply mask position · lane «${applyAllLaneLabel}»`)}
                  </p>
                  <div className="grid grid-cols-2 gap-1">
                    <button
                      type="button"
                      disabled={busy}
                      className={cn(
                        'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                        applyMode === 'full'
                          ? 'border-cyan-400 bg-cyan-500/20 text-cyan-200 font-medium'
                          : 'border-border bg-accent hover:bg-muted text-muted-foreground',
                      )}
                      onClick={() => setApplyMode('full')}
                    >
                      {localize(locale, 'Full video', 'Full video')}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      className={cn(
                        'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                        applyMode === 'range'
                          ? 'border-cyan-400 bg-cyan-500/20 text-cyan-200 font-medium'
                          : 'border-border bg-accent hover:bg-muted text-muted-foreground',
                      )}
                      onClick={() => setApplyMode('range')}
                    >
                      {localize(locale, 'Từ → đến', 'Time Range')}
                    </button>
                  </div>
                  {applyMode === 'range' && (
                    <div className="grid grid-cols-2 gap-2">
                      <NumField
                        label={localize(locale, 'Từ', 'From')}
                        value={fromSec}
                        step={0.1}
                        disabled={busy}
                        onCommit={(v) => setFromSec(Math.max(0, v))}
                        formatDisplay={formatTimecode}
                        parseDisplay={parseTimecode}
                      />
                      <NumField
                        label={localize(locale, 'Đến', 'To')}
                        value={toSec}
                        step={0.1}
                        disabled={busy}
                        onCommit={(v) => setToSec(Math.max(0, v))}
                        formatDisplay={formatTimecode}
                        parseDisplay={parseTimecode}
                      />
                    </div>
                  )}
                  <button
                    type="button"
                    className="w-full rounded-md border border-cyan-400/60 bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-200 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
                    disabled={busy || !(selected || bboxSeg) || segmentsLen === 0}
                    onClick={runApply}
                  >
                    {applyMode === 'full'
                      ? localize(locale, `Áp vị trí (Y) · full · ${applyAllLaneLabel}`, `Apply Y · Full · ${applyAllLaneLabel}`)
                      : localize(locale, `Áp Y · mọi bbox · ${formatTimecode(Math.min(fromSec, toSec))} → ${formatTimecode(Math.max(fromSec, toSec))}`, `Apply Y · ${formatTimecode(Math.min(fromSec, toSec))} → ${formatTimecode(Math.max(fromSec, toSec))}`)}
                  </button>
                </div>
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground pt-1">
                {localize(
                  locale,
                  'Chưa có vùng che tại playhead — chọn đoạn caption hoặc tua tới chỗ có chữ.',
                  'No mask region at playhead — select a caption segment or seek to subtitle.',
                )}
              </p>
            )}

            {/* Reset bbox */}
            <div className="border-t border-border pt-2 space-y-1.5">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                {localize(locale, 'Đặt lại vùng che (Reset)', 'Reset Mask')}
              </p>
              <div className="grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  className="rounded-md border border-border bg-accent hover:bg-muted px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50"
                  disabled={busy || !(selected || bboxSeg)}
                  onClick={() => resetOcrRegion('one')}
                >
                  {localize(locale, 'Reset 1 clip', 'Reset current')}
                </button>
                <button
                  type="button"
                  className="rounded-md border border-cyan-400/50 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-200 px-2 py-1.5 text-[11px] font-medium transition-colors disabled:opacity-50"
                  disabled={busy || segmentsLen === 0}
                  onClick={() => resetOcrRegion('all')}
                >
                  {localize(locale, 'Reset tất cả', 'Reset all')}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

