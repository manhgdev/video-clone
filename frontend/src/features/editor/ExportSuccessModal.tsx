import { createPortal } from 'react-dom'

interface ExportSuccessModalProps {
  isOpen: boolean
  onClose: () => void
  onRevealFolder: () => void
  onOpenVideo: () => void
  onOpenProject: () => void
  /** URL video đã xuất để preview (null nếu không có video) */
  videoSrc: string | null
  /** Thông điệp từ status */
  message: string
  /** settings để biết đã xuất loại gì */
  exportedTypes: {
    video: boolean
    audio: boolean
    srt: boolean
    gif: boolean
  }
  renderName?: string
}

function FileTypeIcon({ type }: { type: 'video' | 'audio' | 'srt' | 'gif' }) {
  if (type === 'video') return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 17v4" />
      <path d="m10 8 6 4-6 4V8z" fill="currentColor" stroke="none" />
    </svg>
  )
  if (type === 'audio') return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  )
  if (type === 'srt') return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6M8 13h8M8 17h5" />
    </svg>
  )
  // gif
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M9 12H6v-1a3 3 0 0 1 3-3c.8 0 1.5.3 2 .8" />
      <path d="M13 8v8M17 8v4h-2" />
    </svg>
  )
}

const TYPE_LABEL: Record<string, string> = {
  video: 'Video (MP4)',
  audio: 'Âm thanh (MP3)',
  srt: 'Chú thích (SRT)',
  gif: 'Ảnh động (GIF)',
}

export function ExportSuccessModal({
  isOpen,
  onClose,
  onRevealFolder,
  onOpenVideo,
  onOpenProject,
  videoSrc,
  message,
  exportedTypes,
  renderName,
}: ExportSuccessModalProps) {
  if (!isOpen) return null

  const types = (['video', 'audio', 'srt', 'gif'] as const).filter((t) => exportedTypes[t])
  const hasVideo = exportedTypes.video && !!videoSrc
  const title = renderName ? `Đã xuất — ${renderName}` : 'Xuất hoàn thành'

  return createPortal(
    <div
      className="fixed inset-0 z-[9998] flex items-center justify-center backdrop-blur-sm"
      style={{ backgroundColor: 'rgba(0,0,0,0.75)' }}
    >
      <div
        className="flex flex-col w-[640px] max-w-[95vw] rounded-xl overflow-hidden font-sans select-none shadow-2xl border"
        style={{
          backgroundColor: 'var(--card, #18191c)',
          color: 'var(--foreground, #e4e4e7)',
          borderColor: 'var(--border, #27272a)',
        }}
      >
        {/* ── Header ── */}
        <div
          className="flex items-center justify-between px-5 py-3 border-b"
          style={{ backgroundColor: 'var(--surface, #131417)', borderColor: 'var(--border, #27272a)' }}
        >
          <div className="flex items-center gap-2">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#00c4cc" strokeWidth="2.5">
              <path d="M20 6L9 17l-5-5" />
            </svg>
            <span className="text-sm font-semibold" style={{ color: 'var(--foreground, #ffffff)' }}>
              {title}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md opacity-60 hover:opacity-100 transition-opacity"
            style={{ color: 'var(--muted-foreground, #a1a1aa)' }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* ── Body ── */}
        <div className="flex gap-0 min-h-0">
          {/* Left: preview */}
          <div
            className="flex items-center justify-center shrink-0 w-[220px]"
            style={{ backgroundColor: 'var(--preview-workspace-bg, #000000)', minHeight: '260px' }}
          >
            {hasVideo ? (
              <video
                src={videoSrc!}
                className="w-full h-full object-contain"
                style={{ maxHeight: '260px' }}
                controls
                playsInline
              />
            ) : (
              <div className="flex flex-col items-center gap-3 opacity-50" style={{ color: 'var(--muted-foreground, #a1a1aa)' }}>
                <FileTypeIcon type={types[0] ?? 'audio'} />
                <span className="text-xs">{TYPE_LABEL[types[0] ?? 'audio']}</span>
              </div>
            )}
          </div>

          {/* Right: info */}
          <div className="flex flex-col flex-1 p-5 gap-4 justify-between">
            {/* Output types */}
            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--muted-foreground, #71717a)' }}>
                Đã xuất
              </span>
              <div className="flex flex-col gap-2">
                {types.map((t) => (
                  <div key={t} className="flex items-center gap-2.5">
                    <div className="flex items-center justify-center w-7 h-7 rounded-md"
                      style={{ backgroundColor: 'var(--muted, #2d2e34)', color: '#00c4cc' }}>
                      <FileTypeIcon type={t} />
                    </div>
                    <span className="text-sm" style={{ color: 'var(--foreground, #e4e4e7)' }}>
                      {TYPE_LABEL[t]}
                    </span>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2.5">
                      <path d="M20 6L9 17l-5-5" />
                    </svg>
                  </div>
                ))}
              </div>
            </div>

            {/* Message */}
            {message && (
              <div
                className="px-3 py-2 rounded-md text-xs leading-relaxed"
                style={{ backgroundColor: 'var(--muted, #2d2e34)', color: 'var(--muted-foreground, #a1a1aa)' }}
              >
                {message}
              </div>
            )}
          </div>
        </div>

        {/* ── Footer ── */}
        <div
          className="flex items-center justify-end gap-2 px-5 py-3 border-t"
          style={{ backgroundColor: 'var(--surface, #131417)', borderColor: 'var(--border, #27272a)' }}
        >
          <button
            type="button"
            onClick={onOpenVideo}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium transition-colors hover:opacity-80"
            style={{ backgroundColor: 'var(--muted, #2d2e34)', color: 'var(--foreground, #e4e4e7)' }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="m10 8 6 4-6 4V8z" fill="currentColor" stroke="none" />
              <rect x="2" y="3" width="20" height="14" rx="2" />
            </svg>
            Mở video
          </button>
          <button
            type="button"
            onClick={onRevealFolder}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium transition-colors hover:opacity-80"
            style={{ backgroundColor: 'var(--muted, #2d2e34)', color: 'var(--foreground, #e4e4e7)' }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            Mở thư mục
          </button>
          <button
            type="button"
            onClick={onOpenProject}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium transition-colors hover:opacity-80"
            style={{ backgroundColor: 'var(--muted, #2d2e34)', color: 'var(--foreground, #e4e4e7)' }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            Mở dự án
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-5 py-1.5 rounded-md text-xs font-semibold transition-colors hover:opacity-80"
            style={{ backgroundColor: '#00c4cc', color: '#000000' }}
          >
            OK
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
