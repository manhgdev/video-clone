import { useEffect, useState } from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import { cn } from '@/shared/lib/cn'
import {
  COVER_MASK_STYLES,
  NumField,
  PropLabel,
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
  /** Độ dài timeline (s) — mặc định khoảng áp dụng */
  timelineDuration?: number
  /** Playhead hiện tại — gợi ý «từ» */
  playheadSec?: number
  commitCoverBox: (patch: Partial<PixelBox>) => void
  stretchCoverFullWidth: () => void
  applyCoverMaskToAll: (range?: CoverApplyRange) => void
  /** Reset bbox: one = clip đang chọn; all = mọi clip */
  resetOcrRegion: (scope: 'one' | 'all') => void
  /** Caption | CAP-MID | Dọc | Nhãn — chỉ áp cùng lane */
  applyAllLaneLabel?: string
  editSegment: (seg: Segment) => void
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
  editSegment,
}: Props) {
  const dur = Math.max(0, timelineDuration)
  const [applyMode, setApplyMode] = useState<'full' | 'range'>('full')
  const [fromSec, setFromSec] = useState(0)
  const [toSec, setToSec] = useState(0)

  useEffect(() => {
    if (dur <= 0) return
    setToSec((t) => (t <= 0 || t > dur ? Math.round(dur * 100) / 100 : t))
  }, [dur])

  useEffect(() => {
    // Gợi ý khoảng quanh playhead khi chuyển sang «Theo đoạn»
    if (applyMode !== 'range' || dur <= 0) return
    const ph = Math.max(0, Math.min(dur, playheadSec))
    setFromSec(Math.round(Math.max(0, ph - 2) * 100) / 100)
    setToSec(Math.round(Math.min(dur, ph + 8) * 100) / 100)
    // chỉ khi đổi mode — không reset mỗi frame playhead
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  return (
    <>
      <p className="text-[11px] text-muted-foreground leading-relaxed">
        Khung trên preview = vùng che chữ gốc. Xuất video dùng{' '}
        <strong className="text-foreground font-medium">cùng khung + kiểu mặt nạ</strong>.
        <strong className="text-foreground font-medium"> Làm mờ</strong> = kính mờ CapCut (blur + tint mỏng);
        <strong className="text-foreground font-medium"> Khối</strong> = phủ màu nền + texture (giống xuất);
        nếu vẫn lộ chữ cũ, chọn <strong className="text-foreground font-medium">Màu nền</strong> hoặc kéo rộng khung.
      </p>
      <PropLabel label="Kiểu mặt nạ">
        <div className="grid grid-cols-3 gap-1">
          {COVER_MASK_STYLES.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              disabled={busy}
              className={cn(
                'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                coverMaskStyle === id
                  ? 'border-violet-400 bg-violet-500/20 text-foreground'
                  : 'border-border bg-accent hover:bg-muted text-muted-foreground',
              )}
              onClick={() => onSettings({ ...settings, coverMaskStyle: id })}
            >
              {label}
            </button>
          ))}
        </div>
      </PropLabel>
      {coverMaskStyle === 'mosaic' && (
        <p className="text-[10px] text-muted-foreground leading-snug">
          Khối lấy màu nền quanh chữ + texture nhẹ — giống khi xuất; không dùng màu phủ.
        </p>
      )}
      {coverMaskStyle !== 'mosaic' && (
        <div className="space-y-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-muted-foreground font-medium">Màu phủ</span>
            <span className="text-[11px] text-muted-foreground tabular-nums">
              Độ đậm {coverMaskOpacity}%
            </span>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="color"
              className="h-8 w-10 shrink-0 cursor-pointer rounded-md border border-border bg-input p-0.5"
              value={coverMaskColor}
              disabled={busy}
              title="Màu phủ"
              onChange={(e) => onSettings({ ...settings, coverMaskColor: e.target.value })}
            />
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              className="min-w-0 flex-1 accent-violet-500"
              value={coverMaskOpacity}
              disabled={busy}
              title={`Độ đậm ${coverMaskOpacity}%`}
              onChange={(e) => onSettings({ ...settings, coverMaskOpacity: Number(e.target.value) })}
            />
          </div>
          {coverMaskStyle === 'blur' && (
            <p className="text-[10px] text-muted-foreground leading-snug">
              Độ đậm = blur + tint mỏng (CapCut).
            </p>
          )}
        </div>
      )}
      {(selected || bboxSeg) && selectedBox ? (
        <>
          <ul className="text-[10px] text-muted-foreground space-y-1 list-disc pl-4">
            <li>Kéo <strong>giữa</strong> khung → di chuyển (Alt = tắt snap giữa)</li>
            <li>Kéo <strong>góc/cạnh</strong> (chấm trắng) → phóng to/thu nhỏ tự do</li>
            <li>Sau khi thả, khung được <strong>giữ nguyên</strong> — không auto reset</li>
            <li>Phụ đề dịch fit trong khung đã kéo</li>
          </ul>
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
              label="Rộng"
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
              label="Cao"
              value={selectedBox.h}
              disabled={busy || !selected}
              onCommit={(v) =>
                commitCoverBox({
                  h: Math.round(Math.max(12, Math.min(sourceHeight - selectedBox.y, v))),
                })
              }
            />
          </div>
          {!selected && (
            <p className="text-[10px] text-muted-foreground">
              Đang hiện khung tại playhead — chọn đoạn để kéo/sửa số, hoặc Áp dụng bên dưới.
            </p>
          )}
          <p className="text-[10px] text-muted-foreground">
            Kéo cạnh trên/dưới (hoặc nhập Cao) để chỉnh chiều cao vùng che.
          </p>
          <button
            type="button"
            className="w-full rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
            disabled={busy || !selected || sourceWidth <= 0}
            title="Giữ Y/Cao, kéo ngang ~96% khung"
            onClick={stretchCoverFullWidth}
          >
            Full ngang
          </button>

          <div className="border-t border-border pt-2 space-y-2">
            <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
              Áp vị trí che · lane «{applyAllLaneLabel}»
            </p>
            <p className="text-[10px] text-muted-foreground leading-snug">
              Chỉ dời <strong className="text-foreground font-medium">Y</strong> (cao/thấp) — giữ nguyên bề ngang / cao từng clip.
            </p>
            <div className="grid grid-cols-2 gap-1">
              <button
                type="button"
                disabled={busy}
                className={cn(
                  'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                  applyMode === 'full'
                    ? 'border-violet-400 bg-violet-500/20 text-foreground'
                    : 'border-border bg-accent hover:bg-muted text-muted-foreground',
                )}
                onClick={() => setApplyMode('full')}
              >
                Full video
              </button>
              <button
                type="button"
                disabled={busy}
                className={cn(
                  'rounded-md border px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50',
                  applyMode === 'range'
                    ? 'border-violet-400 bg-violet-500/20 text-foreground'
                    : 'border-border bg-accent hover:bg-muted text-muted-foreground',
                )}
                onClick={() => setApplyMode('range')}
              >
                Từ → đến
              </button>
            </div>
            {applyMode === 'range' && (
              <div className="grid grid-cols-2 gap-2">
                <NumField
                  label="Từ"
                  value={fromSec}
                  step={0.1}
                  disabled={busy}
                  onCommit={(v) => setFromSec(Math.max(0, v))}
                  formatDisplay={formatTimecode}
                  parseDisplay={parseTimecode}
                />
                <NumField
                  label="Đến"
                  value={toSec}
                  step={0.1}
                  disabled={busy}
                  onCommit={(v) => setToSec(Math.max(0, v))}
                  formatDisplay={formatTimecode}
                  parseDisplay={parseTimecode}
                />
              </div>
            )}
            {applyMode === 'range' && (
              <p className="text-[10px] text-muted-foreground leading-snug">
                Áp Y cho <strong className="text-foreground font-medium">mọi bbox</strong> chồng khoảng{' '}
                <strong className="text-foreground font-medium tabular-nums">
                  {formatTimecode(Math.min(fromSec, toSec))} – {formatTimecode(Math.max(fromSec, toSec))}
                </strong>
                {dur > 0 ? ` (video ~${formatTimecode(dur)})` : ''} — không lọc lane.
              </p>
            )}
            <button
              type="button"
              className="w-full rounded-md border border-violet-400/60 bg-violet-500/15 hover:bg-violet-500/25 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
              disabled={busy || !(selected || bboxSeg) || segmentsLen === 0}
              title={
                applyMode === 'full'
                  ? `Chỉ dời Y khung che sang mọi clip lane «${applyAllLaneLabel}» — không đổi W/H`
                  : 'Dời Y mọi bbox trong khoảng thời gian đã chọn'
              }
              onClick={runApply}
            >
              {applyMode === 'full'
                ? `Áp vị trí (Y) · full · ${applyAllLaneLabel}`
                : `Áp Y · mọi bbox · ${formatTimecode(Math.min(fromSec, toSec))} → ${formatTimecode(Math.max(fromSec, toSec))}`}
            </button>
          </div>
        </>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          Chưa có vùng che tại playhead — chọn đoạn caption hoặc tua tới chỗ có chữ.
        </p>
      )}

      <div className="border-t border-border pt-2 space-y-1.5">
        <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
          Reset bbox
        </p>
        <div className="grid grid-cols-2 gap-1.5">
          <button
            type="button"
            className="rounded-md border border-border bg-accent hover:bg-muted px-2 py-1.5 text-[11px] transition-colors disabled:opacity-50"
            disabled={busy || !(selected || bboxSeg)}
            title="Xóa bbox clip đang chọn"
            onClick={() => resetOcrRegion('one')}
          >
            Reset 1 bbox
          </button>
          <button
            type="button"
            className="rounded-md border border-violet-400/50 bg-violet-500/10 hover:bg-violet-500/20 px-2 py-1.5 text-[11px] font-medium transition-colors disabled:opacity-50"
            disabled={busy || segmentsLen === 0}
            title="Xóa bbox mọi clip trong dự án"
            onClick={() => resetOcrRegion('all')}
          >
            Reset all bbox
          </button>
        </div>
      </div>
    </>
  )
}
