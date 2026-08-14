import { createContext, useContext, useEffect } from 'react'
import englishCatalog from './ui.en.json'

export type AppLocale = 'vi' | 'en'

export const LOCALE_LS = 'videoclone.locale'

export function detectLocale(): AppLocale {
  try {
    const languages = navigator.languages?.length ? navigator.languages : [navigator.language]
    return languages.some((language) => language?.toLowerCase().startsWith('vi')) ? 'vi' : 'en'
  } catch {
    return 'vi'
  }
}

export function loadLocale(): AppLocale {
  try {
    const saved = localStorage.getItem(LOCALE_LS)
    if (saved === 'vi' || saved === 'en') return saved
  } catch {
    /* private mode */
  }
  return detectLocale()
}

export function persistLocale(locale: AppLocale) {
  try { localStorage.setItem(LOCALE_LS, locale) } catch { /* private mode */ }
  document.cookie = `videoclone_locale=${locale}; path=/; SameSite=Lax`
}

type LocaleContextValue = { locale: AppLocale; setLocale: (locale: AppLocale) => void }
export const LocaleContext = createContext<LocaleContextValue>({ locale: 'vi', setLocale: () => {} })

const MESSAGES = {
  'brand.tagline': { vi: 'Studio Dịch Thuật & Ghép & Lồng Tiếng AI', en: 'AI Translation, Video Cloning & Dubbing Studio' },
  'nav.clone': { vi: 'Clone Video', en: 'Clone Video' },
  'nav.renders': { vi: 'Đã render', en: 'Renders' },
  'nav.download': { vi: 'Download Video', en: 'Download Video' },
  'nav.tts': { vi: 'Text to Speech', en: 'Text to Speech' },
  'nav.tools': { vi: 'Tools', en: 'Tools' },
  'nav.settings': { vi: 'Cấu hình', en: 'Settings' },
  'tools.cleanVideo': { vi: 'Làm sạch video', en: 'Clean video' },
  'tools.srtImage': { vi: 'Ghép ảnh/video SRT', en: 'Create SRT image/video' },
  'tools.exportSubtitles': { vi: 'Xuất phụ đề', en: 'Export subtitles' },
  'header.openTtsMenu': { vi: 'Mở menu TTS', en: 'Open TTS menu' },
  'header.closeTtsMenu': { vi: 'Đóng menu TTS', en: 'Close TTS menu' },
  'header.interfaceLanguage': { vi: 'Ngôn ngữ giao diện', en: 'Interface language' },
  'header.unlimited': { vi: 'Không giới hạn', en: 'Unlimited' },
  'header.daysLeft': { vi: '{count} ngày còn lại', en: '{count} days left' },
  'header.expires': { vi: 'Hết hạn: {date}', en: 'Expires: {date}' },
  'header.switchLight': { vi: 'Chuyển sang giao diện sáng', en: 'Switch to light mode' },
  'header.switchDark': { vi: 'Chuyển sang giao diện tối', en: 'Switch to dark mode' },
} as const

export type MessageKey = keyof typeof MESSAGES

export function translate(locale: AppLocale, key: MessageKey, values: Record<string, string | number> = {}): string {
  return MESSAGES[key][locale].replace(/\{(\w+)\}/g, (_, name: string) => String(values[name] ?? `{${name}}`))
}

export function useLocale() {
  return useContext(LocaleContext)
}

export function useT() {
  const { locale } = useLocale()
  return (key: MessageKey, values?: Record<string, string | number>) => translate(locale, key, values)
}

const TEXT_ATTRIBUTES = ['aria-label', 'placeholder', 'title'] as const
const originalText = new WeakMap<Text, string>()
const originalAttrs = new WeakMap<Element, Map<string, string>>()

/**
 * ponytail: existing UI predates i18n and contains hundreds of JSX literals.
 * This bridge uses the checked-in catalog while modules are migrated to keys,
 * so changing language never relies on an online translation service.
 */
export function LocaleTextSync() {
  const { locale } = useLocale()
  useEffect(() => {
    const translate = (text: string) => englishCatalog[text as keyof typeof englishCatalog]
    const applyText = (node: Text) => {
      const value = node.nodeValue || ''
      const leading = value.match(/^\s*/)?.[0] || ''
      const trailing = value.match(/\s*$/)?.[0] || ''
      const content = value.slice(leading.length, value.length - trailing.length)
      const original = originalText.get(node)
      if (locale === 'en') {
        const english = translate(content)
        if (english && english !== content) {
          originalText.set(node, content)
          node.nodeValue = `${leading}${english}${trailing}`
        }
      } else if (original && content === translate(original)) {
        node.nodeValue = `${leading}${original}${trailing}`
      }
    }
    const applyElement = (element: Element) => {
      if (element.closest('script, style, code, pre')) return
      for (const attribute of TEXT_ATTRIBUTES) {
        const value = element.getAttribute(attribute)
        if (!value) continue
        const saved = originalAttrs.get(element)?.get(attribute)
        if (locale === 'en') {
          const english = translate(value)
          if (english && english !== value) {
            const attrs = originalAttrs.get(element) || new Map<string, string>()
            attrs.set(attribute, value)
            originalAttrs.set(element, attrs)
            element.setAttribute(attribute, english)
          }
        } else if (saved && value === translate(saved)) {
          element.setAttribute(attribute, saved)
        }
      }
      for (const child of element.childNodes) if (child.nodeType === Node.TEXT_NODE) applyText(child as Text)
    }
    const apply = (root: Node) => {
      if (root.nodeType === Node.TEXT_NODE) applyText(root as Text)
      if (root.nodeType === Node.ELEMENT_NODE) {
        const element = root as Element
        applyElement(element)
        for (const child of element.querySelectorAll('*')) applyElement(child)
      }
    }
    apply(document.body)
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') applyText(record.target as Text)
        else if (record.type === 'attributes') applyElement(record.target as Element)
        else for (const node of record.addedNodes) apply(node)
      }
    })
    observer.observe(document.body, {
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [...TEXT_ATTRIBUTES],
      subtree: true,
    })
    return () => observer.disconnect()
  }, [locale])
  return null
}

export function localize(locale: AppLocale, vietnamese: string, english: string): string {
  return locale === 'en' ? english : vietnamese
}
