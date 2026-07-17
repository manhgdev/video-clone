import type { ProjectSettings } from '@/features/project/project.types'

export const SETTINGS_LS = 'videoclone.settings'
export const SESSION_LS = 'videoclone.session'
export const SIDEBAR_W_LS = 'videoclone.sidebarWidth'
export const THEME_LS = 'videoclone.theme'

export const SIDEBAR_MIN = 240
export const SIDEBAR_MAX = 560
export const SIDEBAR_DEFAULT = 360

/** Mặc định lần đầu — từng engine khác nhau (user chỉnh sau thì nhớ riêng). */
export const ENGINE_DEFAULTS = {
  whisper: {
    matchDuration: 'preferVideo' as const,
    processOriginalAudio: false,
    originalAudioMode: 'original' as const,
    originalAudioVolume: 100,
  },
  paddleocr: {
    matchDuration: 'none' as const,
    processOriginalAudio: false,
    originalAudioMode: 'original' as const,
    originalAudioVolume: 100,
  },
}

export const defaultSettings: ProjectSettings = {
  engine: 'whisper',
  sourceLang: 'auto',
  targetLang: 'vi',
  translator: 'google',
  matchDuration: ENGINE_DEFAULTS.whisper.matchDuration,
  defaultVoice: 'cc:BV075_streaming:7102355803792740865',
  coverHardsubs: true,
  coverMaskStyle: 'blur',
  coverMaskColor: '#4c1d95',
  coverMaskOpacity: 40,
  burnSubs: true,
  captionPlacement: 'below',
  subtitleFontSize: 0,
  subtitleFontFamily: 'system',
  captionTextColor: '#ffffff',
  captionBgStyle: 'none',
  captionBgColor: '#000000',
  captionBgOpacity: 55,
  captionStroke: true,
  processOriginalAudio: ENGINE_DEFAULTS.whisper.processOriginalAudio,
  originalAudioMode: ENGINE_DEFAULTS.whisper.originalAudioMode,
  originalAudioVolume: ENGINE_DEFAULTS.whisper.originalAudioVolume,
  previewSec: 20,
  workers: 0,
  previewAspectRatio: 'original',
  engineProfiles: {
    whisper: { ...ENGINE_DEFAULTS.whisper },
    paddleocr: { ...ENGINE_DEFAULTS.paddleocr },
  },
}

export function loadTheme(): boolean {
  try {
    return localStorage.getItem(THEME_LS) === 'dark'
  } catch {
    return false
  }
}

export function loadSidebarWidth(): number {
  try {
    const raw = localStorage.getItem(SIDEBAR_W_LS)
    if (raw != null && raw !== '') {
      const n = Number(raw)
      if (Number.isFinite(n) && n > 0) {
        return Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, n))
      }
    }
  } catch {
    /* ignore */
  }
  return SIDEBAR_DEFAULT
}

export function applyEngineProfile(
  s: ProjectSettings,
  engine: ProjectSettings['engine'],
): ProjectSettings {
  const base = ENGINE_DEFAULTS[engine]
  const saved = s.engineProfiles?.[engine]
  return {
    ...s,
    engine,
    matchDuration: saved?.matchDuration ?? base.matchDuration,
    processOriginalAudio: saved?.processOriginalAudio ?? base.processOriginalAudio,
    originalAudioMode: saved?.originalAudioMode ?? base.originalAudioMode,
    originalAudioVolume: saved?.originalAudioVolume ?? base.originalAudioVolume,
  }
}

/** Ghi profile engine đang active (matchDuration / lọc âm) — không đụng engine kia. */
export function snapshotEngineProfile(s: ProjectSettings): ProjectSettings {
  const eng = s.engine === 'paddleocr' ? 'paddleocr' : 'whisper'
  return {
    ...s,
    engineProfiles: {
      ...s.engineProfiles,
      [eng]: {
        matchDuration: s.matchDuration,
        processOriginalAudio: s.processOriginalAudio,
        originalAudioMode: s.originalAudioMode,
        originalAudioVolume: s.originalAudioVolume,
      },
    },
  }
}

