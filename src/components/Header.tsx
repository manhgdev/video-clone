import type { HardwareInfo } from '../types'
import {
  IconBatch,
  IconBook,
  IconCam,
  IconDownload,
  IconFilm,
  IconGear,
  IconLogo,
} from './Icons'
import './Header.css'

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

const NAV = [
  { label: 'Clone Video', Icon: IconCam, action: null as null | 'config' },
  { label: 'Clone Phim', Icon: IconFilm, action: null },
  { label: 'Clone Hàng loạt', Icon: IconBatch, action: null },
  { label: 'Download Video', Icon: IconDownload, action: null },
  { label: 'Cấu hình', Icon: IconGear, action: 'config' as const },
  { label: 'Hướng dẫn', Icon: IconBook, action: null },
]

const SHORT: Record<string, string> = { cpu: 'CPU', cuda: 'GPU', metal: 'GPU' }

type Props = {
  hardware: HardwareInfo
  dark: boolean
  onToggleTheme: () => void
  onOpenConfig?: () => void
}

export default function Header({ hardware, dark, onToggleTheme, onOpenConfig }: Props) {
  const display = SHORT[hardware.accel] ?? hardware.accel.toUpperCase()

  return (
    <header className="header">
      <div className="brand">
        <IconLogo />
        <div className="brand-text">
          <strong>VideoClone</strong>
          <span>Studio Dịch Thuật & Lồng Tiếng AI</span>
        </div>
      </div>
      <nav className="nav" aria-label="Chính">
        {NAV.map((item, i) => (
          <button
            key={item.label}
            type="button"
            className={i === 0 ? 'active' : undefined}
            onClick={() => {
              if (item.action === 'config') onOpenConfig?.()
            }}
          >
            <item.Icon size={16} />
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="hw" title={hardware.label}>
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
