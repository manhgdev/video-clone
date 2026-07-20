import { useEffect, useRef, useState } from 'react'

export const VOICE_TAGS = [
  '👨 Nam',
  '👩 Nữ',
  '🏔️ Miền Bắc',
  '🌴 Miền Nam',
  '👶 Trẻ em',
  '👴 Người già',
  '⭐ Review',
  '📜 Đọc thơ',
  '📰 Tin tức',
  '📢 Quảng cáo',
] as const

export type VoiceTagLabel = (typeof VOICE_TAGS)[number]

/** Ngôn ngữ gán cho giọng (metadata hiển thị / lọc). */
export const VOICE_LANGUAGES = [
  { code: 'vi', label: 'Tiếng Việt' },
  { code: 'en', label: 'Tiếng Anh' },
  { code: 'zh', label: 'Tiếng Trung' },
  { code: 'ja', label: 'Tiếng Nhật' },
  { code: 'ko', label: 'Tiếng Hàn' },
  { code: 'th', label: 'Tiếng Thái' },
  { code: 'id', label: 'Tiếng Indonesia' },
  { code: 'es', label: 'Tiếng Tây Ban Nha' },
  { code: 'fr', label: 'Tiếng Pháp' },
  { code: 'de', label: 'Tiếng Đức' },
  { code: 'pt', label: 'Tiếng Bồ Đào Nha' },
] as const

export type VoiceLanguageCode = (typeof VOICE_LANGUAGES)[number]['code']
/** Rỗng = chưa gán ngôn ngữ (không mặc định VI). */
export type VoiceLanguageValue = VoiceLanguageCode | ''

export function normalizeVoiceLanguage(raw?: string | null): VoiceLanguageValue {
  const s = (raw || '').trim()
  if (!s) return ''
  const base = s.toLowerCase().split(/[-_]/)[0]
  return (VOICE_LANGUAGES.some((x) => x.code === base) ? base : '') as VoiceLanguageValue
}

const OPPOSITES: Partial<Record<VoiceTagLabel, VoiceTagLabel>> = {
  '👨 Nam': '👩 Nữ',
  '👩 Nữ': '👨 Nam',
  '🏔️ Miền Bắc': '🌴 Miền Nam',
  '🌴 Miền Nam': '🏔️ Miền Bắc',
  '👶 Trẻ em': '👴 Người già',
  '👴 Người già': '👶 Trẻ em',
}

export function canonicalVoiceTags(tags?: string[]): VoiceTagLabel[] {
  const selected = new Set(tags || [])
  return VOICE_TAGS.filter((tag) => selected.has(tag))
}

export function VoiceTagPicker({
  value,
  onChange,
  disabled = false,
  label = 'Tag giọng nói',
}: {
  value: string[]
  onChange: (tags: VoiceTagLabel[]) => void
  disabled?: boolean
  label?: string
}) {
  const selected = canonicalVoiceTags(value)
  const toggle = (tag: VoiceTagLabel) => {
    const next = new Set(selected)
    if (next.has(tag)) {
      next.delete(tag)
    } else {
      const opposite = OPPOSITES[tag]
      if (opposite) next.delete(opposite)
      next.add(tag)
    }
    onChange(VOICE_TAGS.filter((item) => next.has(item)))
  }

  return (
    <fieldset className="tts-tag-picker" disabled={disabled}>
      <legend>{label}</legend>
      <div className="tts-tag-picker-chips">
        {VOICE_TAGS.map((tag) => (
          <button
            key={tag}
            type="button"
            className={`tts-tag-picker-chip${selected.includes(tag) ? ' is-active' : ''}`}
            aria-pressed={selected.includes(tag)}
            onClick={() => toggle(tag)}
          >
            {tag}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

export default function VoiceMetadataModal({
  name: initialName,
  tags: initialTags,
  language: initialLanguage,
  onSave,
  onClose,
}: {
  name: string
  tags: string[]
  language?: string
  onSave: (name: string, tags: VoiceTagLabel[], language: VoiceLanguageValue, file: File | null) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(initialName)
  const [tags, setTags] = useState<VoiceTagLabel[]>(() => canonicalVoiceTags(initialTags))
  const [language, setLanguage] = useState<VoiceLanguageValue>(() => normalizeVoiceLanguage(initialLanguage))
  const [file, setFile] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const [validation, setValidation] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, saving])

  const submit = async () => {
    if (saving) return
    const cleanName = name.trim()
    if (!cleanName) {
      setValidation('Tên giọng không được trống')
      inputRef.current?.focus()
      return
    }
    setSaving(true)
    setValidation('')
    try {
      await onSave(cleanName, tags, language, file)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="tts-modal-backdrop" role="presentation" onClick={() => !saving && onClose()}>
      <div
        className="tts-modal tts-voice-metadata-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tts-voice-metadata-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h3 id="tts-voice-metadata-title" className="tts-modal-title">Sửa thông tin giọng</h3>
        <label className="tts-field">
          <span>Tên giọng</span>
          <input
            ref={inputRef}
            type="text"
            value={name}
            disabled={saving}
            aria-invalid={Boolean(validation)}
            placeholder="Nhập tên giọng"
            autoComplete="off"
            onChange={(event) => {
              setName(event.target.value)
              if (validation) setValidation('')
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                void submit()
              }
            }}
          />
        </label>
        <label className="tts-field">
          <span>Ngôn ngữ giọng</span>
          <select
            value={language}
            disabled={saving}
            onChange={(event) => setLanguage(normalizeVoiceLanguage(event.target.value))}
          >
            <option value="">— Chưa chọn —</option>
            {VOICE_LANGUAGES.map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="tts-field">
          <span>Đổi file giọng <small>(không bắt buộc)</small></span>
          <input
            type="file"
            accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg"
            disabled={saving}
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>
        {validation && <p className="tts-modal-validation" role="alert">{validation}</p>}
        <VoiceTagPicker value={tags} onChange={setTags} disabled={saving} />
        <div className="tts-modal-actions">
          <button type="button" className="tts-btn tts-btn-ghost" disabled={saving} onClick={onClose}>
            Hủy
          </button>
          <button type="button" className="tts-btn tts-btn-blue" disabled={saving} onClick={() => void submit()}>
            {saving ? 'Đang lưu…' : 'Lưu'}
          </button>
        </div>
      </div>
    </div>
  )
}
