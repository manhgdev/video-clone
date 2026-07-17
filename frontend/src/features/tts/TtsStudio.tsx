import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/features/project/project.api'
import ProgressPopup from '@/shared/components/ProgressPopup'
import { IconHeadphones, IconMic, IconSpeaker } from '@/shared/components/Icons'
import {
  type TtsEngine,
  type TtsOutputFormat,
  loadTtsSettings,
  persistTtsSettings,
} from './ttsSettings'
import VoiceMetadataModal, {
  VOICE_TAGS,
  VoiceTagPicker,
  canonicalVoiceTags,
  type VoiceTagLabel,
} from './VoiceMetadataModal'
import DashPanel from './DashPanel'
import {
  DEFAULT_DASH_LAYOUT,
  loadDashLayout,
  persistDashLayout,
  type DashId,
  type DashLayout,
} from './ttsDashboardLayout'
import './TtsStudio.css'

type Voice = {
  id: string
  name: string
  type?: string
  engine?: string
  mode?: string
  available?: boolean
  previewUrl?: string
  description?: string
  gender?: string
  language?: string
  accent?: string
  age?: string
  style?: string
  category?: string
  tags?: string[]
}
type EngineStatus = {
  id?: string
  name?: string
  local?: boolean
  installed?: boolean
  ready?: boolean
  loaded?: boolean
  loadState?: string
  device?: string
  model?: string
  version?: string
  message?: string
  presetCount?: number
  installHint?: string
  cloneRequiresPytorch?: boolean
}
type HistoryItem = {
  id: string
  title?: string
  voice?: string
  voiceName?: string
  engine?: string
  duration?: number
  createdAt?: string
  audioUrl?: string
  mp3Url?: string
  srtUrl?: string
  zipUrl?: string
  text?: string
}
type Props = {
  voices: Voice[]
  onRefreshVoices?: (lang?: string) => void
}
type SrtStyle = 'hard' | 'v916' | 'h169' | 'clause' | 'sentence'
const SRT_STYLE_OPTIONS: { id: SrtStyle; label: string }[] = [
  { id: 'hard', label: 'Cue ngắn (mặc định)' },
  { id: 'v916', label: 'Video dọc 9:16' },
  { id: 'h169', label: 'Video ngang 16:9' },
  { id: 'clause', label: 'Ngắt câu ngắn' },
  { id: 'sentence', label: 'Ngắt câu hợp lý' },
]

