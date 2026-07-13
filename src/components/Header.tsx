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

const NAV = [
  { label: 'Clone Video', Icon: IconCam, action: null as null | 'config' },
  { label: 'Clone Phim', Icon: IconFilm, action: null },
  { label: 'Clone Hàng loạt', Icon: IconBatch, action: null },
  { label: 'Download Video', Icon: IconDownload, action: null },
  { label: 'Cấu hình', Icon: IconGear, action: 'config' as const },
  { label: 'Hướng dẫn', Icon: IconBook, action: null },
]

type Props = {
  hardware: HardwareInfo
  onOpenConfig?: () => void
}

export default function Header({ hardware, onOpenConfig }: Props) {
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
      <div className="hw" title={hardware.accel}>
        <span className="dot" />
        {hardware.label}
      </div>
    </header>
  )
}
