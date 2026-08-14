import type { AppMode } from '@/shared/components/Header'

export const APP_MODE_LS = 'videoclone.appMode'

export const APP_MODES = ['clone', 'renders', 'cleaner', 'srt-image', 'srt-export', 'download', 'tts'] as const satisfies readonly AppMode[]

/** Validate raw storage — invalid/corrupt/missing → Clone Video. */
export function parseAppMode(raw: string | null | undefined): AppMode {
  if (typeof raw === 'string' && (APP_MODES as readonly string[]).includes(raw)) {
    return raw as AppMode
  }
  return 'clone'
}

export function loadAppMode(): AppMode {
  try {
    return parseAppMode(localStorage.getItem(APP_MODE_LS))
  } catch {
    return 'clone'
  }
}

export function persistAppMode(mode: AppMode): void {
  try {
    localStorage.setItem(APP_MODE_LS, mode)
  } catch {
    /* quota / private mode */
  }
}

/** ponytail: self-check — invalid/missing → clone; all tabs restore */
export function __checkParseAppMode(): void {
  if (parseAppMode(null) !== 'clone') throw new Error('null → clone')
  if (parseAppMode(undefined) !== 'clone') throw new Error('undefined → clone')
  if (parseAppMode('') !== 'clone') throw new Error('empty → clone')
  if (parseAppMode('bogus') !== 'clone') throw new Error('bogus → clone')
  if (parseAppMode('TTS') !== 'clone') throw new Error('wrong case → clone')
  if (parseAppMode('{"mode":"tts"}') !== 'clone') throw new Error('json junk → clone')
  for (const m of APP_MODES) {
    if (parseAppMode(m) !== m) throw new Error(`keep ${m}`)
  }
}
