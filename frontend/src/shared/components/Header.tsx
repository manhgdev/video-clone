import type { HardwareInfo } from '@/features/project/project.types'
import {
  IconBatch,
  IconBook,
  IconCam,
  IconDownload,
  IconFilm,
  IconGear,
  IconLogo,
  IconMic,
} from '@/shared/components/Icons'
import './Header.css'

export type AppMode = 'clone' | 'tts' | 'download' | 'film' | 'batch'

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
  id: AppMode | 'config' | 'help'
  label: string
  Icon: typeof IconCam
  mode?: AppMode
  action?: 'config'
}[] = [
  { id: 'clone', label: 'Clone Video', Icon: IconCam, mode: 'clone' },
  { id: 'film', label: 'Clone Phim', Icon: IconFilm, mode: 'film' },
  { id: 'batch', label: 'Clone Hàng loạt', Icon: IconBatch, mode: 'batch' },
  { id: 'download', label: 'Download Video', Icon: IconDownload, mode: 'download' },
  { id: 'tts', label: 'Text to Speech', Icon: IconMic, mode: 'tts' },
  { id: 'config', label: 'Cấu hình', Icon: IconGear, action: 'config' },
  { id: 'help', label: 'Hướng dẫn', Icon: IconBook },
]

const SHORT: Record<string, string> = { cpu: 'CPU', cuda: 'GPU', metal: 'GPU' }

type Props = {
  hardware: HardwareInfo
  dark: boolean
  mode?: AppMode
  onModeChange?: (mode: AppMode) => void
  onToggleTheme: () => void
  onOpenConfig?: () => void
}

export default function Header({
  hardware,
  dark,
  mode = 'clone',
  onModeChange,
  onToggleTheme,
  onOpenConfig,
}: Props) {
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
        {NAV.map((item) => {
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
              className={active ? 'active' : undefined}
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
