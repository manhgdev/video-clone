import { useEffect, useState } from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import { cn } from '@/shared/lib/cn'
import { COVER_MASK_STYLES, NumField, PropLabel, type PixelBox } from '@/features/editor/lib'

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
      {coverMaskStyle !== 'mosaic' && (
        <PropLabel label="Màu phủ">
          <input
            type="color"
            className="h-9 w-full cursor-pointer rounded-md border border-border bg-input"
            value={coverMaskColor}
            disabled={busy}
            onChange={(e) => onSettings({ ...settings, coverMaskColor: e.target.value })}
          />
        </PropLabel>
      )}
      {coverMaskStyle === 'mosaic' && (
        <p className="text-[10px] text-muted-foreground leading-snug">
          Khối lấy màu nền quanh chữ + texture nhẹ — giống khi xuất; không dùng màu phủ.
        </p>
      )}
      {coverMaskStyle === 'blur' && (
        <p className="text-[10px] text-muted-foreground leading-snug">
          Độ đậm = blur + tint mỏng (CapCut). Preview phải thấy kính mờ trên chữ gốc.
        </p>
      )}
      {coverMaskStyle !== 'mosaic' && (
        <PropLabel label={`Độ đậm: ${coverMaskOpacity}%`}>
          <input
            type="range"
            min={5}
            max={100}
            step={1}
            className="w-full accent-violet-500"
            value={coverMaskOpacity}
            disabled={busy}
            onChange={(e) => onSettings({ ...settings, coverMaskOpacity: Number(e.target.value) })}
          />
        </PropLabel>
      )}
      {(selected || bboxSeg) && selectedBox ? (
        <>
          <ul className="text-[10px] text-muted-foreground space-y-1 list-disc pl-4">
            <li>Kéo <strong>giữa</strong> khung → di chuyển (Alt = tắt snap giữa)</li>
            <li>Kéo <strong>góc/cạnh</strong> (chấm trắng) → phóng to/thu nhỏ tự do</li>
            <li>Sau khi thả, khung được <strong>giữ nguyên</strong> — không auto reset</li>
            <li>Phụ đề dịch fit trong khung đã kéo</li>
          </ul>
          <div className="grid grid-cols-2 gap-2">
            <NumField
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
                Từ → đến (s)
              </button>
            </div>
            {applyMode === 'range' && (
              <div className="grid grid-cols-2 gap-2">
                <NumField
                  label="Từ (s)"
                  value={fromSec}
                  step={0.1}
                  disabled={busy}
                  onCommit={(v) => setFromSec(Math.max(0, v))}
                />
                <NumField
                  label="Đến (s)"
                  value={toSec}
                  step={0.1}
                  disabled={busy}
                  onCommit={(v) => setToSec(Math.max(0, v))}
                />
              </div>
            )}
            {applyMode === 'range' && (
              <p className="text-[10px] text-muted-foreground leading-snug">
                Chỉ clip lane «{applyAllLaneLabel}» có thời gian chồng khoảng{' '}
                <strong className="text-foreground font-medium">
                  {Math.min(fromSec, toSec).toFixed(1)}s – {Math.max(fromSec, toSec).toFixed(1)}s
                </strong>
                {dur > 0 ? ` (video ~${dur.toFixed(1)}s)` : ''}.
              </p>
            )}
            <button
              type="button"
              className="w-full rounded-md border border-violet-400/60 bg-violet-500/15 hover:bg-violet-500/25 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
              disabled={busy || !(selected || bboxSeg) || segmentsLen === 0}
              title={
                applyMode === 'full'
                  ? `Chỉ dời Y khung che sang mọi clip lane «${applyAllLaneLabel}» — không đổi W/H`
                  : `Chỉ dời Y trong khoảng thời gian · lane «${applyAllLaneLabel}»`
              }
              onClick={runApply}
            >
              {applyMode === 'full'
                ? `Áp vị trí (Y) · full · ${applyAllLaneLabel}`
                : `Áp vị trí (Y) · ${Math.min(fromSec, toSec).toFixed(1)}s → ${Math.max(fromSec, toSec).toFixed(1)}s`}
            </button>
          </div>

          <button
            type="button"
            className="w-full rounded-md border border-border bg-accent hover:bg-muted px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
            disabled={busy || !selected?.bbox}
            onClick={() => selected && editSegment({ ...selected, bbox: null, captionLayout: null })}
          >
            Reset vùng OCR
          </button>
        </>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          Chưa có vùng che tại playhead — chọn đoạn caption hoặc tua tới chỗ có chữ.
        </p>
      )}
    </>
  )
}
