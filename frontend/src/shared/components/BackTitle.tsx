import type { ReactNode } from 'react'
import { localize, useLocale } from '@/app/i18n'
import './BackTitle.css'

export function BackTitle({ onBack, children }: { onBack: () => void; children: ReactNode }) {
  const { locale } = useLocale()
  const label = localize(locale, 'Quay lại', 'Back')
  return (
    <h1 className="back-title">
      <button type="button" className="back-title-btn" onClick={onBack} aria-label={label} title={label}>←</button>
      {children}
    </h1>
  )
}