function fmtDur(sec = 0) {
  const s = Math.max(0, Math.floor(sec))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

function engineOf(voiceId: string) {
  if (voiceId.startsWith('vn:clone:')) return 'Clone'
  if (voiceId.startsWith('vn:')) return 'VieNeu Local'
  if (voiceId.startsWith('cc:')) return 'CapCut TTS'
  if (voiceId.startsWith('el:')) return 'ElevenLabs'
  return 'System'
}

/** Bucket rõ ràng — clone không lẫn VieNeu Local / zmAI. */
function voiceEngineBucket(v: Voice): TtsEngine {
  if (v.type === 'zmAI' || v.engine === 'zmai') return 'zmai'
  if (v.type === 'clone' || v.engine === 'clone' || v.id.startsWith('vn:clone:')) return 'clone'
  if (v.engine === 'capcut' || v.id.startsWith('cc:')) return 'capcut'
  if (v.id.startsWith('el:')) return 'eleven'
  if (v.type === 'preset' || v.engine === 'vieneu' || v.id.startsWith('vn:')) return 'vieneu'
  return 'system'
}

type VoiceTag = { label: string; kind: 'source' | 'gender' | 'accent' | 'category' | 'language' | 'editable' }

const LANGUAGE_NAMES: Record<string, string> = {
  vi: 'Tiếng Việt',
  en: 'Tiếng Anh',
  zh: 'Tiếng Trung',
  ja: 'Tiếng Nhật',
  ko: 'Tiếng Hàn',
  th: 'Tiếng Thái',
  id: 'Tiếng Indonesia',
  es: 'Tiếng Tây Ban Nha',
  fr: 'Tiếng Pháp',
  de: 'Tiếng Đức',
  pt: 'Tiếng Bồ Đào Nha',
}
const VOICE_TRAIT_NAMES: Record<string, string> = {
  male: '👨 Nam',
  female: '👩 Nữ',
  bac: '🏔️ Miền Bắc',
  bắc: '🏔️ Miền Bắc',
  nam: '🌴 Miền Nam',
  tu_nhien: 'Tự nhiên',
  tin_tuc: '📰 Tin tức',
  doc_truyen: 'Kể chuyện',
  doc_tho: '📜 Đọc thơ',
  quang_cao: '📢 Quảng cáo',
  tre_em: '👶 Trẻ em',
  young: '👶 Trẻ em',
  old: '👴 Người già',
  elderly: '👴 Người già',
  review: '⭐ Review',
}
function voiceMetadata(v: Voice): { description: string; tags: VoiceTag[] } {
  const bucket = voiceEngineBucket(v)
  const source =
    bucket === 'zmai' ? 'zmAI' :
      bucket === 'vieneu' ? 'VieNeu Local' :
        bucket === 'clone' ? 'Clone' :
          bucket === 'capcut' ? 'CapCut' :
            bucket === 'eleven' ? 'ElevenLabs' : 'Hệ thống'
  const description = v.description?.trim() ||
    (bucket === 'clone' ? 'Giọng tùy chỉnh được tạo từ audio mẫu.' :
      bucket === 'zmai' ? 'Giọng tham chiếu cục bộ của zmAI.' :
        bucket === 'system' ? 'Giọng hệ thống theo ngôn ngữ đích.' :
          `Giọng từ ${source}.`)
  const tags: VoiceTag[] = [{ label: source, kind: 'source' }]
  const editableTags = canonicalVoiceTags(v.tags)
  if (editableTags.length) {
    tags.push(...editableTags.map((label) => ({ label, kind: 'editable' as const })))
    return { description, tags }
  }
  const add = (raw: string | undefined, kind: VoiceTag['kind'], map = true) => {
    const value = raw?.trim()
    if (!value) return
    const key = value.toLowerCase().replace(/\s+/g, '_')
    const label = map ? (VOICE_TRAIT_NAMES[key] || value) : value
    if (!tags.some((tag) => tag.label.toLowerCase() === label.toLowerCase())) tags.push({ label, kind })
  }
  add(v.gender, 'gender')
  add(v.accent, 'accent')
  add(v.style, 'category')
  add(v.category, 'category')
  add(v.age, 'category')
  const languageCode = v.language?.toLowerCase().split(/[-_]/)[0]
  if (languageCode) add(LANGUAGE_NAMES[languageCode] || v.language, 'language', false)
  return { description, tags }
}

function engineLabel(engine?: string, voiceId?: string) {
  const e = (engine || '').toLowerCase()
  if (e === 'vieneu' || e === 'vn') return voiceId?.startsWith('vn:clone:') ? 'Clone' : 'VieNeu Local'
  if (e === 'capcut' || e === 'cc') return 'CapCut TTS'
  if (e === 'elevenlabs' || e === 'eleven' || e === 'el') return 'ElevenLabs'
  if (e === 'system') return 'System'
  if (voiceId) return engineOf(voiceId)
  return engine || '—'
}

/** Bỏ prefix engine lặp (VieNeu · Clone · …) — kể cả đã stack nhiều lần. */
function stripEngineNamePrefix(name: string): string {
  let s = name.trim().replace(/\s+/g, ' ')
  const re =
    /^(?:VieNeu\s*[·•.\-]\s*(?:Clone\s*[·•.\-]\s*)?|CapCut\s*[·•.\-]\s*|ElevenLabs\s*[·•.\-]\s*|macOS\s*[·•.\-]\s*)+/i
  for (let i = 0; i < 8; i++) {
    const next = s.replace(re, '').replace(/^[·•.\-\s]+/, '').trim()
    if (next === s) break
    s = next
  }
  return s
}

/** Tên hiển thị: meta.voiceName → list voices → fallback id */
function voiceDisplayName(
  voiceId: string | undefined,
  voices: Voice[],
  voiceName?: string,
): string {
  if (voiceName?.trim()) {
    const cleaned = stripEngineNamePrefix(voiceName)
    if (cleaned) return cleaned
  }
  if (!voiceId) return '—'
  const hit = voices.find((v) => v.id === voiceId)
  if (hit?.name) {
    const cleaned = stripEngineNamePrefix(hit.name)
    if (cleaned) return cleaned
  }
  if (voiceId.startsWith('cc:')) {
    // cc:voice_type:resource_id → voice_type dễ đọc hơn rid
    const rest = voiceId.slice(3)
    const type = rest.includes(':') ? rest.slice(0, rest.lastIndexOf(':')) : rest
    return type || rest
  }
  return voiceId.replace(/^vn:clone:/, '').replace(/^vn:|^el:/, '')
}

function IconFile({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
    </svg>
  )
}
function IconList({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
    </svg>
  )
}
function IconClock({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </svg>
  )
}
function IconUsers({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  )
}
function IconClone({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  )
}
function IconGear({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
    </svg>
  )
}
function IconUpload({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
    </svg>
  )
}
function IconPlay({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
  )
}
function IconPause({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <path d="M6 5h4v14H6zM14 5h4v14h-4z" />
    </svg>
  )
}
function IconDownload({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
    </svg>
  )
}
function IconTrash({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
    </svg>
  )
}
function IconHelp({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01" />
    </svg>
  )
}
function IconKb({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8" />
    </svg>
  )
}

const WAVE_BARS = Array.from({ length: 180 }, (_, i) => {
  const t = i / 180
  const taper = 0.5 + Math.sin(Math.PI * Math.min(1, t * 1.15)) * 0.5
  return 3 + (Math.abs(Math.sin(t * 43)) * 12 + Math.abs(Math.cos(t * 91)) * 5) * taper
})

/** Placeholder / fallback «Nghe thử» theo ngôn ngữ — ô nhập mặc định trống. */
const PREVIEW_SAMPLES: Record<string, string> = {
  vi: 'Xin chào, đây là giọng thử của Text to Speech Studio.',
  en: 'Hello, this is a sample voice preview from Text to Speech Studio.',
  zh: '你好，这是语音试听示例。',
  ja: 'こんにちは。これは音声プレビューのサンプルです。',
  ko: '안녕하세요. 이것은 음성 미리듣기 샘플입니다.',
  th: 'สวัสดี นี่คือตัวอย่างเสียงทดลองฟัง',
  id: 'Halo, ini adalah contoh pratinjau suara.',
  es: 'Hola, esta es una muestra de vista previa de voz.',
  fr: 'Bonjour, ceci est un aperçu vocal d’exemple.',
  de: 'Hallo, dies ist eine Beispiel-Stimmvorschau.',
  pt: 'Olá, esta é uma amostra de prévia de voz.',
}

const HISTORY_PAGE_SIZE = 10
const HISTORY_MAX = 50

function previewSampleFor(lang: string): string {
  return PREVIEW_SAMPLES[lang] || PREVIEW_SAMPLES.vi
}

/** Full dashboard (1–7): Tổng quan + Tạo giọng nói */
const FULL_DASHBOARD = new Set(['overview', 'make'])
/** Chưa làm / UI tạm → “Sắp ra mắt…” */
const COMING_SOON = new Set(['engines', 'audio', 'match', 'advanced'])

const SECTION_LABELS: Record<string, string> = {
  overview: 'Tổng quan',
  input: 'Nhập văn bản',
  srt: 'Nhập SRT / Phụ đề',
  make: 'Tạo giọng nói',
  history: 'Lịch sử tạo',
  voice: 'Danh sách giọng',
  clone: 'Clone giọng nói',
  engines: 'TTS Engines',
  audio: 'Cấu hình âm thanh',
  match: 'Khớp thời lượng',
  advanced: 'Tùy chọn nâng cao',
}

export default function TtsStudio({ voices, onRefreshVoices }: Props) {
  const savedRef = useRef(loadTtsSettings())
  const saved = savedRef.current
  /** Preferred voice across async voice-list loads — avoids wiping restored selection. */
  const preferredVoiceRef = useRef(saved.voice)

  const [section, setSection] = useState('overview')
  const [text, setText] = useState('')
  const [lang, setLang] = useState(saved.lang)
  const [engine, setEngine] = useState<TtsEngine>(saved.engine)
  const [voice, setVoice] = useState(saved.voice)
  const [style, setStyle] = useState(saved.style)
  const [speed, setSpeed] = useState(saved.speed)
  const [volume, setVolume] = useState(saved.volume)
  const [pitch, setPitch] = useState(saved.pitch)
  const [matchSrt, setMatchSrt] = useState(saved.matchSrt)
  const [keepTimeline, setKeepTimeline] = useState(saved.keepTimeline)
  const [normalize, setNormalize] = useState(saved.normalize)
  const [gapOn, setGapOn] = useState(saved.gapOn)
  const [gapMs, setGapMs] = useState(saved.gapMs)
  const [trimSilence, setTrimSilence] = useState(saved.trimSilence)
  const [autoSplit, setAutoSplit] = useState(saved.autoSplit)
  const [outputFormat, setOutputFormat] = useState<TtsOutputFormat>(saved.outputFormat)
  const [busy, setBusy] = useState(false)
  const [busyKind, setBusyKind] = useState<'synth' | 'preview' | 'clone' | null>(null)
  const [busyProgress, setBusyProgress] = useState(0)
  const [progressMinimized, setProgressMinimized] = useState(false)
  const [error, setError] = useState('')
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [mp3Url, setMp3Url] = useState<string | null>(null)
  // ponytail: SRT/ZIP URLs are derived from jobId when downloading.
  const [jobId, setJobId] = useState<string | null>(null)
  const [duration, setDuration] = useState(0)
  const [playbackTime, setPlaybackTime] = useState(0)
  const [playbackDuration, setPlaybackDuration] = useState(0)
  const [playbackVolume, setPlaybackVolume] = useState(saved.playbackVolume)
  const [isPlaying, setIsPlaying] = useState(false)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [historyPage, setHistoryPage] = useState(1)
  /** Menu chọn định dạng tải trong cột Hành động */
  const [downloadMenuId, setDownloadMenuId] = useState<string | null>(null)
  const [historySrtMenuId, setHistorySrtMenuId] = useState<string | null>(null)
  const [mainSrtMenuOpen, setMainSrtMenuOpen] = useState(false)
  const [status, setStatus] = useState<Record<string, EngineStatus>>({})
  const [cloneName, setCloneName] = useState('')
  const [cloneText, setCloneText] = useState('')
  const [cloneFile, setCloneFile] = useState<File | null>(null)
  const [cloneTags, setCloneTags] = useState<VoiceTagLabel[]>([])
  const [previewSample, setPreviewSample] = useState('')
  const [srtRaw, setSrtRaw] = useState('')
  const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null)
  const [selectedVoiceIds, setSelectedVoiceIds] = useState<Set<string>>(() => new Set())
  const [bulkMoveOpen, setBulkMoveOpen] = useState(false)
  const [voiceQuery, setVoiceQuery] = useState('')
  const [voiceTag, setVoiceTag] = useState('')
  const [editingVoice, setEditingVoice] = useState<Voice | null>(null)
  const [dashLayout, setDashLayout] = useState<DashLayout>(() => loadDashLayout())
  const [dashActive, setDashActive] = useState<DashId | null>(null)
  const dashRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const srtRef = useRef<HTMLInputElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const voicePreviewRef = useRef<HTMLAudioElement | null>(null)

  const sortedVoices = useMemo(() => {
    const pref = [...voices]
    pref.sort((a, b) => {
      const score = (v: Voice) => {
        const bkt = voiceEngineBucket(v)
        if (bkt === 'zmai' || bkt === 'vieneu' || bkt === 'clone') return 0
        if (bkt === 'capcut') return 1
        if (bkt === 'eleven') return 2
        return 3
      }
      return score(a) - score(b) || a.name.localeCompare(b.name, 'vi')
    })
    return pref
  }, [voices])

  /** Chỉ giọng thuộc Engine đang chọn */
  const engineVoices = useMemo(
    () => sortedVoices.filter((v) => voiceEngineBucket(v) === engine),
    [sortedVoices, engine],
  )
  const voiceFilterTags: readonly string[] = VOICE_TAGS
  const activeVoiceTag = voiceFilterTags.includes(voiceTag) ? voiceTag : ''
  const visibleEngineVoices = useMemo(() => {
    const query = voiceQuery.trim().toLocaleLowerCase('vi')
    return engineVoices.filter((v) => {
      const metadata = voiceMetadata(v)
      const matchesTag = !activeVoiceTag || metadata.tags.some((tag) => tag.label === activeVoiceTag)
      const matchesQuery = !query || [v.name, metadata.description, ...metadata.tags.map((tag) => tag.label)]
        .join(' ')
        .toLocaleLowerCase('vi')
        .includes(query)
      return matchesTag && matchesQuery
    })
  }, [activeVoiceTag, engineVoices, voiceQuery])
  const canBulkManage = engine === 'zmai' || engine === 'clone'
  const selectedVoiceCount = canBulkManage
    ? engineVoices.filter((v) => selectedVoiceIds.has(v.id)).length
    : 0
  const allEngineVoicesSelected =
    canBulkManage && engineVoices.length > 0 && selectedVoiceCount === engineVoices.length
  const bulkMoveTarget: 'zmai' | 'clone' = engine === 'zmai' ? 'clone' : 'zmai'
  const bulkMoveSourceLabel = engine === 'clone' ? 'Clone' : 'zmAI'
  const bulkMoveTargetLabel = bulkMoveTarget === 'clone' ? 'Clone' : 'zmAI'

  const cloneCount = useMemo(
    () => sortedVoices.filter((v) => voiceEngineBucket(v) === 'clone').length,
    [sortedVoices],
  )
  const selectedVoice = useMemo(() => voices.find((v) => v.id === voice), [voices, voice])
  const isVieneuVoice = selectedVoice ? voiceEngineBucket(selectedVoice) === 'vieneu' : false

  const historyCapped = useMemo(() => history.slice(0, HISTORY_MAX), [history])
  const historyTotalPages = Math.max(1, Math.ceil(historyCapped.length / HISTORY_PAGE_SIZE) || 1)
  const historyPageSafe = Math.min(Math.max(1, historyPage), historyTotalPages)
  const historyPageItems = useMemo(() => {
    const start = (historyPageSafe - 1) * HISTORY_PAGE_SIZE
    return historyCapped.slice(start, start + HISTORY_PAGE_SIZE)
  }, [historyCapped, historyPageSafe])
  const historyOffset = (historyPageSafe - 1) * HISTORY_PAGE_SIZE

  useEffect(() => {
    if (historyPage > historyTotalPages) setHistoryPage(historyTotalPages)
  }, [historyPage, historyTotalPages])

  useEffect(() => {
    setSelectedVoiceIds(new Set())
  }, [engine, lang])

  useEffect(() => {
    // Keep restored/preferred voice until the async list arrives; only then fall back.
    if (voice && engineVoices.some((v) => v.id === voice)) {
      preferredVoiceRef.current = voice
      return
    }
    if (!engineVoices.length) {
      // App seeds eleven+system before /api/voices returns — don't wipe restored vn:/cc: ids.
      const looksSeed =
        voices.length === 0 ||
        (voices.length <= 3 &&
          voices.every((v) => {
            const b = voiceEngineBucket(v)
            return b === 'eleven' || b === 'system'
          }))
      if (!looksSeed && voice) setVoice('')
      return
    }
    const preferred = preferredVoiceRef.current
    if (preferred && engineVoices.some((v) => v.id === preferred)) {
      if (voice !== preferred) setVoice(preferred)
      return
    }
    const fallback = engineVoices[0].id
    preferredVoiceRef.current = fallback
    setVoice(fallback)
  }, [engineVoices, voice, lang, engine, voices])

  useEffect(() => {
    persistTtsSettings({
      lang,
      engine,
      voice,
      style,
      speed,
      volume,
      pitch,
      matchSrt,
      keepTimeline,
      normalize,
      gapOn,
      gapMs,
      trimSilence,
      autoSplit,
      playbackVolume,
      outputFormat,
    })
  }, [
    lang,
    engine,
    voice,
    style,
    speed,
    volume,
    pitch,
    matchSrt,
    keepTimeline,
    normalize,
    gapOn,
    gapMs,
    trimSilence,
    autoSplit,
    playbackVolume,
    outputFormat,
  ])

  useEffect(() => {
    persistDashLayout(dashLayout)
  }, [dashLayout])

  const setDashLayoutSafe = useCallback((next: DashLayout | ((prev: DashLayout) => DashLayout)) => {
    setDashLayout(next)
  }, [])

  useEffect(() => {
    onRefreshVoices?.(lang)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang])

  // Progress giả lập khi chờ API TTS (server chưa stream %)
  useEffect(() => {
    if (!busy) {
      setBusyProgress(0)
      return
    }
    setBusyProgress(4)
    setProgressMinimized(false)
    const id = window.setInterval(() => {
      setBusyProgress((p) => {
        if (p >= 92) return p
        // chậm dần khi gần xong
        const step = p < 40 ? 3.5 : p < 70 ? 2 : 0.8
        return Math.min(92, p + step)
      })
    }, 400)
    return () => window.clearInterval(id)
  }, [busy])

  useEffect(() => {
    setPlaybackTime(0)
    setPlaybackDuration(audioUrl ? duration : 0)
    setIsPlaying(false)
  }, [audioUrl, duration])

  useEffect(() => () => {
    voicePreviewRef.current?.pause()
    voicePreviewRef.current = null
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.ttsStatus())
    } catch {
      /* ignore */
    }
  }, [])

  const loadHistory = useCallback(async () => {
    try {
      const rows = await api.ttsStudioHistory()
      setHistory(
        rows.slice(0, HISTORY_MAX).map((r) => ({
          id: String(r.id || ''),
          title: String(r.title || ''),
          voice: String(r.voice || ''),
          voiceName: r.voiceName ? String(r.voiceName) : undefined,
          engine: String(r.engine || ''),
          duration: Number(r.duration || 0),
          createdAt: String(r.createdAt || ''),
          audioUrl: String(r.audioUrl || ''),
          mp3Url: r.mp3Url ? String(r.mp3Url) : undefined,
          srtUrl: r.srtUrl ? String(r.srtUrl) : undefined,
          zipUrl: r.zipUrl ? String(r.zipUrl) : undefined,
          text: String(r.text || ''),
        })),
      )
      setHistoryPage(1)
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    void loadStatus()
    void loadHistory()
  }, [loadStatus, loadHistory])

  const vieneu = status.vieneu

  function go(id: string) {
    setSection(id)
    // Full dashboard: không scroll; tab riêng: hiện 1 panel
  }

  const isFullDash = FULL_DASHBOARD.has(section)
  const showComingSoon = COMING_SOON.has(section)

  function applyJobUrls(res: {
    id: string
    duration: number
    audioUrl: string
    mp3Url?: string
  }) {
    const t = Date.now()
    setJobId(res.id)
    // stream inline (không ?download=) — tránh trình duyệt tải file khi <audio> play
    setAudioUrl(`${res.audioUrl}${res.audioUrl.includes('?') ? '&' : '?'}t=${t}`)
    const mp3 = res.mp3Url || `/api/tts/studio/jobs/${res.id}/audio.mp3`
    setMp3Url(`${mp3}${mp3.includes('?') ? '&' : '?'}download=1&t=${t}`)
    setDuration(res.duration)
  }

  function downloadWavHref(url: string | null) {
    if (!url) return undefined
    const u = url.replace(/([?&])t=\d+/, '').replace(/\?$/, '')
    return `${u}${u.includes('?') ? '&' : '?'}download=1`
  }

  /** URL tải theo loại file cho 1 job lịch sử */
  function historyDownloadUrl(
    h: HistoryItem,
    kind: 'wav' | 'mp3' | 'srt' | 'zip',
    style: SrtStyle = 'hard',
  ): string | undefined {
    if (!h.id && !h.audioUrl) return undefined
    const jobIdFromUrl = h.audioUrl?.match(/\/jobs\/([^/]+)\//)?.[1]
    const id = h.id || jobIdFromUrl || ''
    if (!id) return undefined
    const t = Date.now()
    if (kind === 'wav') {
      const base = h.audioUrl || `/api/tts/studio/jobs/${id}/audio.wav`
      return downloadWavHref(base)
    }
    if (kind === 'mp3') {
      const base = h.mp3Url || `/api/tts/studio/jobs/${id}/audio.mp3`
      const clean = base.replace(/([?&])t=\d+/, '').replace(/([?&])download=1/, '')
      return `${clean}${clean.includes('?') ? '&' : '?'}download=1&t=${t}`
    }
    if (kind === 'srt') {
      return `/api/tts/studio/jobs/${id}/subs.srt?style=${style}&t=${t}`
    }
    return `/api/tts/studio/jobs/${id}/bundle.zip?style=${style}&t=${t}`
  }

  function triggerDownload(url: string | undefined, filename: string) {
    if (!url) return
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setDownloadMenuId(null)
    setHistorySrtMenuId(null)
    setMainSrtMenuOpen(false)
  }

  useEffect(() => {
    if (!downloadMenuId && !mainSrtMenuOpen) return
    const onDoc = (e: MouseEvent) => {
      const el = e.target as HTMLElement | null
      if (el?.closest?.('[data-dl-menu]')) return
      setDownloadMenuId(null)
      setHistorySrtMenuId(null)
      setMainSrtMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDownloadMenuId(null)
        setHistorySrtMenuId(null)
        setMainSrtMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [downloadMenuId, mainSrtMenuOpen])

  useEffect(() => {
    if (!bulkMoveOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) setBulkMoveOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [bulkMoveOpen, busy])

  /** Phát trong player / Audio() — không mở tab mới, không nhảy giao diện. */
  function playHistoryItem(h: HistoryItem) {
    if (!h.audioUrl) return
    const t = Date.now()
    const base = h.audioUrl.replace(/([?&])download=1/, '').replace(/([?&])t=\d+/, '')
    const inlineUrl = `${base}${base.includes('?') ? '&' : '?'}t=${t}`
    setJobId(h.id)
    setAudioUrl(inlineUrl)
    setDuration(h.duration || 0)
    if (h.mp3Url) setMp3Url(downloadWavHref(h.mp3Url) || null)
    // Dashboard: dùng <audio> panel 5; tab lịch sử: phát ẩn không đổi section
    if (isFullDash) {
      requestAnimationFrame(() => {
        const el = audioRef.current
        if (!el) return
        el.load()
        void el.play().catch(() => {})
      })
      return
    }
    try {
      const a = new Audio(inlineUrl)
      void a.play().catch(() => {})
    } catch {
      /* ignore */
    }
  }

  async function onSynth() {
    if ((!text.trim() && !srtRaw.trim()) || !voice) return
    setBusyKind('synth')
    setBusy(true)
    setError('')
    try {
      const res = await api.ttsStudioSynth({
        text: srtRaw.trim() ? undefined : text.trim(),
        srtText: srtRaw.trim() || undefined,
        voice,
        lang,
        speed,
        volume,
        pitch,
        style,
        matchDuration: matchSrt ? 'natural' : 'none',
        keepTimeline,
        autoSplit,
        gapMs: gapOn ? gapMs : 0,
        title: (text || srtRaw).trim().slice(0, 48),
      })
      setBusyProgress(100)
      applyJobUrls(res)
      // Cùng giọng + chữ + setting → server trả cache, không thêm lịch sử mới
      if (!(res as { cached?: boolean }).cached) await loadHistory()
      setTimeout(() => audioRef.current?.play().catch(() => {}), 80)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tạo giọng thất bại')
    } finally {
      setBusy(false)
      setBusyKind(null)
    }
  }

  async function onPreview() {
    const sample = previewSample.trim().slice(0, 200)
    if (!sample || !voice) return
    setBusyKind('preview')
    setBusy(true)
    setError('')
    try {
      const res = await api.ttsStudioSynth({
        text: sample,
        voice,
        lang,
        speed,
        volume,
        pitch,
        style,
        matchDuration: 'none',
        autoSplit: false,
        title: 'Nghe thử',
      })
      setBusyProgress(100)
      applyJobUrls(res)
      if (!(res as { cached?: boolean }).cached) await loadHistory()
      // Chỉ phát trong player — không mở tab / không download
      requestAnimationFrame(() => {
        const el = audioRef.current
        if (!el) return
        el.load()
        void el.play().catch(() => {
          /* autoplay policy — user bấm play trên controls */
        })
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Nghe thử thất bại')
    } finally {
      setBusy(false)
      setBusyKind(null)
    }
  }

  async function onCancelJob() {
    if (jobId) {
      try {
        await api.ttsStudioCancel(jobId)
      } catch {
        /* ignore */
      }
    }
    setBusy(false)
    setBusyKind(null)
    setBusyProgress(0)
    setError('Đã hủy')
  }

  async function onClone() {
    if (!cloneFile || !cloneName.trim()) {
      setError('Chọn file audio và nhập tên giọng clone')
      return
    }
    setBusyKind('clone')
    setBusy(true)
    setError('')
    try {
      const v = await api.ttsStudioClone(cloneName.trim(), cloneFile, cloneText.trim(), cloneTags)
      setBusyProgress(100)
      setEngine('clone')
      preferredVoiceRef.current = v.id
      setVoice(v.id)
      onRefreshVoices?.(lang)
      setCloneFile(null)
      setCloneName('')
      setCloneText('')
      setCloneTags([])
      go('voice') // danh sách / quản lý ở tab riêng
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Clone thất bại')
    } finally {
      setBusy(false)
      setBusyKind(null)
    }
  }

  async function onSaveVoiceMetadata(voiceId: string, name: string, tags: VoiceTagLabel[]) {
    setBusyKind('clone')
    setBusy(true)
    setError('')
    try {
      const v = await api.ttsStudioVoicePatch(voiceId, { name, tags })
      if (voice === voiceId) {
        preferredVoiceRef.current = v.id
        setVoice(v.id)
      }
      onRefreshVoices?.(lang)
      setEditingVoice(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Lưu thông tin giọng thất bại')
      throw e
    } finally {
      setBusy(false)
      setBusyKind(null)
    }
  }

  async function onDeleteVoice(voiceId: string) {
    const label = voiceDisplayName(voiceId, voices)
    const isClone = voiceId.startsWith('vn:clone:')
    if (!window.confirm(isClone ? `Xóa giọng clone «${label}»?` : `Ẩn / xóa giọng zmAI «${label}» khỏi danh sách?`)) return
    setBusyKind('clone')
    setBusy(true)
    setError('')
    try {
      await api.ttsStudioVoiceDelete(voiceId)
      if (voice === voiceId) {
        preferredVoiceRef.current = ''
        setVoice('')
      }
      onRefreshVoices?.(lang)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Xóa giọng thất bại')
    } finally {
      setBusy(false)
      setBusyKind(null)
    }
  }

  async function onMoveVoice(voiceId: string, target: 'zmai' | 'clone') {
    setBusyKind('clone')
    setBusy(true)
    setError('')
    try {
      const v = await api.ttsStudioVoicePatch(voiceId, { engine: target })
      if (voice === voiceId) {
        preferredVoiceRef.current = v.id
        setVoice(v.id)
        setEngine(target)
      }
      onRefreshVoices?.(lang)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Chuyển engine thất bại')
    } finally {
      setBusy(false)
      setBusyKind(null)
    }
  }

  function toggleBulkVoice(voiceId: string) {
    setSelectedVoiceIds((current) => {
      const next = new Set(current)
      if (next.has(voiceId)) next.delete(voiceId)
      else next.add(voiceId)
      return next
    })
  }

  function openBulkMoveModal() {
    if (!canBulkManage || busy || selectedVoiceCount === 0) return
    setBulkMoveOpen(true)
  }

  function closeBulkMoveModal() {
    if (busy) return
    setBulkMoveOpen(false)
  }

  async function confirmBulkMoveVoices() {
    if (!canBulkManage || busy) return
    const voiceIds = engineVoices.filter((v) => selectedVoiceIds.has(v.id)).map((v) => v.id)
    if (!voiceIds.length) {
      setBulkMoveOpen(false)
      return
    }
    const target = bulkMoveTarget

    setBusyKind('clone')
    setBusy(true)
    setError('')
    try {
      const result = await api.ttsStudioVoicesBulkMove(voiceIds, target)
      onRefreshVoices?.(lang)
      if (result.failures.length === 0) {
        setSelectedVoiceIds(new Set())
        setBulkMoveOpen(false)
        if (result.successes.length) {
          setEngine(target)
          preferredVoiceRef.current = result.successes[0].voice.id
          setVoice(result.successes[0].voice.id)
        }
        return
      }
      // Partial: keep failed selected on source so user can retry; switch only on full success.
      setSelectedVoiceIds(new Set(result.failures.map((item) => item.voiceId)))
      setBulkMoveOpen(false)
      const details = result.failures.map((item) => `${item.voiceId}: ${item.error}`).join('; ')
      setError(
        `Đã chuyển ${result.successes.length}/${voiceIds.length} giọng sang ${bulkMoveTargetLabel}. ` +
          `Thất bại ${result.failures.length} (vẫn chọn): ${details}`,
      )
    } catch (e) {
      // Preserve selection on hard API failure.
      setError(e instanceof Error ? e.message : 'Chuyển nhiều giọng thất bại')
      setBulkMoveOpen(false)
    } finally {
      setBusy(false)
      setBusyKind(null)
    }
  }

  function toggleVoicePreview(v: Voice) {
    const current = voicePreviewRef.current
    if (current && previewingVoiceId === v.id && !current.paused) {
      current.pause()
      setPreviewingVoiceId(null)
      return
    }
    current?.pause()
    audioRef.current?.pause()
    if (!v.previewUrl) return
    const player = new Audio(`${v.previewUrl}${v.previewUrl.includes('?') ? '&' : '?'}t=${Date.now()}`)
    voicePreviewRef.current = player
    setPreviewingVoiceId(v.id)
    const clear = () => {
      if (voicePreviewRef.current === player) {
        voicePreviewRef.current = null
        setPreviewingVoiceId(null)
      }
    }
    player.onended = clear
    player.onerror = () => {
      clear()
      setError('Không phát được audio mẫu của giọng này')
    }
    void player.play().catch(() => {
      clear()
      setError('Trình duyệt không cho phát audio mẫu')
    })
  }

  /** Danh sách giọng theo engine; zmAI + clone có sửa / xóa / chuyển bucket. */
  function renderVoiceList() {
    if (!visibleEngineVoices.length) {
      return (
        <p className="tts-clone-empty">
          {engine === 'clone' && !voiceQuery ? 'Chưa có giọng clone.' : 'Không có giọng phù hợp với bộ lọc này.'}
        </p>
      )
    }
    return (
      <ul className="tts-clone-list">
        {visibleEngineVoices.map((v) => {
          const voiceBucket = voiceEngineBucket(v)
          const isClone = voiceBucket === 'clone'
          const isZmai = voiceBucket === 'zmai'
          const canManage = isClone || isZmai
          const bucket: 'zmai' | 'clone' = isClone ? 'clone' : 'zmai'
          const metadata = voiceMetadata(v)
          return (
          <li
            key={v.id}
            className={`tts-clone-item${voice === v.id ? ' is-active' : ''}`}
            aria-current={voice === v.id ? 'true' : undefined}
          >
            {canManage && (
              <input
                type="checkbox"
                className="tts-voice-check"
                checked={selectedVoiceIds.has(v.id)}
                disabled={busy}
                aria-label={`Chọn ${voiceDisplayName(v.id, voices, v.name)}`}
                onChange={() => toggleBulkVoice(v.id)}
              />
            )}
            <button
              type="button"
              className="tts-clone-pick"
              title="Chọn giọng này"
              onClick={() => {
                preferredVoiceRef.current = v.id
                setVoice(v.id)
                go('overview')
              }}
            >
              <span className="tts-voice-copy">
                <strong>{voiceDisplayName(v.id, voices, v.name)}</strong>
                <span className="tts-voice-description">{metadata.description}</span>
                <span className="tts-voice-tags" aria-label="Thông tin giọng">
                  {metadata.tags.map((tag) => (
                    <span key={`${tag.kind}:${tag.label}`} className={`tts-voice-tag ${tag.kind}`}>
                      {tag.label}
                    </span>
                  ))}
                </span>
              </span>
            </button>
            <div className="tts-clone-actions">
              {v.previewUrl && (
                <button
                  type="button"
                  className="tts-btn-sm tts-btn-icon"
                  title={previewingVoiceId === v.id ? 'Dừng' : 'Phát audio mẫu có sẵn'}
                  aria-label={previewingVoiceId === v.id ? 'Dừng' : 'Nghe thử'}
                  onClick={() => toggleVoicePreview(v)}
                >
                  {previewingVoiceId === v.id ? <IconPause size={13} /> : <IconPlay size={13} />}
                </button>
              )}
              {canManage && (
                <>
                  <select
                    className="tts-voice-move"
                    value={bucket}
                    disabled={busy}
                    title="Chuyển sang engine khác"
                    aria-label="Chuyển engine"
                    onChange={(e) => {
                      const next = e.target.value as 'zmai' | 'clone'
                      if (next === bucket) return
                      void onMoveVoice(v.id, next)
                    }}
                  >
                    <option value="zmai">zmAI</option>
                    <option value="clone">Clone</option>
                  </select>
                  <button type="button" className="tts-btn-sm" disabled={busy} onClick={() => setEditingVoice(v)}>
                    Sửa
                  </button>
                  <button type="button" className="tts-btn-sm" disabled={busy} onClick={() => void onDeleteVoice(v.id)}>
                    Xóa
                  </button>
                </>
              )}
            </div>
          </li>
          )
        })}
      </ul>
    )
  }

  const busyTitle =
    busyKind === 'preview'
      ? 'Nghe thử giọng'
      : busyKind === 'clone'
        ? 'Clone giọng nói'
        : 'Tạo giọng nói'
  const busyMessage =
    busyKind === 'preview'
      ? isVieneuVoice
        ? 'Đang tổng hợp mẫu VieNeu…'
        : 'Đang tổng hợp mẫu…'
      : busyKind === 'clone'
        ? 'Đang tạo giọng clone…'
        : srtRaw.trim()
          ? 'Đang tạo giọng từ SRT…'
          : isVieneuVoice
            ? 'Đang tạo giọng VieNeu (lần đầu có thể nạp model)…'
            : 'Đang tạo giọng nói…'

  function renderHistoryBody() {
    return (
      <>
        <div className="tts-history-wrap">
          <table className="tts-history">
            <thead>
              <tr>
                <th>#</th>
                <th>Tiêu đề / tên</th>
                <th>Engine</th>
                <th>Giọng</th>
                <th>Thời lượng</th>
                <th>Ngày tạo</th>
                <th>File audio</th>
                <th>File SRT</th>
                <th>Trạng thái</th>
                <th>Hành động</th>
              </tr>
            </thead>
            <tbody>
              {historyPageItems.length === 0 && (
                <tr>
                  <td colSpan={10} className="tts-empty">Chưa có lịch sử — tạo giọng nói để bắt đầu</td>
                </tr>
              )}
              {historyPageItems.map((h, i) => (
                <tr key={h.id}>
                  <td>{historyOffset + i + 1}</td>
                  <td style={{ fontWeight: 600 }}>{h.title || h.id}</td>
                  <td>{engineLabel(h.engine, h.voice)}</td>
                  <td
                    style={{ color: 'var(--tts-muted)', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }}
                    title={voiceDisplayName(h.voice, voices, h.voiceName)}
                  >
                    {voiceDisplayName(h.voice, voices, h.voiceName)}
                  </td>
                  <td>{fmtDur(h.duration)}</td>
                  <td style={{ color: 'var(--tts-muted)', whiteSpace: 'nowrap' }}>{h.createdAt || '—'}</td>
                  <td>
                    {h.audioUrl ? (
                      <button
                        type="button"
                        className="link"
                        style={{ background: 'none', border: 0, cursor: 'pointer', padding: 0, font: 'inherit' }}
                        onClick={() => playHistoryItem(h)}
                      >
                        {(h.title || h.id).slice(0, 16)}.wav
                      </button>
                    ) : '—'}
                  </td>
                  <td style={{ color: 'var(--tts-muted)' }}>—</td>
                  <td className="tts-tag-ok">Hoàn thành</td>
                  <td>
                    <div className="tts-act" data-dl-menu>
                      {h.audioUrl && (
                        <button type="button" title="Nghe" onClick={() => playHistoryItem(h)}>
                          <IconPlay size={12} />
                        </button>
                      )}
                      {h.audioUrl && (
                        <div className="tts-dl-wrap">
                          <button
                            type="button"
                            title="Tải xuống — chọn định dạng"
                            className={downloadMenuId === h.id ? 'is-open' : undefined}
                            onClick={(e) => {
                              e.stopPropagation()
                              setDownloadMenuId((cur) => (cur === h.id ? null : h.id))
                              setHistorySrtMenuId(null)
                            }}
                          >
                            <IconDownload size={12} />
                          </button>
                          {downloadMenuId === h.id && (
                            <div className="tts-dl-menu" role="menu">
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() =>
                                  triggerDownload(
                                    historyDownloadUrl(h, 'wav'),
                                    `${(h.title || h.id).slice(0, 40)}.wav`,
                                  )
                                }
                              >
                                WAV
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() =>
                                  triggerDownload(
                                    historyDownloadUrl(h, 'mp3'),
                                    `${(h.title || h.id).slice(0, 40)}.mp3`,
                                  )
                                }
                              >
                                MP3
                              </button>
                              <div className="tts-dl-subwrap">
                                <button
                                  type="button"
                                  role="menuitem"
                                  aria-haspopup="menu"
                                  aria-expanded={historySrtMenuId === h.id}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setHistorySrtMenuId((cur) => (cur === h.id ? null : h.id))
                                  }}
                                >
                                  SRT CapCut <span className="tts-menu-arrow">‹</span>
                                </button>
                                {historySrtMenuId === h.id && (
                                  <div className="tts-dl-menu tts-dl-submenu" role="menu">
                                    {SRT_STYLE_OPTIONS.map((opt) => (
                                      <button
                                        key={opt.id}
                                        type="button"
                                        role="menuitem"
                                        onClick={() =>
                                          triggerDownload(
                                            historyDownloadUrl(h, 'srt', opt.id),
                                            `${(h.title || h.id).slice(0, 40)}-${opt.id}.srt`,
                                          )
                                        }
                                      >
                                        {opt.label}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() =>
                                  triggerDownload(
                                    historyDownloadUrl(h, 'zip'),
                                    `${(h.title || h.id).slice(0, 40)}.zip`,
                                  )
                                }
                              >
                                ZIP (Audio + SRT)
                              </button>
                            </div>
                          )}
                        </div>
                      )}
                      <button
                        type="button"
                        title="Xóa"
                        onClick={() => { void api.ttsStudioDelete(h.id).then(loadHistory) }}
                      >
                        <IconTrash size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {historyCapped.length > 0 && (
          <div className="tts-pager">
            <span className="tts-pager-info">
              {historyOffset + 1}–{Math.min(historyOffset + HISTORY_PAGE_SIZE, historyCapped.length)}
              {' / '}
              {historyCapped.length}
              {historyCapped.length >= HISTORY_MAX ? ` (tối đa ${HISTORY_MAX})` : ''}
            </span>
            <div className="tts-pager-btns">
              <button
                type="button"
                className="tts-btn tts-btn-ghost"
                disabled={historyPageSafe <= 1}
                onClick={() => setHistoryPage((p) => Math.max(1, p - 1))}
              >
                Trước
              </button>
              <span className="tts-pager-page">
                Trang {historyPageSafe}/{historyTotalPages}
              </span>
              <button
                type="button"
                className="tts-btn tts-btn-ghost"
                disabled={historyPageSafe >= historyTotalPages}
                onClick={() => setHistoryPage((p) => Math.min(historyTotalPages, p + 1))}
              >
                Sau
              </button>
            </div>
          </div>
        )}
      </>
    )
  }

  function onLoadTxt(file: File) {
    const reader = new FileReader()
    reader.onload = () => setText(String(reader.result || ''))
    reader.readAsText(file, 'utf-8')
  }

  function onLoadSrt(file: File) {
    const reader = new FileReader()
    reader.onload = () => {
      const raw = String(reader.result || '')
      setSrtRaw(raw)
      const lines = raw
        .split(/\r?\n/)
        .filter((ln) => ln.trim() && !/^\d+$/.test(ln.trim()) && !/-->/.test(ln))
      setText(lines.join('\n'))
    }
    reader.readAsText(file, 'utf-8')
  }

  return (
    <div className="tts-studio">
      {/* ── Left sidebar ── */}
      <aside className="tts-side">
        <div className="tts-side-body">
          <select
            className="tts-side-select"
            value={section === 'overview' ? 'overview' : section}
            onChange={(e) => go(e.target.value)}
          >
            <option value="overview">Tổng quan</option>
            <option value="input">Nhập văn bản</option>
            <option value="make">Tạo giọng nói</option>
            <option value="history">Lịch sử tạo</option>
            <option value="clone">Clone giọng nói</option>
          </select>

          <div className="tts-sec">Tạo giọng nói</div>
          <button type="button" className={`tts-nav${section === 'input' ? ' active' : ''}`} onClick={() => go('input')}>
            <IconFile /> Nhập văn bản
          </button>
          <button type="button" className={`tts-nav${section === 'srt' ? ' active' : ''}`} onClick={() => go('srt')}>
            <IconList /> Nhập SRT / Phụ đề
          </button>
          <button type="button" className={`tts-nav${section === 'make' || section === 'overview' ? ' active' : ''}`} onClick={() => go('overview')}>
            <IconMic size={14} /> Tạo giọng nói
          </button>
          <button type="button" className={`tts-nav${section === 'history' ? ' active' : ''}`} onClick={() => go('history')}>
            <IconClock /> Lịch sử tạo
          </button>

          <div className="tts-sec">Quản lý giọng</div>
          <button type="button" className={`tts-nav${section === 'voice' ? ' active' : ''}`} onClick={() => go('voice')}>
            <IconUsers /> Danh sách giọng
          </button>
          <button type="button" className={`tts-nav${section === 'clone' ? ' active' : ''}`} onClick={() => go('clone')}>
            <IconClone /> Clone giọng nói
            <span className="pill-new">Mới</span>
          </button>

          <div className="tts-sec">Cài đặt</div>
          <button type="button" className={`tts-nav${section === 'engines' ? ' active' : ''}`} onClick={() => go('engines')}>
            <IconGear /> TTS Engines
          </button>
          <button type="button" className={`tts-nav${section === 'audio' ? ' active' : ''}`} onClick={() => go('audio')}>
            <IconSpeaker size={14} /> Cấu hình âm thanh
          </button>
          <button type="button" className={`tts-nav${section === 'match' ? ' active' : ''}`} onClick={() => go('match')}>
            <IconClock /> Khớp thời lượng
          </button>
          <button type="button" className={`tts-nav${section === 'advanced' ? ' active' : ''}`} onClick={() => go('advanced')}>
            <IconList /> Tùy chọn nâng cao
          </button>
        </div>

        <div className="tts-engine-card">
          <div className="top">
            <h4>{vieneu?.name || 'VieNeu Local'}</h4>
            <span className="tts-pill-local">Local</span>
          </div>
          <div className="meta">
            <div className="meta-row">
              <span className="meta-lab">Trạng thái</span>
              <strong className={vieneu?.ready ? 'ok' : 'bad'}>
                {vieneu?.ready ? 'Sẵn sàng' : (vieneu?.message || 'Chưa cài').slice(0, 48)}
              </strong>
            </div>
            <div className="meta-row">
              <span className="meta-lab">Thiết bị</span>
              <span className="meta-val">{vieneu?.device || '—'}</span>
            </div>
            <div className="meta-row">
              <span className="meta-lab">Model</span>
              <span className="meta-val" title={vieneu?.model || 'VieNeu-TTS-v3-Turbo'}>
                {(vieneu?.model || 'VieNeu-TTS-v3-Turbo').replace('VieNeu-TTS-', 'v')}
              </span>
            </div>
            <div className="meta-row">
              <span className="meta-lab">Preset</span>
              <span className="meta-val">{vieneu?.presetCount ?? 0}</span>
            </div>
          </div>
          <div className="tts-ram">
            <i style={{ width: vieneu?.loaded ? '42%' : vieneu?.installed ? '18%' : '6%' }} />
          </div>
          {!vieneu?.installed && (
            <p className="tts-engine-hint">
              {vieneu?.installHint || 'pip install vieneu onnxruntime soundfile soxr sea-g2p perth'}
            </p>
          )}
          <button type="button" className="tts-link" onClick={() => void loadStatus()}>
            Làm mới trạng thái
          </button>
        </div>
      </aside>

      {/* ── Main ── */}
      <div className="tts-main">
        <div className="tts-page-head">
          <div>
            <h1>Text to Speech (TTS)</h1>
            <p>Nhập văn bản, chọn giọng và tạo giọng nói AI tự nhiên</p>
          </div>
          <div className="tts-page-actions">
            {isFullDash && (
              <button
                type="button"
                title="Khôi phục bố cục mặc định 4+2"
                onClick={() => {
                  setDashLayout(structuredClone(DEFAULT_DASH_LAYOUT))
                  setDashActive(null)
                }}
              >
                Đặt lại layout
              </button>
            )}
            <button type="button"><IconHelp size={14} /> Hướng dẫn</button>
            <button type="button"><IconKb size={14} /> Phím tắt</button>
          </div>
        </div>

        {error && <div className="tts-error">{error}</div>}

        {showComingSoon && (
          <div className="tts-coming">
            <div className="tts-coming-card">
              <div className="tts-coming-ico">🚀</div>
              <h2>{SECTION_LABELS[section] || 'Tính năng'}</h2>
              <p>Trang này đang được phát triển.</p>
              <p className="tts-coming-soon">Sắp ra mắt…</p>
              <button type="button" className="tts-btn tts-btn-blue" onClick={() => go('overview')}>
                Về Tổng quan
              </button>
            </div>
          </div>
        )}

        {(section === 'input' || section === 'srt') && (
          <div className="tts-page-panel">
            <section className="tts-card" id="tts-input">
              <h3 className="tts-card-title">
                <span className="tts-step">1</span>
                {section === 'srt' ? 'Nhập SRT / Phụ đề' : 'Nhập văn bản'}
              </h3>
              <div className="tts-tabs">
                <button type="button" className={section === 'input' ? 'active' : ''} onClick={() => go('input')}>
                  <IconFile size={12} /> Nhập văn bản
                </button>
                <button type="button" className={section === 'srt' ? 'active' : ''} onClick={() => go('srt')}>
                  <IconList size={12} /> Nhập SRT
                </button>
                <button type="button" onClick={() => fileRef.current?.click()}>
                  <IconFile size={12} /> Nhập TXT
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      const t = await navigator.clipboard.readText()
                      if (t) setText(t)
                    } catch {
                      setError('Không đọc được clipboard')
                    }
                  }}
                >
                  Dán clipboard
                </button>
              </div>
              <input ref={fileRef} type="file" accept=".txt,text/plain" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onLoadTxt(f) }} />
              <input ref={srtRef} type="file" accept=".srt,text/plain" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onLoadSrt(f) }} />
              {section === 'srt' && (
                <div style={{ marginBottom: 12 }}>
                  <button type="button" className="tts-btn tts-btn-ghost" onClick={() => srtRef.current?.click()}>
                    <IconList size={14} /> Chọn file .srt
                  </button>
                  <p style={{ margin: '8px 0 0', fontSize: '0.78rem', color: 'var(--tts-muted)' }}>
                    Import SRT — giữ timeline + khớp thời lượng khi tạo giọng (batch).
                  </p>
                </div>
              )}
              {section === 'srt' ? (
                <textarea
                  className="tts-textarea"
                  style={{ minHeight: 200 }}
                  value={srtRaw}
                  onChange={(e) => {
                    setSrtRaw(e.target.value)
                    const lines = e.target.value
                      .split(/\r?\n/)
                      .filter((ln) => ln.trim() && !/^\d+$/.test(ln.trim()) && !/-->/.test(ln))
                    setText(lines.join('\n'))
                  }}
                  placeholder="Dán nội dung file .srt (có timestamp)…"
                />
              ) : (
                <textarea
                  className="tts-textarea"
                  style={{ minHeight: 200 }}
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value)
                    setSrtRaw('')
                  }}
                  placeholder="Nhập hoặc dán văn bản của bạn ở đây…"
                />
              )}
              <div className="tts-foot-row">
                <span>
                  {(section === 'srt' ? srtRaw : text).length} ký tự
                  {srtRaw ? ' · SRT mode' : ''}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    setText('')
                    setSrtRaw('')
                  }}
                >
                  Xóa nội dung
                </button>
              </div>
              <div className="tts-split-row">
                <label className="tts-split-field">
                  Tùy chọn tách câu
                  <select
                    value={autoSplit ? 'auto' : 'off'}
                    onChange={(e) => setAutoSplit(e.target.value === 'auto')}
                  >
                    <option value="auto">Tự động tách câu (khuyến nghị)</option>
                    <option value="off">Không tách</option>
                  </select>
                </label>
                <button
                  type="button"
                  className={autoSplit ? 'tts-switch is-on' : 'tts-switch'}
                  role="switch"
                  aria-checked={autoSplit}
                  title={autoSplit ? 'Tắt tách câu' : 'Bật tách câu'}
                  onClick={() => setAutoSplit((v) => !v)}
                >
                  <span className="tts-switch-track" />
                </button>
              </div>
              <div style={{ marginTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="tts-btn tts-btn-blue"
                  disabled={busy || !voice || (!text.trim() && !srtRaw.trim())}
                  onClick={() => void onSynth()}
                >
                  {busy ? 'Đang tạo…' : section === 'srt' ? 'Tạo giọng từ SRT' : 'Tạo giọng nói'}
                </button>
                <button type="button" className="tts-btn tts-btn-ghost" onClick={() => go('overview')}>
                  Về Tổng quan
                </button>
              </div>
            </section>
          </div>
        )}

        {section === 'voice' && (
          <div className="tts-page-panel tts-voice-page">
            <section className="tts-card" id="tts-voice-list">
              <h3 className="tts-card-title">
                <span className="tts-step">4</span> Danh sách giọng
              </h3>
              <div className="tts-voice-toolbar">
                <label className="tts-field">
                  <span>Ngôn ngữ</span>
                  <select
                    value={lang}
                    onChange={(e) => {
                      setLang(e.target.value)
                      preferredVoiceRef.current = ''
                      setVoice('')
                    }}
                  >
                    <option value="auto">Tự động</option>
                    <option value="vi">Tiếng Việt</option>
                    <option value="en">Tiếng Anh</option>
                    <option value="zh">Tiếng Trung</option>
                    <option value="ja">Tiếng Nhật</option>
                    <option value="ko">Tiếng Hàn</option>
                    <option value="th">Tiếng Thái</option>
                    <option value="id">Tiếng Indonesia</option>
                    <option value="es">Tiếng Tây Ban Nha</option>
                    <option value="fr">Tiếng Pháp</option>
                    <option value="de">Tiếng Đức</option>
                    <option value="pt">Tiếng Bồ Đào Nha</option>
                  </select>
                </label>
                <label className="tts-field">
                  <span>Engine</span>
                  <select
                    value={engine}
                    onChange={(e) => {
                      setEngine(e.target.value as typeof engine)
                      preferredVoiceRef.current = ''
                      setVoice('')
                    }}
                  >
                    <option value="zmai">zmAI</option>
                    <option value="vieneu">VieNeu Local</option>
                    <option value="clone">Clone{cloneCount > 0 ? ` (${cloneCount})` : ''}</option>
                    <option value="capcut">CapCut TTS</option>
                    <option value="eleven">ElevenLabs</option>
                    <option value="system">System</option>
                  </select>
                </label>
                <label className="tts-voice-search">
                  <span className="tts-sr-only">Tìm giọng</span>
                  <svg aria-hidden="true" viewBox="0 0 24 24">
                    <circle cx="11" cy="11" r="7" />
                    <path d="m20 20-4-4" />
                  </svg>
                  <input
                    type="search"
                    value={voiceQuery}
                    onChange={(e) => setVoiceQuery(e.target.value)}
                    placeholder="Tìm kiếm giọng nói…"
                    aria-label="Tìm giọng theo tên, mô tả hoặc tag"
                  />
                </label>
              </div>
              <div className="tts-voice-filter">
                  <strong>Lọc theo tag:</strong>
                  <div className="tts-voice-filter-chips" role="group" aria-label="Lọc danh sách theo tag">
                    {voiceFilterTags.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        className={`tts-voice-filter-chip${activeVoiceTag === tag ? ' is-active' : ''}`}
                        aria-pressed={activeVoiceTag === tag}
                        onClick={() => setVoiceTag(activeVoiceTag === tag ? '' : tag)}
                      >
                        {tag}
                      </button>
                    ))}
                  </div>
                </div>
              <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--tts-muted)' }}>
                {visibleEngineVoices.length} giọng phù hợp
                {engine === 'zmai' || engine === 'clone'
                  ? ' · Có thể Sửa / Xóa / chuyển zmAI ↔ Clone'
                  : ''}
              </p>
              {canBulkManage && (
                <div className="tts-voice-bulk">
                  <label>
                    <input
                      type="checkbox"
                      checked={allEngineVoicesSelected}
                      disabled={busy || !engineVoices.length}
                      onChange={(e) => {
                        setSelectedVoiceIds(
                          e.target.checked ? new Set(engineVoices.map((v) => v.id)) : new Set(),
                        )
                      }}
                    />
                    Chọn tất cả
                  </label>
                  <span>{selectedVoiceCount} đã chọn</span>
                  <button
                    type="button"
                    className="tts-btn-sm"
                    disabled={busy || selectedVoiceCount === 0}
                    onClick={openBulkMoveModal}
                  >
                    Chuyển sang {engine === 'zmai' ? 'Clone' : 'zmAI'}
                  </button>
                </div>
              )}
              {renderVoiceList()}
              <div style={{ marginTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button type="button" className="tts-btn tts-btn-blue" onClick={() => go('clone')}>
                  Clone giọng mới
                </button>
                <button type="button" className="tts-btn tts-btn-ghost" onClick={() => onRefreshVoices?.(lang)}>
                  Làm mới
                </button>
              </div>
            </section>
          </div>
        )}

        {section === 'clone' && (
          <div className="tts-page-panel" style={{ maxWidth: 520 }}>
            <section className="tts-card" id="tts-clone">
              <h3 className="tts-card-title">
                <span className="tts-step">4</span> Clone giọng nói (TTS)
                <span className="tts-badge-new">Mới</span>
              </h3>
              <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--tts-muted)' }}>
                Tạo giọng nói tùy chỉnh từ giọng của bạn
                {cloneCount > 0 ? (
                  <>
                    {' · '}
                    <button
                      type="button"
                      className="link"
                      style={{ background: 'none', border: 0, cursor: 'pointer', padding: 0, font: 'inherit', color: 'inherit' }}
                      onClick={() => go('voice')}
                    >
                      {cloneCount} giọng đã lưu
                    </button>
                  </>
                ) : null}
              </p>
              <div className="tts-drop">
                <div className="ico"><IconUpload size={20} /></div>
                <p>Kéo & thả file audio vào đây<br />hoặc</p>
                <button
                  type="button"
                  className="tts-btn tts-btn-ghost"
                  onClick={() => document.getElementById('tts-clone-file')?.click()}
                >
                  Chọn file audio
                </button>
                <input
                  id="tts-clone-file"
                  type="file"
                  accept="audio/*,.wav,.mp3,.m4a"
                  hidden
                  onChange={(e) => setCloneFile(e.target.files?.[0] || null)}
                />
                <div className="hint">
                  {cloneFile ? cloneFile.name : 'Định dạng hỗ trợ: WAV, MP3, M4A · Tối thiểu 10 giây, tối đa 5 phút'}
                </div>
              </div>
              <label className="tts-field">
                <span>Tên giọng</span>
                <input type="text" value={cloneName} placeholder="Ví dụ: Giọng của tôi" onChange={(e) => setCloneName(e.target.value)} />
              </label>
              <label className="tts-field">
                <span>Văn bản tham khảo (tùy chọn — v3 Turbo không bắt buộc)</span>
                <input
                  type="text"
                  value={cloneText}
                  placeholder="Nhập nội dung đã đọc trong file audio"
                  onChange={(e) => setCloneText(e.target.value)}
                />
              </label>
              <VoiceTagPicker value={cloneTags} onChange={setCloneTags} />
              <button
                type="button"
                className="tts-btn tts-btn-primary tts-btn-block"
                disabled={busy || !cloneFile || !cloneName.trim()}
                onClick={() => void onClone()}
              >
                Tạo giọng clone
              </button>
            </section>
          </div>
        )}

        {section === 'history' && (
          <div className="tts-page-panel">
            <section className="tts-card tts-history-card" id="tts-history">
              <h3 className="tts-card-title"><span className="tts-step">7</span> Lịch sử tạo giọng</h3>
              {renderHistoryBody()}
            </section>
          </div>
        )}

        {isFullDash && (
        <>
        {/* Full dashboard — Tổng quan / Tạo giọng nói */}
        <div className="tts-dash" ref={dashRef}>
          <DashPanel
            id="input"
            item={dashLayout.input}
            active={dashActive === 'input'}
            gridRef={dashRef}
            onActive={setDashActive}
            onChange={setDashLayoutSafe}
          >
          <section className="tts-card" id="tts-input">
            <h3 className="tts-card-title"><span className="tts-step">1</span> Nhập nội dung</h3>
            <div className="tts-tabs">
              <button type="button" className="active"><IconFile size={12} /> Nhập văn bản</button>
              <button type="button" onClick={() => srtRef.current?.click()}>
                <IconList size={12} /> Nhập SRT
              </button>
              <button type="button" onClick={() => fileRef.current?.click()}>
                <IconFile size={12} /> Nhập TXT
              </button>
              <button
                type="button"
                onClick={async () => {
                  try {
                    const t = await navigator.clipboard.readText()
                    if (t) setText(t)
                  } catch {
                    setError('Không đọc được clipboard')
                  }
                }}
              >
                Dán clipboard
              </button>
            </div>
            <input ref={fileRef} type="file" accept=".txt,text/plain" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onLoadTxt(f) }} />
            <input ref={srtRef} type="file" accept=".srt,text/plain" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onLoadSrt(f) }} />
            <textarea
              className="tts-textarea"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Nhập hoặc dán văn bản của bạn ở đây…"
            />
            <div className="tts-foot-row">
              <span>{text.length} ký tự</span>
              <button type="button" onClick={() => setText('')}>Xóa nội dung</button>
            </div>
            <div className="tts-split-row">
              <label className="tts-split-field">
                Tùy chọn tách câu
                <select
                  value={autoSplit ? 'auto' : 'off'}
                  onChange={(e) => setAutoSplit(e.target.value === 'auto')}
                >
                  <option value="auto">Tự động tách câu (khuyến nghị)</option>
                  <option value="off">Không tách</option>
                </select>
              </label>
              <button
                type="button"
                className={autoSplit ? 'tts-switch is-on' : 'tts-switch'}
                role="switch"
                aria-checked={autoSplit}
                title={autoSplit ? 'Tắt tách câu' : 'Bật tách câu'}
                onClick={() => setAutoSplit((v) => !v)}
              >
                <span className="tts-switch-track" />
              </button>
            </div>
            <p style={{ margin: '6px 0 0', fontSize: '0.72rem', color: 'var(--tts-muted)' }}>
              Hệ thống tự động tách văn bản thành các câu hợp lý.
            </p>
          </section>
          </DashPanel>

          <DashPanel
            id="voice"
            item={dashLayout.voice}
            active={dashActive === 'voice'}
            gridRef={dashRef}
            onActive={setDashActive}
            onChange={setDashLayoutSafe}
          >
          <section className="tts-card" id="tts-voice">
            <h3 className="tts-card-title"><span className="tts-step">2</span> Cài đặt giọng nói</h3>
            <div className="tts-inline" style={{ gap: 8, marginBottom: 8 }}>
              <label className="tts-field" style={{ flex: 1, marginBottom: 0 }}>
                <span>Ngôn ngữ</span>
                <select
                  value={lang}
                  onChange={(e) => {
                    setLang(e.target.value)
                    preferredVoiceRef.current = ''
                    setVoice('')
                  }}
                >
                  <option value="auto">Tự động</option>
                  <option value="vi">Tiếng Việt</option>
                  <option value="en">Tiếng Anh</option>
                  <option value="zh">Tiếng Trung</option>
                  <option value="ja">Tiếng Nhật</option>
                  <option value="ko">Tiếng Hàn</option>
                  <option value="th">Tiếng Thái</option>
                  <option value="id">Tiếng Indonesia</option>
                  <option value="es">Tiếng Tây Ban Nha</option>
                  <option value="fr">Tiếng Pháp</option>
                  <option value="de">Tiếng Đức</option>
                  <option value="pt">Tiếng Bồ Đào Nha</option>
                </select>
              </label>
              <label className="tts-field" style={{ flex: 1, marginBottom: 0 }}>
                <span>Engine</span>
                <div className="tts-inline">
                  <select
                    value={engine}
                    onChange={(e) => {
                      const eng = e.target.value as typeof engine
                      setEngine(eng)
                      preferredVoiceRef.current = ''
                      setVoice('') // effect chọn giọng đầu của engine
                    }}
                  >
                    <option value="zmai">zmAI</option>
                    <option value="vieneu">VieNeu Local</option>
                    <option value="clone">
                      Clone{cloneCount > 0 ? ` (${cloneCount})` : ''}
                    </option>
                    <option value="capcut">CapCut TTS</option>
                    <option value="eleven">ElevenLabs</option>
                    <option value="system">System</option>
                  </select>
                  {(engine === 'zmai' || engine === 'vieneu' || engine === 'clone' || engine === 'system') && (
                    <span className="tts-pill-local">Local</span>
                  )}
                </div>
              </label>
            </div>
            <label className="tts-field">
              <span>Giọng nói ({engineVoices.length})</span>
              <div className="tts-inline">
                <select
                  value={voice}
                  onChange={(e) => {
                    preferredVoiceRef.current = e.target.value
                    setVoice(e.target.value)
                  }}
                >
                  {engineVoices.length === 0 && (
                    <option value="">
                      {engine === 'clone'
                        ? '— Chưa có giọng clone —'
                        : '— Không có giọng engine này —'}
                    </option>
                  )}
                  {engineVoices.map((v) => (
                    <option key={v.id} value={v.id}>{v.name}</option>
                  ))}
                </select>
                {selectedVoice?.previewUrl && (
                  <button
                    type="button"
                    className="tts-btn-sm tts-btn-icon"
                    onClick={() => toggleVoicePreview(selectedVoice)}
                    title={previewingVoiceId === selectedVoice.id ? 'Dừng' : 'Phát audio mẫu có sẵn, không tạo TTS'}
                    aria-label={previewingVoiceId === selectedVoice.id ? 'Dừng' : 'Nghe thử'}
                  >
                    {previewingVoiceId === selectedVoice.id ? <IconPause size={13} /> : <IconPlay size={13} />}
                  </button>
                )}
                <button
                  type="button"
                  className="tts-btn-sm tts-btn-list"
                  onClick={() => go('voice')}
                  title="Mở danh sách và chọn giọng"
                >
                  <IconUsers size={13} /> Danh sách
                </button>
              </div>
              {selectedVoice && (() => {
                const metadata = voiceMetadata(selectedVoice)
                return (
                  <div className="tts-voice-selected-meta">
                    <span>{metadata.description}</span>
                    <span className="tts-voice-tags" aria-label="Thông tin giọng đang chọn">
                      {metadata.tags.map((tag) => (
                        <span key={`${tag.kind}:${tag.label}`} className={`tts-voice-tag ${tag.kind}`}>
                          {tag.label}
                        </span>
                      ))}
                    </span>
                  </div>
                )
              })()}
            </label>
              <label className="tts-field">
              <span>Nghe thử giọng</span>
              <div className="tts-listen-row">
                <input
                  type="text"
                  value={previewSample}
                  onChange={(e) => setPreviewSample(e.target.value)}
                  placeholder="Nhập câu mẫu (tùy chọn)"
                  title={`Gợi ý: ${previewSampleFor(lang)} — hoặc dùng nút Play nếu có WAV mẫu`}
                />
                <button
                  type="button"
                  className="tts-btn tts-btn-ghost"
                  disabled={busy || !voice || !previewSample.trim()}
                  onClick={() => void onPreview()}
                >
                  <IconHeadphones size={14} /> Nghe thử
                </button>
              </div>
            </label>
            <div className="tts-slider-row">
              <div className="lab"><span>Tốc độ (Speed)</span><b>{speed.toFixed(2)}x</b></div>
              <input type="range" min={0.5} max={2} step={0.05} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
              <div className="tts-slider-marks"><span>0.5x</span><span>1.0x</span><span>2.0x</span></div>
            </div>
            <div className="tts-slider-row">
              <div className="lab"><span>Âm lượng (Volume)</span><b>{Math.round(volume * 100)}%</b></div>
              <input type="range" min={0.5} max={2} step={0.05} value={volume} onChange={(e) => setVolume(Number(e.target.value))} />
              <div className="tts-slider-marks"><span>50%</span><span>100%</span><span>150%</span><span>200%</span></div>
            </div>
            <div className="tts-slider-row">
              <div className="lab"><span>Cao độ (Pitch)</span><b>{pitch > 0 ? `+${pitch}` : pitch}</b></div>
              <input type="range" min={-12} max={12} step={1} value={pitch} onChange={(e) => setPitch(Number(e.target.value))} />
              <div className="tts-slider-marks"><span>-12</span><span>0</span><span>+12</span></div>
            </div>
            {isVieneuVoice && (
              <label className="tts-field">
                <span>Phong cách (VieNeu)</span>
                <select value={style} onChange={(e) => setStyle(e.target.value)}>
                  <option value="tu_nhien">Tự nhiên</option>
                  <option value="tin_tuc">Tin tức</option>
                  <option value="doc_truyen">Đọc truyện</option>
                </select>
              </label>
            )}
          </section>
          </DashPanel>

          <DashPanel
            id="advanced"
            item={dashLayout.advanced}
            active={dashActive === 'advanced'}
            gridRef={dashRef}
            onActive={setDashActive}
            onChange={setDashLayoutSafe}
          >
          <section className="tts-card" id="tts-advanced">
            <h3 className="tts-card-title"><span className="tts-step">3</span> Tùy chọn nâng cao</h3>
            <label className="tts-check">
              <input type="checkbox" checked={matchSrt} onChange={(e) => setMatchSrt(e.target.checked)} />
              <span>
                Khớp thời lượng (khi nhập SRT)
                <small>Tự động điều chỉnh tốc độ để khớp thời gian phụ đề</small>
              </span>
            </label>
            <label className="tts-check">
              <input type="checkbox" checked={keepTimeline} onChange={(e) => setKeepTimeline(e.target.checked)} />
              <span>
                Giữ nguyên timeline SRT
                <small>Giữ nguyên đúng thời điểm bắt đầu/kết thúc của phụ đề</small>
              </span>
            </label>
            <label className="tts-check">
              <input type="checkbox" checked={normalize} onChange={(e) => setNormalize(e.target.checked)} />
              <span>
                Chuẩn hóa âm lượng
                <small>Giữ âm lượng đồng đều giữa các câu</small>
              </span>
            </label>
            <label className={`tts-check${gapOn ? '' : ' tts-check-disabled'}`}>
              <input type="checkbox" checked={gapOn} onChange={(e) => setGapOn(e.target.checked)} />
              <span>
                Thêm khoảng nghỉ giữa câu
                <small>
                  {gapOn ? (
                    <input
                      type="number"
                      min={50}
                      max={2000}
                      value={gapMs}
                      onChange={(e) => setGapMs(Number(e.target.value))}
                      style={{ width: 72, marginLeft: 4, border: '1px solid var(--tts-line)', borderRadius: 6, padding: '2px 6px' }}
                    />
                  ) : null}
                  {' '}ms — khoảng nghỉ ngắn giữa các câu
                </small>
              </span>
            </label>
            <label className="tts-check">
              <input type="checkbox" checked={trimSilence} onChange={(e) => setTrimSilence(e.target.checked)} />
              <span>
                Loại bỏ khoảng lặng thừa
                <small>Tự động cắt khoảng lặng ở đầu và cuối</small>
              </span>
            </label>
            <label className="tts-field" style={{ marginTop: 4 }}>
              <span>Định dạng xuất audio</span>
              <select
                value={outputFormat}
                onChange={(e) => setOutputFormat(e.target.value as TtsOutputFormat)}
              >
                <option value="wav48">WAV (48kHz, 16bit)</option>
                <option value="wav16">WAV (16kHz)</option>
                <option value="mp3">MP3</option>
              </select>
            </label>
          </section>
          </DashPanel>

          <DashPanel
            id="clone"
            item={dashLayout.clone}
            active={dashActive === 'clone'}
            gridRef={dashRef}
            onActive={setDashActive}
            onChange={setDashLayoutSafe}
          >
          <section className="tts-card" id="tts-clone">
            <h3 className="tts-card-title">
              <span className="tts-step">4</span> Clone giọng nói (TTS)
              <span className="tts-badge-new">Mới</span>
            </h3>
            <p style={{ margin: '0 0 12px', fontSize: '0.8rem', color: 'var(--tts-muted)', lineHeight: 1.6, letterSpacing: '0.02em' }}>
              Tạo giọng nói tùy chỉnh từ giọng của bạn
              {cloneCount > 0 ? (
                <>
                  {' · '}
                  <button
                    type="button"
                    className="link"
                    style={{ background: 'none', border: 0, cursor: 'pointer', padding: 0, font: 'inherit', color: 'inherit' }}
                    onClick={() => go('voice')}
                  >
                    {cloneCount} giọng đã lưu — quản lý
                  </button>
                </>
              ) : null}
            </p>
            <div className="tts-drop">
              <div className="ico"><IconUpload size={20} /></div>
              <p>Kéo & thả file audio vào đây<br />hoặc</p>
              <button
                type="button"
                className="tts-btn tts-btn-ghost"
                onClick={() => document.getElementById('tts-clone-file-dash')?.click()}
              >
                Chọn file audio
              </button>
              <input
                id="tts-clone-file-dash"
                type="file"
                accept="audio/*,.wav,.mp3,.m4a"
                hidden
                onChange={(e) => setCloneFile(e.target.files?.[0] || null)}
              />
              <div className="hint">
                {cloneFile ? cloneFile.name : 'Định dạng hỗ trợ: WAV, MP3, M4A · Tối thiểu 10 giây, tối đa 5 phút'}
              </div>
            </div>
            <label className="tts-field">
              <span>Tên giọng</span>
              <input type="text" value={cloneName} placeholder="Ví dụ: Giọng của tôi" onChange={(e) => setCloneName(e.target.value)} />
            </label>
            <label className="tts-field">
              <span>Văn bản tham khảo (tùy chọn — v3 Turbo không bắt buộc)</span>
              <input
                type="text"
                value={cloneText}
                placeholder="Nhập nội dung đã đọc trong file audio"
                onChange={(e) => setCloneText(e.target.value)}
              />
            </label>
            <VoiceTagPicker value={cloneTags} onChange={setCloneTags} />
            <button
              type="button"
              className="tts-btn tts-btn-primary tts-btn-block"
              disabled={busy || !cloneFile || !cloneName.trim()}
              onClick={() => void onClone()}
            >
              Tạo giọng clone
            </button>
          </section>
          </DashPanel>