export function loadSettings(): ProjectSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_LS)
    if (!raw) return defaultSettings
    const s = { ...defaultSettings, ...JSON.parse(raw) } as ProjectSettings
    if (typeof s.workers !== 'number' || Number.isNaN(s.workers) || s.workers < 0) s.workers = 0
    if (typeof s.originalAudioVolume !== 'number' || Number.isNaN(s.originalAudioVolume)) {
      s.originalAudioVolume = 100
    } else {
      s.originalAudioVolume = Math.max(0, Math.min(100, s.originalAudioVolume))
    }
    const okTr = [
      'google',
      'mymemory',
      'tiktok',
      'ollama',
      'openai',
      'gemini',
      'deepseek',
      'openrouter',
      'grok',
    ] as const
    if (!okTr.includes(s.translator as (typeof okTr)[number])) s.translator = 'google'
    const okMask = ['blur', 'solid', 'mosaic'] as const
    if (!okMask.includes(s.coverMaskStyle as (typeof okMask)[number])) s.coverMaskStyle = 'blur'
    if (typeof s.coverMaskOpacity !== 'number' || Number.isNaN(s.coverMaskOpacity)) {
      s.coverMaskOpacity = 40
    } else {
      s.coverMaskOpacity = Math.max(0, Math.min(100, s.coverMaskOpacity))
    }
    if (typeof s.coverMaskColor !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(s.coverMaskColor)) {
      s.coverMaskColor = '#4c1d95'
    }
    const okFont = [
      'system', 'segoe', 'arial', 'bold', 'helvetica', 'verdana', 'tahoma',
      'trebuchet', 'rounded', 'impact', 'georgia', 'times', 'palatino', 'garamond',
      'courier', 'mono', 'comic', 'cjk', 'meiryo', 'malgun',
    ] as const
    if (!okFont.includes(s.subtitleFontFamily as (typeof okFont)[number])) {
      s.subtitleFontFamily = 'system'
    }
    if (typeof s.captionTextColor !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(s.captionTextColor)) {
      s.captionTextColor = '#ffffff'
    }
    const okBg = ['none', 'solid', 'blur', 'box'] as const
    if (!okBg.includes(s.captionBgStyle as (typeof okBg)[number])) s.captionBgStyle = 'none'
    if (typeof s.captionBgColor !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(s.captionBgColor)) {
      s.captionBgColor = '#000000'
    }
    if (typeof s.captionBgOpacity !== 'number' || Number.isNaN(s.captionBgOpacity)) {
      s.captionBgOpacity = 55
    } else {
      s.captionBgOpacity = Math.max(0, Math.min(100, s.captionBgOpacity))
    }
    if (typeof s.captionStroke !== 'boolean') s.captionStroke = true
    const okAspect = [
      'original', 'custom', '16:9', '4:3', '2.35:1', '2:1', '1.85:1',
      '9:16', '3:4', '58inch', '1:1',
    ] as const
    if (!okAspect.includes(s.previewAspectRatio as (typeof okAspect)[number])) {
      s.previewAspectRatio = 'original'
    }
    const okMatch = ['preferVideo', 'none', 'natural', 'stretch'] as const
    if (!okMatch.includes(s.matchDuration as (typeof okMatch)[number])) {
      s.matchDuration = 'preferVideo'
    }
    const eng = s.engine === 'paddleocr' ? 'paddleocr' : 'whisper'
    const profiles = {
      whisper: {
        ...ENGINE_DEFAULTS.whisper,
        ...s.engineProfiles?.whisper,
      },
      paddleocr: {
        ...ENGINE_DEFAULTS.paddleocr,
        ...s.engineProfiles?.paddleocr,
      },
    }
    if (!s.engineProfiles?.[eng]) {
      profiles[eng] = {
        matchDuration: s.matchDuration,
        processOriginalAudio: s.processOriginalAudio,
        originalAudioMode: s.originalAudioMode,
        originalAudioVolume: s.originalAudioVolume,
      }
    }
    s.engineProfiles = profiles
    const active = profiles[eng]
    s.matchDuration = active.matchDuration ?? ENGINE_DEFAULTS[eng].matchDuration
    s.processOriginalAudio = active.processOriginalAudio ?? ENGINE_DEFAULTS[eng].processOriginalAudio
    s.originalAudioMode = active.originalAudioMode ?? ENGINE_DEFAULTS[eng].originalAudioMode
    s.originalAudioVolume = active.originalAudioVolume ?? ENGINE_DEFAULTS[eng].originalAudioVolume
    return s
  } catch {
    return defaultSettings
  }
}

export function persistSettings(s: ProjectSettings) {
  try {
    localStorage.setItem(SETTINGS_LS, JSON.stringify(snapshotEngineProfile(s)))
  } catch {
    /* quota / private mode */
  }
}

export function persistSession(projectId: string | null) {
  try {
    if (projectId) localStorage.setItem(SESSION_LS, projectId)
    else localStorage.removeItem(SESSION_LS)
  } catch {
    /* ignore */
  }
}
