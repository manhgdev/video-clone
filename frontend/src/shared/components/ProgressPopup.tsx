import { useEffect, useState } from 'react'
import { cn } from '@/lib/cn'

export type ProgressPopupProps = {
  /** Job đang chạy hoặc vừa lỗi cần hiện UI */
  active: boolean
  /** true = chỉ hiện pill góc; false = popup giữa màn */
  minimized: boolean
  title?: string
  message?: string
  /** 0–100 */
  progress: number
  error?: string | null
  /** Job còn chạy (khác lỗi đã xong) — bật đồng hồ / heartbeat */
  running?: boolean
  /** Chỉ “Chạy nền” ẩn popup, job vẫn chạy */
  onMinimize: () => void
  /** Click pill → mở lại popup */
  onRestore: () => void
  /** Huỷ job (tuỳ chọn) */
  onCancel?: () => void
  className?: string
}

function clampPct(n: number) {
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, Math.round(n)))
}

function fmtElapsed(sec: number) {
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}m${s.toString().padStart(2, '0')}s`
}

/**
 * Popup tiến độ tái sử dụng: % + X hủy + nút chạy nền.
 * Parent giữ state `minimized`; job không phụ thuộc vào việc đóng popup.
 * % đứng lâu vẫn hiện đồng hồ + “vẫn đang chạy” — tránh tưởng UI đơ.
 */
export default function ProgressPopup({
  active,
  minimized,
  title = 'Đang xử lý',
  message,
  progress,
  error,
  running = false,
  onMinimize,
  onRestore,
  onCancel,
  className,
}: ProgressPopupProps) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!active || !running) {
      setElapsed(0)
      return
    }
    const t0 = Date.now()
    const id = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - t0) / 1000))
    }, 1000)
    return () => window.clearInterval(id)
  }, [active, running])

  if (!active) return null

  const pct = clampPct(progress)
  const failed = Boolean(error && error !== 'cancelled')
  const base = error && error !== 'cancelled' ? error : message || title
  const compactLine =
    running && !failed
      ? ` · đã chạy ${fmtElapsed(elapsed)} · vẫn đang xử lý`
      : ''
  const line = base

  if (minimized) {
    return (
      <button
        type="button"
        className={cn(
          'fixed bottom-4 right-4 z-[200] flex items-center gap-2 rounded-lg border border-border bg-background/95 px-3 py-2 text-left shadow-lg backdrop-blur-sm',
          'hover:bg-accent/40 transition-colors max-w-[min(360px,90vw)]',
          className,
        )}
        onClick={onRestore}
        title="Mở lại tiến độ"
      >
        <span
          className={cn(
            'h-2 w-2 shrink-0 rounded-full',
            failed ? 'bg-destructive' : 'bg-primary animate-pulse',
          )}
        />
        <span className="min-w-0 flex-1 truncate text-xs text-foreground">{`${line}${compactLine}`}</span>
        <span className="shrink-0 tabular-nums text-xs font-medium text-muted-foreground">{pct}%</span>
      </button>
    )
  }

  return (
    <div
      className={cn(
        'fixed inset-0 z-[200] flex items-center justify-center bg-black/45 p-4',
        className,
      )}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-background shadow-xl">
        <div className="flex items-start gap-2 border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
            {line ? (
              <p className="mt-0.5 text-xs text-muted-foreground leading-snug break-words">{line}</p>
            ) : null}
          </div>
          <button
            type="button"
            className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            title={onCancel ? 'Hủy' : 'Đóng'}
            aria-label={onCancel ? 'Hủy' : 'Đóng'}
            onClick={onCancel ?? onMinimize}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-4 py-4 space-y-2">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-xs text-muted-foreground">
              {running && !failed ? `Đã chạy ${fmtElapsed(elapsed)}` : 'Tiến độ'}
            </span>
            <span className="text-sm font-semibold tabular-nums text-foreground">{pct}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                'h-full rounded-full transition-[width] duration-200 ease-out',
                failed ? 'bg-destructive' : 'bg-primary',
                running && !failed && pct > 0 && pct < 100 ? 'animate-pulse' : '',
              )}
              style={{ width: `${Math.max(pct, running && !failed ? 4 : 0)}%` }}
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button
            type="button"
            className="rounded-md border border-border bg-accent/40 px-3 py-1.5 text-xs hover:bg-accent"
            onClick={onMinimize}
          >
            Chạy nền
          </button>
          {onCancel ? (
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
              onClick={onCancel}
            >
              Huỷ
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
