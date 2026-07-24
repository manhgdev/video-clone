import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import type { ProjectSettings } from '@/features/project/project.types'

type AnalysisRegion = { x: number; y: number; w: number; h: number }

const DEFAULT_ANALYSIS_REGION: AnalysisRegion = { x: 0.05, y: 0.55, w: 0.9, h: 0.28 }

function clampRegion(r: AnalysisRegion): AnalysisRegion {
  const x = Math.max(0, Math.min(0.95, r.x))
  const y = Math.max(0, Math.min(0.95, r.y))
  const w = Math.max(0.08, Math.min(1 - x, r.w))
  const h = Math.max(0.06, Math.min(1 - y, r.h))
  return { x, y, w, h }
}
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
  IconTrash,
  IconType,
} from '@/shared/components/Icons'
import './ProjectSidebar.css'

type Props = {
  videoUrl: string | null
  settings: ProjectSettings
  voices: { id: string; name: string }[]
  busy: boolean
  onSettings: (s: ProjectSettings) => void
  onUpload: (file: File) => void
  onTranslateAll: () => void
  /** previewSec = số giây từ ô Preview (đã commit draft) */
  onPreview: (previewSec: number) => void
  onCancel: () => void
  onClearCache?: (parts: string[]) => void
  clearingCache?: boolean
}

export const CACHE_CLEAR_OPTIONS: { id: string; label: string }[] = [
  { id: 'covers', label: 'Vùng che / bbox OCR' },
  { id: 'ocr', label: 'OCR cache' },
  { id: 'whisper', label: 'Whisper / ASR' },
  { id: 'subtitle', label: 'Subtitle + đoạn thoại' },
  { id: 'translation', label: 'Translation cache' },
  { id: 'audio', label: 'Audio extract' },
  { id: 'tts', label: 'TTS cache' },
  { id: 'preview', label: 'Preview cache' },
  { id: 'render', label: 'Render / xuất' },
  { id: 'temp', label: 'Temp files' },
  { id: 'backend', label: 'Backend cache' },
  { id: 'frontend', label: 'Frontend cache' },
  { id: 'jobs', label: 'Job xử lý tạm' },
]

