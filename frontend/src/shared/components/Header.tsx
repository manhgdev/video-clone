import { useEffect, useRef, useState } from 'react'
import type { HardwareInfo } from '@/features/project/project.types'
import type { LicenseStatus } from '@/features/license/license.api'
import {
  IconBook,
  IconCam,
  IconClock,
  IconDownload,
  IconGear,
  IconLogo,
  IconMic,
  IconVideo,
  IconWand,
} from '@/shared/components/Icons'
import './Header.css'

export type AppMode = 'clone' | 'tts' | 'download' | 'film' | 'batch' | 'renders' | 'cleaner' | 'srt-image' | 'srt-export' | 'license'

function IconSun({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>
    </svg>
  )
}

function IconMoon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  )
}

const NAV: {
  id: AppMode | 'tools' | 'config' | 'help'
  label: string
  Icon: typeof IconCam
  mode?: AppMode
  action?: 'config' | 'tools'
}[] = [
  { id: 'clone', label: 'Clone Video', Icon: IconCam, mode: 'clone' },
  { id: 'renders', label: 'Đã render', Icon: IconVideo, mode: 'renders' },
  // ponytail: Film/Batch chỉ ẩn khỏi nav; giữ page để bật lại khi hai luồng hoàn thiện.
  // { id: 'film', label: 'Clone Phim', Icon: IconFilm, mode: 'film' },
  // { id: 'batch', label: 'Clone Hàng loạt', Icon: IconBatch, mode: 'batch' },
  { id: 'download', label: 'Download Video', Icon: IconDownload, mode: 'download' },
  { id: 'tts', label: 'Text to Speech', Icon: IconMic, mode: 'tts' },
  { id: 'tools', label: 'Tools', Icon: IconWand, action: 'tools' },
  { id: 'config', label: 'Cấu hình', Icon: IconGear, action: 'config' },
  { id: 'license', label: 'Kích hoạt', Icon: IconClock, mode: 'license' },
  { id: 'help', label: 'Hướng dẫn', Icon: IconBook },
]

const SHORT: Record<string, string> = { cpu: 'CPU', cuda: 'GPU', metal: 'GPU' }

function IconMenu({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  )
}

type Props = {
  hardware: HardwareInfo
  dark: boolean
  mode?: AppMode
  onModeChange?: (mode: AppMode) => void
  onToggleTheme: () => void
  onOpenConfig?: () => void
  /** TTS mobile: ☰ thay logo — mở sidebar trái */
  onMenuClick?: () => void
  menuOpen?: boolean
  licenseStatus?: LicenseStatus
}

export default function Header({
  hardware,
  dark,
  mode = 'clone',
  onModeChange,
  onToggleTheme,
  onOpenConfig,
  onMenuClick,
  menuOpen = false,
  licenseStatus,
}: Props) {
  const display = SHORT[hardware.accel] ?? hardware.accel.toUpperCase()
  const showTtsMenu = mode === 'tts' && typeof onMenuClick === 'function'
  const [toolsOpen, setToolsOpen] = useState(false)
  const toolsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!toolsOpen) return
    const close = (event: MouseEvent) => {
      if (!toolsRef.current?.contains(event.target as Node)) setToolsOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setToolsOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [toolsOpen])

  return (
    <header className={`header${showTtsMenu ? ' header--tts' : ''}`}>
      <div className="brand">
        {showTtsMenu && (
          <button
            type="button"
            className={`header-menu-btn${menuOpen ? ' is-open' : ''}`}
            onClick={onMenuClick}
            aria-label={menuOpen ? 'Đóng menu TTS' : 'Mở menu TTS'}
            aria-expanded={menuOpen}
            title="Menu Text to Speech"
          >
            <IconMenu size={22} />
          </button>
        )}
        <span className="header-logo-wrap" aria-hidden={showTtsMenu ? undefined : undefined}>
          <IconLogo />
        </span>
        <div className="brand-text">
          <strong>ZM TOOL</strong>
          <span>Studio Dịch Thuật & Ghép & Lồng Tiếng AI</span>
        </div>
      </div>
      <nav className="nav" aria-label="Chính">
        {NAV.map((item) => {
          if (item.action === 'tools') {
            return (
              <div key={item.id} className="nav-tools" ref={toolsRef}>
                <button
                  type="button"
                  className={mode === 'cleaner' || mode === 'srt-image' || mode === 'srt-export'
                    ? 'active'
                    : mode === 'download' ? 'compact-active' : undefined}
                  aria-haspopup="menu"
                  aria-expanded={toolsOpen}
                  onClick={() => setToolsOpen((open) => !open)}
                >
                  <item.Icon size={16} />
                  <span>{item.label}</span>
                </button>
                {toolsOpen ? (
                  <div className="nav-tools-menu" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      className={`nav-download-menu${mode === 'download' ? ' active' : ''}`}
                      onClick={() => {
                        setToolsOpen(false)
                        onModeChange?.('download')
                      }}
                    >
                      <IconDownload size={16} />
                      <span>Download Video</span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className={mode === 'cleaner' ? 'active' : undefined}
                      onClick={() => {
                        setToolsOpen(false)
                        onModeChange?.('cleaner')
                      }}
                    >
                      <IconWand size={16} />
                      <span>Làm sạch video</span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className={mode === 'srt-image' ? 'active' : undefined}
                      onClick={() => {
                        setToolsOpen(false)
                        onModeChange?.('srt-image')
                      }}
                    >
                      <IconVideo size={16} />
                      <span>Ghép ảnh/video SRT</span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className={mode === 'srt-export' ? 'active' : undefined}
                      onClick={() => {
                        setToolsOpen(false)
                        onModeChange?.('srt-export')
                      }}
                    >
                      <IconBook size={16} />
                      <span>Xuất Phụ Đề</span>
                    </button>
                  </div>
                ) : null}
              </div>
            )
          }
          const active =
            item.mode != null
              ? mode === item.mode
              : item.action === 'config'
                ? false
                : false
          return (
            <button
              key={item.id}
              type="button"
              className={`${active ? 'active ' : ''}${item.id === 'download' ? 'nav-download' : ''}`.trim() || undefined}
              onClick={() => {
                if (item.action === 'config') {
                  onOpenConfig?.()
                  return
                }
                if (item.mode) onModeChange?.(item.mode)
              }}
            >
              <item.Icon size={16} />
              <span>{item.label}</span>
            </button>
          )
        })}
      </nav>
      <div className="hw" title={hardware.label}>
        {licenseStatus && (
          <span
            className={`license-expiry${licenseStatus.remainingDay !== -1 && licenseStatus.remainingDay <= 7 ? ' is-warning' : ''}`}
            title={licenseStatus.expiresAt ? `Hết hạn: ${new Date(licenseStatus.expiresAt).toLocaleString('vi-VN')}` : undefined}
          >
            {licenseStatus.remainingDay === -1 ? 'Không giới hạn' : `Còn ${licenseStatus.remainingDay} ngày`}
          </span>
        )}
        <span className="dot" />
        {display}
        <button
          type="button"
          className="theme-toggle"
          onClick={onToggleTheme}
          title={dark ? 'Chuyển sang Light Mode' : 'Chuyển sang Dark Mode'}
          aria-label={dark ? 'Light mode' : 'Dark mode'}
        >
          {dark ? <IconSun size={15} /> : <IconMoon size={15} />}
        </button>
      </div>
    </header>
  )
}
