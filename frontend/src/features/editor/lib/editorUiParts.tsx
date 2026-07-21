import React from 'react'
import { cn } from '@/shared/lib/cn'

/** OpenCut PanelView: h-11 header with title + scrollable content */
export function PanelView({
  title,
  children,
  showScrollbar = false,
}: {
  title: string
  children: React.ReactNode
  showScrollbar?: boolean
}) {
  return (
    <div className="relative flex h-full flex-col">
      <div className="bg-background h-11 shrink-0 pl-3 pr-2 flex items-center justify-between border-b border-border">
        <span className="text-muted-foreground text-sm">{title}</span>
      </div>
      <div className={cn('w-full min-h-0 flex-1 pt-2', showScrollbar ? 'overflow-y-scroll' : 'overflow-y-auto scrollbar-hidden')}>
        <div className="w-full flex-1 px-2 pt-0">{children}</div>
      </div>
    </div>
  )
}

export function PropLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-muted-foreground font-medium">{label}</span>
      {children}
    </label>
  )
}

/** Numeric input that commits on blur/Enter (avoids re-render storms while typing). */
export function NumField({
  label,
  value,
  step = 1,
  disabled,
  onCommit,
}: {
  label: string
  value: number
  step?: number
  disabled?: boolean
  onCommit: (value: number) => void
}) {
  const commit = (raw: string) => {
    const parsed = Number(raw)
    if (Number.isFinite(parsed) && Math.abs(parsed - value) > 1e-9) onCommit(parsed)
  }
  return (
    <PropLabel label={label}>
      <input
        key={value}
        type="number"
        className="w-full rounded-md border border-border bg-input px-2 py-1 text-xs outline-none focus:border-ring tabular-nums"
        defaultValue={Number.isInteger(value) ? value : +value.toFixed(2)}
        step={step}
        disabled={disabled}
        onBlur={(e) => commit(e.currentTarget.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
        }}
      />
    </PropLabel>
  )
}

export function TrackCtrl({
  title,
  active,
  onClick,
  children,
}: {
  title: string
  active?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      className={cn(
        'w-[18px] h-[18px] rounded-sm flex items-center justify-center shrink-0 transition-colors',
        active
          ? 'text-primary bg-primary/15'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent',
      )}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
    >
      {children}
    </button>
  )
}

export function CtxItem({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      className="w-full text-left px-3 py-1.5 hover:bg-accent disabled:opacity-40 disabled:pointer-events-none"
      onClick={onClick}
    >
      {children}
    </button>
  )
}

export function CtxSep() {
  return <div className="my-1 border-t border-border" role="separator" />
}

export function TlButton({
  title,
  onClick,
  disabled,
  active,
  children,
}: {
  title: string
  onClick?: () => void
  disabled?: boolean
  active?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'w-7 h-7 shrink-0 rounded-sm flex items-center justify-center transition-colors',
        'disabled:opacity-35 disabled:cursor-not-allowed',
        active
          ? 'text-primary bg-primary/15 hover:bg-primary/20'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent disabled:hover:bg-transparent disabled:hover:text-muted-foreground',
      )}
    >
      {children}
    </button>
  )
}
