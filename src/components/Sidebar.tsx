import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { ProjectSettings } from '../types'
import {
  IconArrowRight,
  IconClock,
  IconGlobe,
  IconLangSwap,
  IconLayers,
  IconMic,
  IconPlay,
  IconSpeaker,
  IconTranslate,
  IconType,
} from './Icons'
import './Sidebar.css'

type Props = {
  videoUrl: string | null
  settings: ProjectSettings
  voices: { id: string; name: string }[]
  busy: boolean
  onSettings: (s: ProjectSettings) => void
  onUpload: (file: File) => void
  onTranslateAll: () => void
  onPreview: () => void
  onCancel: () => void
}

function Field({
  label,
  icon,
  children,
  hint,
}: {
  label: string
  icon?: ReactNode
  children: ReactNode
  hint?: string
}) {
  return (
    <div className="field">
      <span className="field-label">
        {icon}
        {label}
      </span>
      {children}
      {hint && <em className="field-hint">{hint}</em>}
    </div>
  )
}

export default function Sidebar({
  videoUrl,
  settings,
  voices,
  busy,
  onSettings,
  onUpload,
  onTranslateAll,
  onPreview,
  onCancel,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [portrait, setPortrait] = useState(false)
  const [showCancel, setShowCancel] = useState(false)
  const [previewDraft, setPreviewDraft] = useState(
    String(settings.previewSec > 0 ? settings.previewSec : 20),
  )

  useEffect(() => {
    setPreviewDraft(String(settings.previewSec > 0 ? settings.previewSec : 20))
  }, [settings.previewSec])

  useEffect(() => {
    if (!busy) {
      setShowCancel(false)
      return
    }
    // hiện Huỷ sớm — cancel flag arm ngay khi Queued
    const t = window.setTimeout(() => setShowCancel(true), 350)
    return () => window.clearTimeout(t)
  }, [busy])

  const set = <K extends keyof ProjectSettings>(key: K, value: ProjectSettings[K]) => {
    if (busy) return
    onSettings({ ...settings, [key]: value })
  }

  const commitPreviewSec = () => {
    if (busy) {
      setPreviewDraft(String(settings.previewSec > 0 ? settings.previewSec : 20))
      return
    }
    const value = Math.max(5, Math.min(600, Number(previewDraft) || 20))
    setPreviewDraft(String(value))
    onSettings({ ...settings, previewSec: value })
  }

  const fontSizes = [16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96, 120]
  const fontSizeOptions = settings.subtitleFontSize === 0 || fontSizes.includes(settings.subtitleFontSize)
    ? fontSizes
    : [...fontSizes, settings.subtitleFontSize].sort((a, b) => a - b)

  return (
    <aside className={`sidebar${busy ? ' is-busy' : ''}`}>
      <div
        className={`preview${portrait ? ' portrait' : ''}${busy ? ' locked' : ''}`}
        onClick={(e) => {
          // Busy: chỉ xem video, không chọn file mới
          if (busy) return
          // click vào controls video — đừng mở file picker
          if ((e.target as HTMLElement).tagName === 'VIDEO') return
          if (videoUrl) return
          inputRef.current?.click()
        }}
        onKeyDown={(e) => {
          if (!busy && !videoUrl && e.key === 'Enter') inputRef.current?.click()
        }}
        role={videoUrl ? undefined : 'button'}
        tabIndex={videoUrl || busy ? -1 : 0}
        aria-disabled={busy && !videoUrl}
      >
        {videoUrl ? (
          <video
            src={videoUrl}
            controls
            playsInline
            onLoadedMetadata={(e) => {
              const v = e.currentTarget
              setPortrait(v.videoHeight > v.videoWidth)
            }}
          />
        ) : (
          <div className="preview-empty">
            <strong>Chọn video</strong>
            <span>MP4 9:16 hoặc 16:9</span>
          </div>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        hidden
        disabled={busy}
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f && !busy) onUpload(f)
        }}
      />
      {videoUrl && (
        <button
          type="button"
          className="linkish"
          disabled={busy}
          onClick={() => {
            if (!busy) inputRef.current?.click()
          }}
        >
          Đổi video
        </button>
      )}

      <Field
        label="Nhận dạng"
        icon={<IconMic size={14} />}
        hint={
          settings.engine === 'paddleocr'
            ? 'Đọc chữ phụ đề trên khung hình (OCR).'
            : 'Nhận dạng lời nói (Faster-Whisper).'
        }
      >
        <select
          value={settings.engine}
          disabled={busy}
          onChange={(e) => set('engine', e.target.value as ProjectSettings['engine'])}
        >
          <option value="whisper">Giọng nói (Whisper)</option>
          <option value="paddleocr">Chữ trên màn (OCR)</option>
        </select>
      </Field>

      <div className="field-row">
        <Field label="Ngôn ngữ gốc" icon={<IconGlobe size={14} />}>
          <select
            value={settings.sourceLang}
            disabled={busy}
            onChange={(e) => set('sourceLang', e.target.value)}
          >
            <option value="auto">Tự động nhận diện</option>
            <option value="zh">Tiếng Trung</option>
            <option value="en">Tiếng Anh</option>
            <option value="ja">Tiếng Nhật</option>
            <option value="ko">Tiếng Hàn</option>
            <option value="vi">Tiếng Việt</option>
          </select>
        </Field>
        <Field
          label="Ngôn ngữ dịch"
          icon={<IconGlobe size={14} />}
          hint={
            settings.targetLang === 'none'
              ? 'Giữ nguyên chữ nguồn — không gọi máy dịch.'
              : undefined
          }
        >
          <select
            value={settings.targetLang}
            disabled={busy}
            onChange={(e) => set('targetLang', e.target.value)}
          >
            <option value="none">Không dịch</option>
            <option value="vi">Tiếng Việt</option>
            <option value="en">Tiếng Anh</option>
            <option value="zh">Tiếng Trung</option>
            <option value="ja">Tiếng Nhật</option>
            <option value="ko">Tiếng Hàn</option>
          </select>
        </Field>
      </div>

      <Field
        label="Công cụ dịch"
        icon={<IconTranslate size={14} />}
        hint={
          settings.translator === 'google'
            ? 'Google free — nhanh.'
            : settings.translator === 'mymemory'
              ? 'MyMemory free — không key (có quota IP).'
              : settings.translator === 'tiktok'
                ? 'TikTok translate free — không key.'
                : settings.translator === 'ollama'
                  ? 'Ollama local — cấu hình model trên máy.'
                  : 'Cấu hình API key tại Cấu hình → API dịch cloud.'
        }
      >
        <select
          value={settings.translator}
          disabled={busy || settings.targetLang === 'none'}
          onChange={(e) =>
            set('translator', e.target.value as ProjectSettings['translator'])
          }
        >
          <option value="google">Google Translate</option>
          <option value="mymemory">MyMemory</option>
          <option value="tiktok">TikTok Translate</option>
          <option value="ollama">Ollama (local)</option>
          <option value="openai">OpenAI</option>
          <option value="gemini">Gemini</option>
          <option value="deepseek">DeepSeek</option>
          <option value="openrouter">OpenRouter</option>
          <option value="grok">Grok (xAI)</option>
        </select>
      </Field>

      <div className="field-row">
        <Field label="Khớp thời lượng" icon={<IconClock size={14} />}>
          <select
            value={settings.matchDuration}
            disabled={busy}
            onChange={(e) =>
              set('matchDuration', e.target.value as ProjectSettings['matchDuration'])
            }
          >
            <option value="natural">Tự nhiên, rút gọn</option>
            <option value="stretch">Kéo giãn khớp đoạn</option>
            <option value="none">Giữ nguyên</option>
          </select>
        </Field>
        <Field label="Giọng mặc định" icon={<IconSpeaker size={14} />}>
          <select
            value={
              voices.some((v) => v.id === settings.defaultVoice)
                ? settings.defaultVoice
                : (voices[0]?.id ?? 'el:pNInz6obpgDQGcFmaJgB')
            }
            disabled={busy}
            onChange={(e) => set('defaultVoice', e.target.value)}
          >
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="field-row">
        <Field label="Phụ đề" icon={<IconLayers size={14} />}>
          <select
            value={
              settings.targetLang === 'none' || !settings.burnSubs
                ? 'none'
                : settings.coverHardsubs
                  ? 'cover'
                  : settings.captionPlacement === 'above'
                    ? 'above'
                    : 'below'
            }
            disabled={busy}
            onChange={(e) => {
              const v = e.target.value
              if (v === 'cover') {
                onSettings({
                  ...settings,
                  coverHardsubs: true,
                  burnSubs: true,
                })
              } else if (v === 'below') {
                onSettings({
                  ...settings,
                  coverHardsubs: false,
                  burnSubs: true,
                  captionPlacement: 'below',
                })
              } else if (v === 'above') {
                onSettings({
                  ...settings,
                  coverHardsubs: false,
                  burnSubs: true,
                  captionPlacement: 'above',
                })
              } else {
                onSettings({ ...settings, burnSubs: false })
              }
            }}
          >
            <option value="cover">Che chữ cũ + chèn bản dịch</option>
            <option value="below">Chèn bản dịch phía dưới</option>
            <option value="above">Chèn bản dịch phía trên</option>
            <option value="none">Không chèn chữ dịch</option>
          </select>
        </Field>
        <Field label="Cỡ chữ" icon={<IconType size={14} />}>
          <select
            value={String(settings.subtitleFontSize)}
            disabled={busy || !settings.burnSubs || settings.targetLang === 'none'}
            onChange={(e) => set('subtitleFontSize', Number(e.target.value))}
            title="Tự động sẽ chọn cỡ lớn nhất vừa từng nhãn, chữ dọc và câu ngang"
          >
            <option value="0">Tự động (khuyên dùng)</option>
            {fontSizeOptions.map((px) => (
              <option key={px} value={px}>
                {px} px
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="audio-filter">
        <label
          className="audio-filter-toggle"
          title="Bật để xử lý track gốc khi lồng tiếng / xuất (xóa lời Demucs, tắt lời…)"
        >
          <span className="field-label">
            <IconSpeaker size={14} />
            Lọc âm thanh gốc
          </span>
          <input
            type="checkbox"
            checked={settings.processOriginalAudio}
            disabled={busy}
            onChange={(e) => {
              const on = e.target.checked
              onSettings({
                ...settings,
                processOriginalAudio: on,
                originalAudioMode:
                  on && settings.originalAudioMode === 'original'
                    ? 'no_vocals'
                    : settings.originalAudioMode,
              })
            }}
          />
        </label>
        {settings.processOriginalAudio && (
          <>
            <div className="audio-filter-options" role="radiogroup" aria-label="Lọc âm thanh gốc">
              {(
                [
                  ['no_vocals', 'Xóa lời'],
                  ['vocals', 'Chỉ giữ lời'],
                  // ['original', 'Giữ âm gốc'],
                  // ['mute', 'Tắt âm gốc'],
                ] as const
              ).map(([value, label]) => (
                <label
                  key={value}
                  className={settings.originalAudioMode === value ? 'active' : ''}
                >
                  <input
                    type="radio"
                    name="original-audio-mode"
                    value={value}
                    checked={settings.originalAudioMode === value}
                    disabled={busy}
                    onChange={() => set('originalAudioMode', value)}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
            <label
              className="audio-volume"
              title="Âm lượng track gốc / nền sau lọc (0–100%)"
            >
              <span className="audio-volume-label">Âm lượng nền</span>
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={
                  settings.originalAudioMode === 'mute'
                    ? 0
                    : Math.max(0, Math.min(100, settings.originalAudioVolume ?? 100))
                }
                disabled={busy || settings.originalAudioMode === 'mute'}
                onChange={(e) =>
                  set('originalAudioVolume', Math.max(0, Math.min(100, Number(e.target.value) || 0)))
                }
              />
              <em className="audio-volume-pct">
                {settings.originalAudioMode === 'mute'
                  ? 0
                  : Math.max(0, Math.min(100, settings.originalAudioVolume ?? 100))}
                %
              </em>
            </label>
          </>
        )}
      </div>

      <div className="preview-run">
        <label
          className="workers-setting"
          title="Tự động tăng/giảm luồng theo CPU, RAM và GPU còn rảnh"
        >
          <span className="preview-run-label">Luồng</span>
          <select
            value={String(
              [0, 1, 2, 4, 6, 8, 12, 16].includes(settings.workers) ? settings.workers : 0,
            )}
            disabled={busy}
            onChange={(e) => set('workers', Number(e.target.value))}
          >
            <option value="0">Tự động</option>
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="4">4</option>
            <option value="6">6</option>
            <option value="8">8</option>
            <option value="12">12</option>
            <option value="16">16</option>
          </select>
        </label>
        <label
          className="preview-len"
          title="Chỉ dùng khi bấm Preview — Dịch cả video vẫn ra full"
        >
          <span className="preview-run-label">Preview(s)</span>
          <input
            type="number"
            min={5}
            max={600}
            step={1}
            value={previewDraft}
            disabled={busy}
            onChange={(e) => setPreviewDraft(e.target.value)}
            onBlur={commitPreviewSec}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
            }}
          />
        </label>
        <button
          type="button"
          className="secondary icon-only"
          disabled={busy || !videoUrl}
          onClick={onPreview}
          aria-label="Preview"
          title={`Dịch ${settings.previewSec > 0 ? settings.previewSec : 20}s đầu — Xuất bản cũng ${settings.previewSec > 0 ? settings.previewSec : 20}s`}
        >
          <IconPlay size={14} />
        </button>
      </div>

      <div className="run-actions">
        <button
          type="button"
          className="primary"
          disabled={busy || !videoUrl}
          onClick={onTranslateAll}
          title="Dịch cả video — Xuất bản sẽ ra full (không theo số Preview)"
        >
          {busy ? (
            'Đang xử lý…'
          ) : (
            <>
              <IconLangSwap size={16} />
              Dịch cả video
              <IconArrowRight size={16} />
            </>
          )}
        </button>
        {showCancel && (
          <button type="button" className="cancel" onClick={onCancel}>
            Huỷ
          </button>
        )}
      </div>
    </aside>
  )
}
