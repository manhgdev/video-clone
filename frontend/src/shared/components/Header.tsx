import { useEffect, useRef, useState } from 'react'
import type { HardwareInfo } from '@/features/project/project.types'
import type { LicenseStatus } from '@/features/license/license.api'
import {
  IconBook,
  IconCam,
  IconDownload,
  IconGear,
  IconLogo,
  IconMic,
  IconVideo,
  IconWand,
} from '@/shared/components/Icons'
import './Header.css'
import { translate, type AppLocale } from '@/app/i18n'

export type AppMode = 'clone' | 'live-preview' | 'tts' | 'download' | 'film' | 'batch' | 'renders' | 'cleaner' | 'srt-image' | 'srt-export' | 'license'

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
  id: AppMode | 'tools' | 'config'
  label: 'nav.clone' | 'nav.livePreview' | 'nav.renders' | 'nav.download' | 'nav.tts' | 'nav.tools' | 'nav.settings'
  Icon: typeof IconCam
  mode?: AppMode
  action?: 'config' | 'tools'
}[] = [
  { id: 'clone', label: 'nav.clone', Icon: IconCam, mode: 'clone' },
  // Live Preview vẫn là page /live-preview, nhưng tạm ẩn khỏi top navigation
  // để chỉ mở theo ngữ cảnh một project từ Clone/Renders/Export.
  // { id: 'live-preview', label: 'nav.livePreview', Icon: IconVideo, mode: 'live-preview' },
  { id: 'renders', label: 'nav.renders', Icon: IconVideo, mode: 'renders' },
  // ponytail: Film/Batch chỉ ẩn khỏi nav; giữ page để bật lại khi hai luồng hoàn thiện.
  // { id: 'film', label: 'Clone Phim', Icon: IconFilm, mode: 'film' },
  // { id: 'batch', label: 'Clone Hàng loạt', Icon: IconBatch, mode: 'batch' },
  { id: 'download', label: 'nav.download', Icon: IconDownload, mode: 'download' },
  { id: 'tts', label: 'nav.tts', Icon: IconMic, mode: 'tts' },
  { id: 'tools', label: 'nav.tools', Icon: IconWand, action: 'tools' },
  { id: 'config', label: 'nav.settings', Icon: IconGear, action: 'config' },
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
  locale: AppLocale
  onLocaleChange: (locale: AppLocale) => void
  onOpenLicense?: () => void
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
  locale,
  onLocaleChange,
  onOpenLicense,
}: Props) {
  const t = (key: Parameters<typeof translate>[1], values?: Record<string, string | number>) => translate(locale, key, values)
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
            aria-label={menuOpen ? t('header.closeTtsMenu') : t('header.openTtsMenu')}
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
          <span>{t('brand.tagline')}</span>
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
                    : undefined}
                  aria-haspopup="menu"
                  aria-expanded={toolsOpen}
                  onClick={() => setToolsOpen((open) => !open)}
                >
                  <item.Icon size={16} />
                  <span>{t(item.label)}</span>
                </button>
                {toolsOpen ? (
                  <div className="nav-tools-menu" role="menu">
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
                      <span>{t('tools.cleanVideo')}</span>
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
                      <span>{t('tools.srtImage')}</span>
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
                      <span>{t('tools.exportSubtitles')}</span>
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
              <span>{t(item.label)}</span>
            </button>
          )
        })}
      </nav>
      <div className="hw" title={hardware.label}>
        <select className="locale-select" value={locale} onChange={(event) => onLocaleChange(event.target.value as AppLocale)} aria-label={t('header.interfaceLanguage')}>
          <option value="vi">VI</option>
          <option value="en">EN</option>
        </select>
        {licenseStatus && (
          <button
            type="button"
            className={`license-expiry${licenseStatus.remainingDay !== -1 && licenseStatus.remainingDay <= 7 ? ' is-warning' : ''}`}
            onClick={onOpenLicense}
            title={licenseStatus.expiresAt ? t('header.expires', { date: new Date(licenseStatus.expiresAt).toLocaleString(locale === 'vi' ? 'vi-VN' : 'en-US') }) : undefined}
          >
            {licenseStatus.remainingDay === -1 ? t('header.unlimited') : t('header.daysLeft', { count: licenseStatus.remainingDay })}
          </button>
        )}
        <span className="dot" />
        {display}
        <button
          type="button"
          className="theme-toggle"
          onClick={onToggleTheme}
          title={dark ? t('header.switchLight') : t('header.switchDark')}
          aria-label={dark ? 'Light mode' : 'Dark mode'}
        >
          {dark ? <IconSun size={15} /> : <IconMoon size={15} />}
        </button>
      </div>
    </header>
  )
}