const ALL_CACHE_PARTS = CACHE_CLEAR_OPTIONS.map((o) => o.id)

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
  onClearCache,
  clearingCache = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const previewShellRef = useRef<HTMLDivElement>(null)
  const [portrait, setPortrait] = useState(false)
  const [showCancel, setShowCancel] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearParts, setClearParts] = useState<string[]>(() => [...ALL_CACHE_PARTS])
  const [previewDraft, setPreviewDraft] = useState(
    String(settings.previewSec > 0 ? settings.previewSec : 20),
  )
  const regionDragRef = useRef<{
    mode: 'move' | 'nw' | 'ne' | 'sw' | 'se' | 'n' | 's' | 'e' | 'w'
    startX: number
    startY: number
    origin: AnalysisRegion
    boxW: number
    boxH: number
  } | null>(null)

  const showAnalysisRoi =
    Boolean(settings.stableCaptionLocate)
    && Boolean(videoUrl)
    && !busy

  const analysisRegion = clampRegion(
    settings.analysisRegion && typeof settings.analysisRegion === 'object'
      ? {
          x: Number(settings.analysisRegion.x) || DEFAULT_ANALYSIS_REGION.x,
          y: Number(settings.analysisRegion.y) || DEFAULT_ANALYSIS_REGION.y,
          w: Number(settings.analysisRegion.w) || DEFAULT_ANALYSIS_REGION.w,
          h: Number(settings.analysisRegion.h) || DEFAULT_ANALYSIS_REGION.h,
        }
      : DEFAULT_ANALYSIS_REGION,
  )

  useEffect(() => {
    if (!settings.stableCaptionLocate) return
    if (settings.analysisRegion) return
    // Lần đầu bật: gán vùng mặc định (dải hardsub giữa-dưới)
    onSettings({ ...settings, analysisRegion: { ...DEFAULT_ANALYSIS_REGION } })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only seed once when toggled on
  }, [settings.stableCaptionLocate])

  function beginRegionDrag(
    mode: NonNullable<typeof regionDragRef.current>['mode'],
    e: ReactPointerEvent,
  ) {
    e.preventDefault()
    e.stopPropagation()
    const shell = previewShellRef.current
    if (!shell || busy) return
    const rect = shell.getBoundingClientRect()
    regionDragRef.current = {
      mode,
      startX: e.clientX,
      startY: e.clientY,
      origin: { ...analysisRegion },
      boxW: Math.max(1, rect.width),
      boxH: Math.max(1, rect.height),
    }
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  }

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const drag = regionDragRef.current
      if (!drag) return
      const dx = (e.clientX - drag.startX) / drag.boxW
      const dy = (e.clientY - drag.startY) / drag.boxH
      let { x, y, w, h } = drag.origin
      if (drag.mode === 'move') {
        x += dx
        y += dy
      } else {
        if (drag.mode.includes('e')) w = drag.origin.w + dx
        if (drag.mode.includes('s')) h = drag.origin.h + dy
        if (drag.mode.includes('w')) {
          x = drag.origin.x + dx
          w = drag.origin.w - dx
        }
        if (drag.mode.includes('n')) {
          y = drag.origin.y + dy
          h = drag.origin.h - dy
        }
      }
      onSettings({ ...settings, analysisRegion: clampRegion({ x, y, w, h }) })
    }
    const onUp = () => {
      regionDragRef.current = null
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [onSettings, settings])

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

  /** Commit ô Preview → settings; trả về số giây đã chốt (dùng khi bấm Preview ngay). */
  const commitPreviewSec = (): number => {
    if (busy) {
      const cur = Math.max(5, Math.min(600, settings.previewSec > 0 ? settings.previewSec : 20))
      setPreviewDraft(String(cur))
      return cur
    }
    const value = Math.max(5, Math.min(600, Number(previewDraft) || 20))
    setPreviewDraft(String(value))
    if (value !== settings.previewSec) {
      onSettings({ ...settings, previewSec: value })
    }
    return value
  }

  const fontSizes = [16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96, 120]
  const fontSizeOptions = settings.subtitleFontSize === 0 || fontSizes.includes(settings.subtitleFontSize)
    ? fontSizes
    : [...fontSizes, settings.subtitleFontSize].sort((a, b) => a - b)

  return (
    <aside className={`sidebar${busy ? ' is-busy' : ''}`}>
      <div
        ref={previewShellRef}
        className={`preview${portrait ? ' portrait' : ''}${busy ? ' locked' : ''}${showAnalysisRoi ? ' has-roi' : ''}`}
        onClick={(e) => {
          // Busy: chỉ xem video, không chọn file mới
          if (busy) return
          // click vào controls video — đừng mở file picker
          if ((e.target as HTMLElement).tagName === 'VIDEO') return
          if ((e.target as HTMLElement).closest('.analysis-roi')) return
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
            key={videoUrl}
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
        {showAnalysisRoi && (
          <div
            className="analysis-roi"
            style={{
              left: `${analysisRegion.x * 100}%`,
              top: `${analysisRegion.y * 100}%`,
              width: `${analysisRegion.w * 100}%`,
              height: `${analysisRegion.h * 100}%`,
            }}
            onPointerDown={(e) => beginRegionDrag('move', e)}
            title="Kéo di chuyển — góc/cạnh để resize. OCR chỉ quét trong khung này."
          >
            <span className="analysis-roi-label">Vùng định vị chữ</span>
            {(['nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w'] as const).map((h) => (
              <i
                key={h}
                className={`analysis-roi-handle analysis-roi-handle-${h}`}
                onPointerDown={(e) => beginRegionDrag(h, e)}
              />
            ))}
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

      <div className="field-row">
        <Field
          label="Nhận dạng"
          icon={<IconMic size={14} />}
          hint={
            settings.engine === 'paddleocr'
              ? 'Đọc chữ phụ đề trên khung hình'
              : 'Faster-Whisper'
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
        <Field
          label="Công cụ dịch"
          icon={<IconTranslate size={14} />}
          hint={
            settings.translator === 'google'
              ? 'Google free — nhanh.'
              : settings.translator === 'mymemory'
                ? 'Free — không key (có quota IP)'
                : settings.translator === 'tiktok'
                  ? 'TikTok translate free — không key.'
                  : settings.translator === 'ollama'
                    ? 'Cấu hình model trên máy'
                    : 'Cấu hình API key tại Cấu hình'
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
      </div>

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
        >
          <select
            value={settings.targetLang}
            disabled={busy}
            onChange={(e) => set('targetLang', e.target.value)}
          >
            <option value="none">Không dịch (giữ chữ nguồn)</option>
            <option value="vi">Tiếng Việt</option>
            <option value="en">Tiếng Anh</option>
            <option value="zh">Tiếng Trung</option>
            <option value="ja">Tiếng Nhật</option>
            <option value="ko">Tiếng Hàn</option>
          </select>
        </Field>
      </div>

      <div className="field-row">
        <Field label="Khớp thời lượng" icon={<IconClock size={14} />}>
          <select
            value={settings.matchDuration}
            disabled={busy}
            title={
              settings.matchDuration === 'preferVideo'
                ? 'Chậm video 0.80× nếu TTS dài hơn'
                : settings.matchDuration === 'none'
                  ? 'Giữ TTS nguyên tốc độ'
                  : settings.matchDuration === 'stretch'
                    ? 'Ép TTS đúng khung gốc (nhanh/chậm)'
                    : 'TTS dài hơn khung → tăng tốc nhẹ (≤1.25×)'
            }
            onChange={(e) =>
              set('matchDuration', e.target.value as ProjectSettings['matchDuration'])
            }
          >
            <option value="preferVideo">Ưu tiên chậm video 0.80× (trước ASR)</option>
            <option value="none">Giữ nguyên TTS</option>
            <option value="natural">Tự nhiên, rút gọn nhẹ</option>
            <option value="stretch">Kéo giãn khớp đoạn</option>
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
          title="Bật: OCR 3 frame/mốc (đầu•giữa•cuối) + majority — ổn định hơn, chậm hơn. Tắt: 1 frame — nhanh, dễ nhảy vị trí."
        >
          <span className="field-label">
            <IconLayers size={14} />
            Khung định vị ổn định (chỉ dịch chữ trong khung này)
          </span>
          <input
            type="checkbox"
            checked={Boolean(settings.stableCaptionLocate)}
            disabled={busy}
            onChange={(e) => {
              if (busy) return
              const on = e.target.checked
              onSettings({
                ...settings,
                stableCaptionLocate: on,
                analysisRegion: on
                  ? (settings.analysisRegion || { ...DEFAULT_ANALYSIS_REGION })
                  : settings.analysisRegion,
              })
            }}
          />
        </label>
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
                    : Math.max(0, Math.min(200, settings.originalAudioVolume ?? 100))
                }
                disabled={busy || settings.originalAudioMode === 'mute'}
                onChange={(e) =>
                  set('originalAudioVolume', Math.max(0, Math.min(200, Number(e.target.value) || 0)))
                }
              />
              <em className="audio-volume-pct">
                {settings.originalAudioMode === 'mute'
                  ? 0
                  : Math.max(0, Math.min(200, settings.originalAudioVolume ?? 100))}
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
          onClick={() => {
            // Commit draft trước — đổi 5→10 rồi bấm ngay vẫn dùng 10s
            const sec = commitPreviewSec()
            onPreview(sec)
          }}
          aria-label="Preview"
          title={`Dịch ${previewDraft || settings.previewSec || 20}s đầu (ô Preview) — Xuất cũng theo cửa sổ này`}
        >
          <IconPlay size={14} />
        </button>
      </div>

      <div className="run-actions">
        <button
          type="button"
          className="clear-cache-btn"
          disabled={!videoUrl || busy || clearingCache || !onClearCache}
          onClick={() => setConfirmClear(true)}
          title="Xóa toàn bộ cache dự án (giữ video nguồn)"
        >
          <IconTrash size={14} />
          {clearingCache ? 'Đang xóa…' : 'Xóa cache'}
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || !videoUrl || clearingCache}
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

      {confirmClear && (
        <div
          className="clear-cache-modal-backdrop"
          role="presentation"
        >
          <div
            className="clear-cache-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="clear-cache-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="clear-cache-title">Xóa cache dự án</h3>
            <p>Chọn mục cần xóa. Video nguồn không bao giờ bị xóa.</p>
            <div className="clear-cache-toolbar">
              <button
                type="button"
                className="clear-cache-link"
                disabled={clearingCache}
                onClick={() => setClearParts([...ALL_CACHE_PARTS])}
              >
                Chọn tất cả
              </button>
              <button
                type="button"
                className="clear-cache-link"
                disabled={clearingCache}
                onClick={() => setClearParts([])}
              >
                Bỏ chọn
              </button>
            </div>
            <div className="clear-cache-checks">
              {CACHE_CLEAR_OPTIONS.map((opt) => {
                const on = clearParts.includes(opt.id)
                return (
                  <label key={opt.id} className="clear-cache-check">
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={clearingCache}
                      onChange={() =>
                        setClearParts((prev) =>
                          on ? prev.filter((id) => id !== opt.id) : [...prev, opt.id],
                        )
                      }
                    />
                    <span>{opt.label}</span>
                  </label>
                )
              })}
            </div>
            <p className="clear-cache-note">Không xóa: video nguồn · settings dự án</p>
            <div className="clear-cache-modal-actions">
              <button
                type="button"
                className="secondary"
                disabled={clearingCache}
                onClick={() => setConfirmClear(false)}
              >
                Hủy
              </button>
              <button
                type="button"
                className="clear-cache-confirm"
                disabled={clearingCache || clearParts.length === 0}
                onClick={() => {
                  onClearCache?.(clearParts)
                  setConfirmClear(false)
                }}
              >
                {clearParts.length === ALL_CACHE_PARTS.length
                  ? 'Xóa tất cả'
                  : `Xóa đã chọn (${clearParts.length})`}
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