<DashPanel
            id="preview"
            item={dashLayout.preview}
            active={dashActive === 'preview'}
            gridRef={dashRef}
            onActive={setDashActive}
            onChange={setDashLayoutSafe}
          >
          <section className="tts-card" id="tts-make">
            <h3 className="tts-card-title"><span className="tts-step">5</span> Xem trước & Tạo giọng nói</h3>
            <div className="tts-preview-body">
              <button
                type="button"
                className="tts-main-play"
                disabled={!audioUrl}
                aria-label={isPlaying ? 'Tạm dừng' : 'Phát'}
                title={isPlaying ? 'Tạm dừng' : 'Phát'}
                onClick={() => {
                  const a = audioRef.current
                  if (!a) return
                  if (a.paused) void a.play()
                  else a.pause()
                }}
              >
                {isPlaying ? <IconPause size={17} /> : <IconPlay size={20} />}
              </button>
              <div className="tts-player-main">
                <div className="tts-wave-box">
                  <button
                    type="button"
                    className={`tts-wave-vis${isPlaying ? ' is-playing' : ''}`}
                    disabled={!audioUrl}
                    aria-label={isPlaying ? 'Tạm dừng' : 'Phát audio'}
                    title={isPlaying ? 'Tạm dừng' : 'Phát audio'}
                    onClick={() => {
                      const a = audioRef.current
                      if (!a) return
                      if (a.paused) void a.play()
                      else a.pause()
                    }}
                  >
                    {WAVE_BARS.map((h, i) => (
                      <i
                        key={i}
                        className={
                          audioUrl &&
                          i / WAVE_BARS.length <=
                            playbackTime / Math.max(playbackDuration, duration, 0.01)
                            ? 'played'
                            : audioUrl
                              ? 'ready'
                              : undefined
                        }
                        style={{ height: `${h}px` }}
                      />
                    ))}
                  </button>
                </div>
                {audioUrl ? (
                  <>
                    <audio
                      ref={audioRef}
                      key={audioUrl}
                      className="tts-audio"
                      src={audioUrl}
                      preload="auto"
                      onLoadedMetadata={(e) => {
                        const d = e.currentTarget.duration
                        setPlaybackDuration(Number.isFinite(d) ? d : duration)
                        e.currentTarget.volume = playbackVolume
                      }}
                      onTimeUpdate={(e) => setPlaybackTime(e.currentTarget.currentTime)}
                      onPlay={() => setIsPlaying(true)}
                      onPause={() => setIsPlaying(false)}
                      onEnded={() => setIsPlaying(false)}
                    />
                    <div className="tts-player-controls">
                      <span className="tts-player-time">
                        {fmtDur(playbackTime)} / {fmtDur(playbackDuration || duration)}
                      </span>
                      <input
                        className="tts-seek"
                        type="range"
                        min={0}
                        max={Math.max(playbackDuration || duration, 0.01)}
                        step={0.01}
                        value={Math.min(playbackTime, playbackDuration || duration || 0)}
                        aria-label="Vị trí phát"
                        style={{
                          background: `linear-gradient(to right, var(--tts-blue) ${
                            (playbackTime / Math.max(playbackDuration || duration, 0.01)) * 100
                          }%, var(--tts-line) 0%)`,
                        }}
                        onChange={(e) => {
                          const next = Number(e.target.value)
                          setPlaybackTime(next)
                          if (audioRef.current) audioRef.current.currentTime = next
                        }}
                      />
                      <IconSpeaker size={14} />
                      <input
                        className="tts-player-volume"
                        type="range"
                        min={0}
                        max={1}
                        step={0.01}
                        value={playbackVolume}
                        aria-label="Âm lượng phát"
                        style={{
                          background: `linear-gradient(to right, var(--tts-blue) ${
                            playbackVolume * 100
                          }%, var(--tts-line) 0%)`,
                        }}
                        onChange={(e) => {
                          const next = Number(e.target.value)
                          setPlaybackVolume(next)
                          if (audioRef.current) audioRef.current.volume = next
                        }}
                      />
                    </div>
                  </>
                ) : (
                  <div className="tts-player-controls is-idle">
                    <span className="tts-player-time">00:00 / {fmtDur(duration)}</span>
                    <input className="tts-seek" type="range" min={0} max={1} value={0} disabled aria-label="Vị trí phát" readOnly />
                    <IconSpeaker size={14} />
                    <input
                      className="tts-player-volume"
                      type="range"
                      min={0}
                      max={1}
                      value={1}
                      disabled
                      aria-label="Âm lượng phát"
                      readOnly
                      style={{ background: 'var(--tts-line)' }}
                    />
                  </div>
                )}
              </div>
              <div className="tts-preview-actions">
                <button
                  type="button"
                  className="tts-btn tts-btn-blue"
                  disabled={busy || !text.trim() || !voice}
                  onClick={() => void onSynth()}
                >
                  <IconMic size={15} /> {busy ? 'Đang tạo…' : 'Tạo giọng nói'}
                </button>
                <button
                  type="button"
                  className="tts-btn tts-btn-ghost"
                  disabled={!busy && !jobId}
                  onClick={() => {
                    audioRef.current?.pause()
                    void onCancelJob()
                  }}
                >
                  Dừng / Hủy
                </button>
                <button
                  type="button"
                  className="tts-btn tts-btn-ghost"
                  disabled={!audioUrl}
                  onClick={() => {
                    setAudioUrl(null)
                    setMp3Url(null)
                    setJobId(null)
                    setDuration(0)
                  }}
                >
                  Xóa & Làm mới
                </button>
              </div>
            </div>
          </section>
          </DashPanel>

          <DashPanel
            id="export"
            item={dashLayout.export}
            active={dashActive === 'export'}
            gridRef={dashRef}
            onActive={setDashActive}
            onChange={setDashLayoutSafe}
          >
          <section className="tts-card" id="tts-export">
            <h3 className="tts-card-title"><span className="tts-step">6</span> Xuất kết quả</h3>
            <div className="tts-export-grid">
              <a
                className="tts-btn tts-btn-ghost"
                href={downloadWavHref(audioUrl)}
                download={audioUrl ? 'tts-output.wav' : undefined}
                style={{ pointerEvents: audioUrl ? 'auto' : 'none', opacity: audioUrl ? 1 : 0.5, textDecoration: 'none' }}
              >
                <IconDownload size={14} /> Tải audio (WAV)
              </a>
              <a
                className="tts-btn tts-btn-ghost"
                href={mp3Url || undefined}
                download={mp3Url ? 'tts-output.mp3' : undefined}
                style={{ pointerEvents: mp3Url ? 'auto' : 'none', opacity: mp3Url ? 1 : 0.5, textDecoration: 'none' }}
              >
                <IconDownload size={14} /> Tải audio (MP3)
              </a>
              <div className="tts-export-menu-wrap" data-dl-menu>
                <button
                  type="button"
                  className="tts-btn tts-btn-ghost"
                  disabled={!jobId}
                  aria-haspopup="menu"
                  aria-expanded={mainSrtMenuOpen}
                  onClick={() => setMainSrtMenuOpen((open) => !open)}
                >
                  <IconList size={14} /> Xuất SRT cho CapCut
                </button>
                {mainSrtMenuOpen && jobId && (
                  <div className="tts-dl-menu tts-export-srt-menu" role="menu">
                    {SRT_STYLE_OPTIONS.map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        role="menuitem"
                        onClick={() =>
                          triggerDownload(
                            `/api/tts/studio/jobs/${jobId}/subs.srt?style=${opt.id}&t=${Date.now()}`,
                            `tts-output-${opt.id}.srt`,
                          )
                        }
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <a
                className="tts-btn tts-btn-ghost"
                href={jobId ? `/api/tts/studio/jobs/${jobId}/bundle.zip?style=hard&t=${Date.now()}` : undefined}
                download={jobId ? 'tts-bundle.zip' : undefined}
                style={{ pointerEvents: jobId ? 'auto' : 'none', opacity: jobId ? 1 : 0.5, textDecoration: 'none' }}
              >
                <IconFile size={14} /> Xuất ZIP (Audio + SRT)
              </a>
            </div>
          </section>
          </DashPanel>
        </div>

        <section className="tts-card tts-history-card" id="tts-history">
          <h3 className="tts-card-title"><span className="tts-step">7</span> Lịch sử tạo giọng</h3>
          {renderHistoryBody()}
        </section>
        </>
        )}
      </div>

      <ProgressPopup
        active={busy || Boolean(error && error !== 'Đã hủy' && error !== 'cancelled')}
        minimized={progressMinimized}
        running={busy}
        title={busy ? busyTitle : error ? 'Lỗi TTS' : 'TTS'}
        message={busy ? busyMessage : error || undefined}
        progress={busy ? busyProgress : error ? 0 : 100}
        error={!busy && error && error !== 'Đã hủy' ? error : null}
        onMinimize={() => {
          setProgressMinimized(true)
          if (!busy && error) setError('')
        }}
        onRestore={() => setProgressMinimized(false)}
        onCancel={busy ? () => { void onCancelJob() } : undefined}
      />

      {editingVoice && (
        <VoiceMetadataModal
          name={voiceDisplayName(editingVoice.id, voices, editingVoice.name)}
          tags={editingVoice.tags || []}
          onClose={() => setEditingVoice(null)}
          onSave={(name, tags) => onSaveVoiceMetadata(editingVoice.id, name, tags)}
        />
      )}

      {bulkMoveOpen && (
        <div
          className="tts-modal-backdrop"
          role="presentation"
          onClick={closeBulkMoveModal}
        >
          <div
            className="tts-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="tts-bulk-move-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="tts-bulk-move-title" className="tts-modal-title">
              Chuyển engine giọng
            </h3>
            <p className="tts-modal-body">
              Chuyển <strong>{selectedVoiceCount}</strong> giọng đã chọn từ{' '}
              <strong>{bulkMoveSourceLabel}</strong> sang <strong>{bulkMoveTargetLabel}</strong>.
            </p>
            <p className="tts-modal-hint">
              File giọng và registry sẽ được di chuyển sang engine đích. Thao tác này không tạo bản
              sao.
            </p>
            <div className="tts-modal-actions">
              <button
                type="button"
                className="tts-btn tts-btn-ghost"
                disabled={busy}
                onClick={closeBulkMoveModal}
              >
                Hủy
              </button>
              <button
                type="button"
                className="tts-btn tts-btn-blue"
                disabled={busy || selectedVoiceCount === 0}
                onClick={() => void confirmBulkMoveVoices()}
              >
                {busy ? 'Đang chuyển…' : `Xác nhận · ${bulkMoveTargetLabel}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
