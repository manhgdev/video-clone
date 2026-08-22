import { useEffect, useState } from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import { localize, useLocale } from '@/app/i18n'
import { cn } from '@/shared/lib/cn'
import {
  COVER_MASK_STYLES,
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

  const maskBox = (selected || bboxSeg) && selectedBox ? selectedBox : null
  const hasMask = Boolean(maskBox)
  const styleLabel = (id: string) => {
    if (id === 'blur') return localize(locale, 'Làm mờ', 'Blur')
    if (id === 'solid') return localize(locale, 'Màu nền', 'Solid')
    if (id === 'mosaic') return localize(locale, 'Khối', 'Mosaic')
    return id
  }

  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-lg border border-border bg-card p-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-foreground">
              {localize(locale, 'Vị trí vùng che', 'Mask position')}
            </h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {hasMask
                ? localize(locale, 'Kéo khung trên video hoặc nhập chính xác các giá trị bên dưới.', 'Drag the frame on video or enter precise values below.')
                : localize(locale, 'Chọn đoạn có chữ hoặc tua đến vị trí cần che để bắt đầu.', 'Select a segment with text or seek to the text you want to cover.')}
            </p>
          </div>
          {hasMask && <span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary">{localize(locale, 'Đã chọn', 'Selected')}</span>}
        </div>
        {maskBox ? (
          <div className="space-y-3">
                <div className="grid grid-cols-4 gap-1.5">
                  <NumField
                    inline
                    label="X"
                    value={maskBox.x}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        x: Math.round(Math.max(0, Math.min(sourceWidth - maskBox.w, v))),
                      })
                    }
                  />
                  <NumField
                    inline
                    label="Y"
                    value={maskBox.y}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        y: Math.round(Math.max(0, Math.min(sourceHeight - maskBox.h, v))),
                      })
                    }
                  />
                  <NumField
                    inline
                    label={localize(locale, 'Rộng', 'W')}
                    value={maskBox.w}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        w: Math.round(Math.max(12, Math.min(sourceWidth - maskBox.x, v))),
                      })
                    }
                  />
                  <NumField
                    inline
                    label={localize(locale, 'Cao', 'H')}
                    value={maskBox.h}
                    disabled={busy || !selected}
                    onCommit={(v) =>
                      commitCoverBox({
                        h: Math.round(Math.max(12, Math.min(sourceHeight - maskBox.y, v))),
                      })
                    }
                  />
                </div>

                <div className="flex items-center gap-2 pt-0.5">
                  <button
                    type="button"
                    className="flex-1 rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-xs font-medium text-primary transition-colors hover:bg-primary/15 disabled:opacity-50"
                    disabled={busy || !selected || sourceWidth <= 0}
                    title={localize(locale, 'Mở rộng vùng che theo toàn bộ chiều ngang video', 'Stretch the mask across the entire video width')}
                    onClick={stretchCoverFullWidth}
                  >
                    {localize(locale, 'Phủ toàn chiều ngang', 'Fill full width')}
                  </button>
                </div>
          </div>
        ) : null}
      </section>

      <section className="space-y-3 rounded-lg border border-border bg-card p-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{localize(locale, 'Kiểu che', 'Mask style')}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{localize(locale, 'Chọn cách xử lý cho vùng chữ gốc.', 'Choose how the original text area is treated.')}</p>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {COVER_MASK_STYLES.map(({ id }) => (
            <button
              key={id}
              type="button"
              disabled={busy}
              className={cn(
                'rounded-md border px-2 py-2 text-xs font-medium transition-colors disabled:opacity-50',
                coverMaskStyle === id
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
              onClick={() => onSettings({ ...settings, coverMaskStyle: id })}
            >
              {styleLabel(id)}
            </button>
          ))}
        </div>
        {coverMaskStyle !== 'mosaic' && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <label className="font-medium text-foreground">{localize(locale, 'Màu phủ và độ đậm', 'Tint and opacity')}</label>
              <span className="tabular-nums text-muted-foreground">{coverMaskOpacity}%</span>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="color"
                className="h-9 w-11 shrink-0 cursor-pointer rounded-md border border-border bg-input p-1"
                value={coverMaskColor}
                disabled={busy}
                aria-label={localize(locale, 'Màu phủ', 'Tint color')}
                onChange={(e) => onSettings({ ...settings, coverMaskColor: e.target.value })}
              />
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                className="min-w-0 flex-1 accent-primary"
                value={coverMaskOpacity}
                disabled={busy}
                aria-label={localize(locale, 'Độ đậm vùng che', 'Mask opacity')}
                onChange={(e) => onSettings({ ...settings, coverMaskOpacity: Number(e.target.value) })}
              />
            </div>
          </div>
        )}
      </section>

      {hasMask && (
        <section className="space-y-3 rounded-lg border border-border bg-card p-3">
          <div>
            <h3 className="text-sm font-semibold text-foreground">{localize(locale, 'Áp dụng vị trí', 'Apply position')}</h3>
            <p className="mt-1 text-xs text-muted-foreground">{localize(locale, `Đồng bộ vị trí vùng che cho lane ${applyAllLaneLabel}.`, `Sync this mask position to the ${applyAllLaneLabel} lane.`)}</p>
          </div>

                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-1">
                    <button
                      type="button"
                      disabled={busy}
                      className={cn(
                        'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                        applyMode === 'full' ? 'border-primary bg-primary/10 text-primary font-medium' : 'border-border bg-background text-muted-foreground hover:bg-muted',
                      )}
                      onClick={() => setApplyMode('full')}
                    >
                      {localize(locale, 'Toàn video', 'Full video')}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      className={cn(
                        'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                        applyMode === 'range' ? 'border-primary bg-primary/10 text-primary font-medium' : 'border-border bg-background text-muted-foreground hover:bg-muted',
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
                    className="w-full rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                    disabled={busy || !(selected || bboxSeg) || segmentsLen === 0}
                    onClick={runApply}
                  >
                    {applyMode === 'full'
                      ? localize(locale, `Áp dụng cho toàn bộ ${applyAllLaneLabel}`, `Apply to all ${applyAllLaneLabel}`)
                      : localize(locale, `Áp dụng ${formatTimecode(Math.min(fromSec, toSec))} → ${formatTimecode(Math.max(fromSec, toSec))}`, `Apply ${formatTimecode(Math.min(fromSec, toSec))} → ${formatTimecode(Math.max(fromSec, toSec))}`)}
                  </button>
                </div>
        </section>
      )}

      <section className="space-y-2 rounded-lg border border-border bg-card p-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{localize(locale, 'Đặt lại', 'Reset')}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{localize(locale, 'Xóa vùng che đã lưu để nhận dạng lại.', 'Clear saved mask regions so they can be detected again.')}</p>
        </div>
              <div className="grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  className="rounded-md border border-border bg-accent hover:bg-muted px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50"
                  disabled={busy || !(selected || bboxSeg)}
                  onClick={() => resetOcrRegion('one')}
                >
                  {localize(locale, 'Đặt lại đoạn này', 'Reset current')}
                </button>
                <button
                  type="button"
                  className="rounded-md border border-border bg-background px-2 py-1.5 text-[11px] font-medium transition-colors hover:bg-muted disabled:opacity-50"
                  disabled={busy || segmentsLen === 0}
                  onClick={() => resetOcrRegion('all')}
                >
                  {localize(locale, 'Đặt lại tất cả', 'Reset all')}
                </button>
              </div>
      </section>
    </div>
  )
}
