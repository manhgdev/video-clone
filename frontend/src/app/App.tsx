import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import Header, { type AppMode } from '@/shared/components/Header'
import LivePreviewEditor from '@/features/editor/LivePreviewEditor'
import ProgressPopup from '@/shared/components/ProgressPopup'
import ProjectSidebar from '@/features/project/ProjectSidebar'
import PipelineStepper from '@/features/project/PipelineStepper'
import SegmentList from '@/features/project/SegmentList'
import ConfigModal from '@/features/configuration/ConfigModal'
import TtsPage from '@/pages/TtsPage'
import DownloadPage from '@/pages/DownloadPage'
import FilmPage from '@/pages/FilmPage'
import BatchPage from '@/pages/BatchPage'
import { api } from '@/features/project/project.api'
import type { HardwareInfo, JobStatus, ProjectSettings, Segment, Step, TextOverlay } from '@/features/project/project.types'
import { loadAppMode, persistAppMode } from '@/app/appMode'
import './App.css'

const SETTINGS_LS = 'videoclone.settings'
const SESSION_LS = 'videoclone.session'
const SIDEBAR_W_LS = 'videoclone.sidebarWidth'
const THEME_LS = 'videoclone.theme'

function loadTheme(): boolean {
  try { return localStorage.getItem(THEME_LS) === 'dark' } catch { return false }
}
const SIDEBAR_MIN = 240
const SIDEBAR_MAX = 560
const SIDEBAR_DEFAULT = 360

function loadSidebarWidth(): number {
  try {
    const raw = localStorage.getItem(SIDEBAR_W_LS)
    // Number(null) === 0 — không dùng khi chưa lưu
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

/** Mặc định lần đầu — từng engine khác nhau (user chỉnh sau thì nhớ riêng). */
const ENGINE_DEFAULTS = {
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

const defaultSettings: ProjectSettings = {
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

function applyEngineProfile(s: ProjectSettings, engine: ProjectSettings['engine']): ProjectSettings {
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
function snapshotEngineProfile(s: ProjectSettings): ProjectSettings {
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

function loadSettings(): ProjectSettings {
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
    // Seed profile thiếu (lần đầu / settings cũ)
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
    // Migrate: giá trị đang active → profile engine hiện tại (nếu user đã chỉnh trước khi có profiles)
    if (!s.engineProfiles?.[eng]) {
      profiles[eng] = {
        matchDuration: s.matchDuration,
        processOriginalAudio: s.processOriginalAudio,
        originalAudioMode: s.originalAudioMode,
        originalAudioVolume: s.originalAudioVolume,
      }
    }
    s.engineProfiles = profiles
    // Active fields = profile engine đang chọn
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

function persistSettings(s: ProjectSettings) {
  try {
    localStorage.setItem(SETTINGS_LS, JSON.stringify(snapshotEngineProfile(s)))
  } catch {
    /* quota / private mode */
  }
}

function persistSession(projectId: string | null) {
  try {
    if (projectId) localStorage.setItem(SESSION_LS, projectId)
    else localStorage.removeItem(SESSION_LS)
  } catch {
    /* ignore */
  }
}

const idleStatus: JobStatus = {
  step: 'video',
  progress: 0,
  message: 'Chọn video để bắt đầu',
  running: false,
}

function fmtDuration(sec: number) {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function App() {
  const [dark, setDark] = useState(loadTheme)
  const [appMode, setAppMode] = useState<AppMode>(loadAppMode)
  const [hw, setHw] = useState<HardwareInfo>({ label: 'CPU', accel: 'cpu' })
  const [voices, setVoices] = useState<{ id: string; name: string; previewUrl?: string }[]>([
    { id: 'el:pNInz6obpgDQGcFmaJgB', name: 'ElevenLabs · Adam' },
    { id: 'system', name: 'Giọng hệ thống (theo ngôn ngữ đích)' },
  ])
  const [settings, setSettings] = useState(loadSettings)
  const [configOpen, setConfigOpen] = useState(false)
  const [configSection, setConfigSection] = useState<'setup' | 'cloud' | 'tts'>('cloud')
  const [forceSetup, setForceSetup] = useState(false)
  const [setupReady, setSetupReady] = useState(true)
  const [sidebarWidth, setSidebarWidth] = useState(loadSidebarWidth)
  const sidebarWidthRef = useRef(sidebarWidth)
  const sidebarDrag = useRef<{ startX: number; startW: number } | null>(null)
  const [projectId, setProjectId] = useState<string | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [duration, setDuration] = useState(0)
  /** Độ dài clip làm việc = lần dịch gần nhất (0 = full). Khác settings.previewSec (ô Preview). */
  const [workClipSec, setWorkClipSec] = useState(0)
  const workClipSecRef = useRef(0)
  const [bakedPreferVideo, setBakedPreferVideo] = useState(false)
  const bakedPreferVideoRef = useRef(false)
  const [bakedSpeed, setBakedSpeed] = useState(1)
  const [segments, setSegments] = useState<Segment[]>([])
  const [overlays, setOverlays] = useState<TextOverlay[]>([])
  const [status, setStatus] = useState<JobStatus>(idleStatus)
  const [exportUrl, setExportUrl] = useState<string | null>(null)
  const [exportPath, setExportPath] = useState<string | null>(null)
  const [viewExportSrc, setViewExportSrc] = useState<string | null>(null)
  const [previewEditorOpen, setPreviewEditorOpen] = useState(false)
  const [progressMinimized, setProgressMinimized] = useState(false)
  const pollRef = useRef<number | null>(null)
  const pollInFlight = useRef(false)
  const pollFailStreak = useRef(0)
  const pendingExportUrl = useRef<string | null>(null)
  const pendingExportPath = useRef<string | null>(null)
  /** Guards project/video switches from late restore, upload, and poll responses. */
  const projectSwitchRef = useRef(0)
  const activeProjectRef = useRef<string | null>(null)
  const videoRevisionRef = useRef(0)
  /** chặn double-click: Dịch/Xuất rồi dính nút Huỷ vừa hiện */
  const busyAt = useRef(0)

  const freshVideoUrl = (url: string) => {
    // Chỉ revision (không Date.now) — giảm abort Range storm khi poll/bake
    videoRevisionRef.current += 1
    const base = url.split('?')[0]
    return `${base}?v=${videoRevisionRef.current}`
  }

  useEffect(() => {
    if (status.running) setProgressMinimized(false)
  }, [status.running])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    try { localStorage.setItem(THEME_LS, dark ? 'dark' : 'light') } catch { /* ignore */ }
  }, [dark])

  // F5: giữ tab top-level (TTS / Film / …); editor preview không persist → không ép mode
  useEffect(() => {
    persistAppMode(appMode)
  }, [appMode])

  useEffect(() => {
    api.hardware().then(setHw).catch(() => setHw({ label: 'Local', accel: 'cpu' }))
  }, [])

  // First-run: thiếu ffmpeg / package → mở tab Thiết lập
  useEffect(() => {
    let cancelled = false
    void api
      .systemChecks()
      .then((c) => {
        if (cancelled) return
        if (!c.ok) {
          setSetupReady(false)
          setForceSetup(true)
          setConfigSection('setup')
          setConfigOpen(true)
        } else {
          setSetupReady(true)
          setForceSetup(false)
        }
      })
      .catch(() => {
        // API chưa lên — không chặn UI; user mở Cấu hình sau
        if (!cancelled) setSetupReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // F5 / Vite HMR: mở lại project đang làm (kể cả đang export)
  useEffect(() => {
    const switchVersion = projectSwitchRef.current
    let id = ''
    try {
      id = localStorage.getItem(SESSION_LS) || ''
    } catch {
      return
    }
    if (!id) return
    let dead = false
    ;(async () => {
      try {
        const [st, segs] = await Promise.all([api.status(id), api.segments(id)])
        if (dead || projectSwitchRef.current !== switchVersion) return
        activeProjectRef.current = id
        setProjectId(id)
        // ?t= bust cache — tránh <video> Range cũ → 416 sau đổi preview/full
        setVideoUrl(freshVideoUrl(`/api/projects/${id}/video`))
        const dur = Number(st.duration || 0)
        if (dur > 0) setDuration(dur)
        if (typeof st.workClipSec === 'number') {
          const wc = Math.max(0, st.workClipSec)
          workClipSecRef.current = wc
          setWorkClipSec(wc)
        }
        const baked = Boolean(st.bakedPreferVideo)
        bakedPreferVideoRef.current = baked
        setBakedPreferVideo(baked)
        if (typeof st.bakedSpeed === 'number' && st.bakedSpeed > 0) setBakedSpeed(st.bakedSpeed)
        else setBakedSpeed(baked ? 0.8 : 1)
        const extra = st as JobStatus & { settings?: Partial<ProjectSettings> }
        const mergedVoice =
          (extra.settings && typeof extra.settings === 'object' && extra.settings.defaultVoice) ||
          settings.defaultVoice
        setSegments(applyDefaultVoice(asSegmentList(segs), mergedVoice))
        if (extra.settings && typeof extra.settings === 'object') {
          setSettings((s) => {
            const next = { ...s, ...extra.settings }
            persistSettings(next)
            return next
          })
        }
        setStatus({
          step: st.step || 'video',
          progress: st.progress || 0,
          message: st.message || 'Đã mở lại project',
          running: Boolean(st.running),
          error: st.error,
          outputRel: st.outputRel,
        })
        if (st.running) busyAt.current = Date.now()
        if (!st.running && st.outputRel && (st.progress || 0) >= 100) {
          setExportUrl(`/api/projects/${id}/output`)
          setExportPath(st.outputRel)
        }
      } catch {
        persistSession(null)
      }
    })()
    return () => {
      dead = true
    }
  }, [])

  useEffect(() => {
    const ac = new AbortController()
    const t = window.setTimeout(() => ac.abort(), 8000)
    fetch(`/api/voices?lang=${encodeURIComponent(settings.targetLang === 'none' ? 'vi' : settings.targetLang)}`, {
      signal: ac.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        return r.json() as Promise<{ id: string; name: string; previewUrl?: string }[]>
      })
      .then((vs) => {
        if (!Array.isArray(vs) || !vs.length) return
        setVoices(vs)
        setSettings((s) => {
          const next = vs.some((v) => v.id === s.defaultVoice) ? s : { ...s, defaultVoice: vs[0].id }
          if (next !== s) persistSettings(next)
          return next
        })
      })
      .catch(() => {
        /* giữ preset đã seed — tránh kẹt "Đang tải giọng" */
      })
      .finally(() => window.clearTimeout(t))
    return () => {
      ac.abort()
      window.clearTimeout(t)
    }
  }, [settings.targetLang])

  useEffect(() => {
    if (!projectId || !status.running) {
      if (pollRef.current) window.clearInterval(pollRef.current)
      pollRef.current = null
      pollInFlight.current = false
      pollFailStreak.current = 0
      return
    }
    pollFailStreak.current = 0
    // 1.5s: giảm storm HTTP status (Windows WinError 10055 khi quá nhiều socket)
    pollRef.current = window.setInterval(async () => {
      if (pollInFlight.current) return
      pollInFlight.current = true
      try {
        const s = await api.status(projectId)
        if (activeProjectRef.current !== projectId) return
        pollFailStreak.current = 0
        const exportDone =
          !s.running &&
          s.step === 'export' &&
          s.progress >= 100 &&
          Boolean(s.outputRel || pendingExportUrl.current)
        setStatus(exportDone && s.error ? { ...s, error: undefined } : s)
        if (typeof s.workClipSec === 'number') {
          const wc = Math.max(0, s.workClipSec)
          if (wc !== workClipSecRef.current) {
            workClipSecRef.current = wc
            setWorkClipSec(wc)
            // Clip preview/full đổi kích thước — phải đổi URL kẻo Range cũ 416
            setVideoUrl(freshVideoUrl(`/api/projects/${projectId}/video`))
          }
        }
        const baked = Boolean(s.bakedPreferVideo)
        if (baked !== bakedPreferVideoRef.current) {
          bakedPreferVideoRef.current = baked
          setBakedPreferVideo(baked)
          setVideoUrl(freshVideoUrl(`/api/projects/${projectId}/video`))
        }
        if (typeof s.bakedSpeed === 'number' && s.bakedSpeed > 0) setBakedSpeed(s.bakedSpeed)
        if (!s.running) {
          try {
            const segs = await api.segments(projectId)
            if (activeProjectRef.current !== projectId) return
            // Cache-bust ổn định theo audioDuration (không Date.now mỗi poll → storm Range)
            const list = applyDefaultVoice(asSegmentList(segs), settings.defaultVoice).map((seg) => {
              if (!seg.audioUrl || !seg.audioFile) return seg
              const base = seg.audioUrl.split('?')[0]
              const v = Math.round((seg.audioDuration || 0) * 1000)
              return { ...seg, audioUrl: `${base}?v=${v}` }
            })
            setSegments(list)
          } catch {
            /* status đã xong — segments có thể retry sau */
          }
          if (exportDone) {
            const url = pendingExportUrl.current || `/api/projects/${projectId}/output`
            setExportUrl(url)
            setExportPath(
              pendingExportPath.current ||
                s.outputRel ||
                `backend/public/exports/${projectId}.mp4`,
            )
            pendingExportUrl.current = null
            pendingExportPath.current = null
            // Xuất xong → về trang chủ + hiện bản xuất
            setPreviewEditorOpen(false)
            setViewExportSrc(`${url}?t=${Date.now()}`)
          }
        }
      } catch {
        pollFailStreak.current += 1
        // ~7.5s (5×1.5s) backend down
        if (pollFailStreak.current >= 5) {
          setStatus((prev) => ({
            ...prev,
            running: false,
            message: prev.running
              ? 'Mất kết nối backend (đang reload?). Bấm Dịch/Xuất lại nếu cần.'
              : prev.message,
            error: 'backend_unreachable',
          }))
        }
      } finally {
        pollInFlight.current = false
      }
    }, 1500)
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
      pollRef.current = null
      pollInFlight.current = false
    }
  }, [projectId, status.running])

  // phục hồi nếu state segments bị ghi nhầm (vd. onClick truyền event DOM)
  useEffect(() => {
    if (!projectId || Array.isArray(segments)) return
    void api.segments(projectId)
      .then((segs) => setSegments(applyDefaultVoice(asSegmentList(segs), settings.defaultVoice)))
      .catch(() => setSegments([]))
  }, [projectId, segments, settings.defaultVoice])

  // Hiện ô Xem/Tải khi đã từng xuất (kể cả vừa dịch lại — bản có thể cũ)
  useEffect(() => {
    if (!projectId || status.running || exportUrl) return
    if (status.outputRel && (status.progress || 0) >= 100) {
      setExportUrl(`/api/projects/${projectId}/output`)
      setExportPath(status.outputRel)
    }
  }, [projectId, status.running, status.step, status.progress, status.outputRel, exportUrl])

  // ESC đóng popup xem export
  useEffect(() => {
    if (!viewExportSrc) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setViewExportSrc(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [viewExportSrc])

  async function onUpload(file: File) {
    const switchVersion = ++projectSwitchRef.current
    activeProjectRef.current = null
    persistSession(null)
    setProjectId(null)
    setVideoUrl(null)
    setDuration(0)
    setExportUrl(null)
    setExportPath(null)
    setSegments([])
    setOverlays([])
    setWorkClipSec(0)
    workClipSecRef.current = 0
    setBakedPreferVideo(false)
    bakedPreferVideoRef.current = false
    setBakedSpeed(1)
    setViewExportSrc(null)
    setPreviewEditorOpen(false)
    setStatus({ step: 'video', progress: 10, message: 'Đang tải video…', running: true })
    try {
      const res = await api.upload(file)
      if (projectSwitchRef.current !== switchVersion) return
      activeProjectRef.current = res.projectId
      setProjectId(res.projectId)
      persistSession(res.projectId)
      // bust browser + <video> cache khi đổi / mở lại project
      setVideoUrl(freshVideoUrl(res.videoUrl))
      setDuration(res.duration)
      if (res.settings && typeof res.settings === 'object') {
        setSettings((s) => {
          const next = { ...s, ...res.settings }
          persistSettings(next)
          return next
        })
      }
      if (res.segments?.length) {
        const voice =
          (res.settings && typeof res.settings === 'object' && res.settings.defaultVoice) ||
          settings.defaultVoice
        setSegments(applyDefaultVoice(asSegmentList(res.segments), voice))
        setStatus({
          step: 'translate',
          progress: 100,
          message: res.cached
            ? `Đã mở lại từ cache — ${res.segments.length} đoạn`
            : 'Video sẵn sàng',
          running: false,
        })
      } else {
        setStatus({
          step: 'video',
          progress: 100,
          message: res.cached ? 'Video đã có sẵn (cache)' : 'Video sẵn sàng',
          running: false,
        })
      }
    } catch (e) {
      if (projectSwitchRef.current !== switchVersion) return
      setStatus({
        step: 'video',
        progress: 0,
        message: e instanceof Error ? e.message : 'Tải video thất bại — kiểm tra server :8787',
        running: false,
        error: 'upload',
      })
    }
  }

  async function onTranslateAll(previewSec = 0) {
    if (!projectId) return
    setExportUrl(null)
    const wc = Math.max(0, previewSec)
    workClipSecRef.current = wc
    setWorkClipSec(wc)
    setVideoUrl(freshVideoUrl(`/api/projects/${projectId}/video`))
    busyAt.current = Date.now()
    setStatus({
      step: 'asr',
      progress: 0,
      message: previewSec > 0 ? `Preview ${previewSec}s…` : 'Bắt đầu nhận dạng…',
      running: true,
      error: undefined,
    })
    await api.run(projectId, { ...settings, previewSec })
    setStatus((s) => ({ ...s, running: true }))
  }

  async function onDub() {
    if (!projectId) return
    busyAt.current = Date.now()
    setProgressMinimized(false)
    // Xóa audio local cũ — tránh preview lệch khi TTS gen lại (cache file)
    setSegments((segs) =>
      (Array.isArray(segs) ? segs : []).map((s) => ({
        ...s,
        audioFile: undefined,
        audioUrl: undefined,
        audioDuration: undefined,
        videoSpeed: undefined,
      })),
    )
    setStatus({
      step: 'dub',
      progress: 0,
      message: 'Đang lồng tiếng…',
      running: true,
      error: undefined,
    })
    await api.dub(projectId, { ...settings, forceTts: true })
    setStatus((s) => ({ ...s, running: true }))
  }

  function onSettings(next: ProjectSettings) {
    const prev = settings
    // đang chạy job — đừng đổi engine (tránh xóa đoạn + nhảy về Video)
    if (status.running && next.engine !== prev.engine) return

    let out = next
    if (next.engine !== prev.engine) {
      // Lưu profile engine cũ → nạp profile engine mới (mặc định riêng nếu chưa chỉnh)
      const snapped = snapshotEngineProfile({ ...prev, engine: prev.engine })
      out = applyEngineProfile(
        { ...snapped, ...next, engine: next.engine, engineProfiles: snapped.engineProfiles },
        next.engine === 'paddleocr' ? 'paddleocr' : 'whisper',
      )
    } else {
      out = snapshotEngineProfile(next)
    }

    setSettings(out)
    persistSettings(out)
    if (projectId) {
      void api.saveSettings(projectId, out).catch(() => {
        /* ponytail: ignore transient save */
      })
    }
    // Đổi engine → bỏ đoạn cũ; matchDuration / lọc âm theo profile riêng
    if (out.engine !== prev.engine) {
      setSegments([])
      setExportUrl(null)
      setExportPath(null)
      setStatus({
        step: 'video',
        progress: 0,
        message:
          out.engine === 'paddleocr'
            ? 'Nhận dạng chữ trên màn — chạy Dịch toàn bộ'
            : 'Nhận dạng giọng nói — chạy Dịch toàn bộ rồi Lồng tiếng',
        running: false,
      })
      return
    }
    if (out.defaultVoice === prev.defaultVoice) return
    // giọng mặc định sidebar áp dụng cả list (đổi lại = đổi hết đoạn)
    setSegments((segs) => (Array.isArray(segs) ? segs : []).map((seg) => ({ ...seg, voice: out.defaultVoice })))
  }

  /** Server hay đóng dấu Adam sau Dịch — đồng bộ về default đang chọn nếu cả loạt cùng 1 giọng */
  function asSegmentList(raw: unknown): Segment[] {
    return Array.isArray(raw) ? raw : []
  }

  function applyDefaultVoice(segs: Segment[], voice: string): Segment[] {
    if (!voice || !segs.length) return segs
    const uniq = new Set(segs.map((s) => (s.voice || '').trim()).filter(Boolean))
    if (uniq.size <= 1 && (!uniq.size || !uniq.has(voice))) {
      return segs.map((s) => ({ ...s, voice }))
    }
    return segs.map((s) => {
      const v = (s.voice || '').trim()
      if (!v || v === 'system') return { ...s, voice }
      return s
    })
  }

  async function onExport(exportSegments?: Segment[]) {
    if (!projectId) return
    setExportUrl(null)
    setExportPath(null)
    setViewExportSrc(null)
    setProgressMinimized(false)
    busyAt.current = Date.now()
    // độ dài xuất = lần dịch gần nhất (status đã nói Preview Ns / full), không theo ô số khi đã Dịch cả video
    const audioHint =
      settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals'
        ? ' · xóa lời'
        : settings.processOriginalAudio && settings.originalAudioMode === 'vocals'
          ? ' · giữ lời'
          : settings.processOriginalAudio && settings.originalAudioMode === 'mute'
            ? ' · tắt âm gốc'
            : ''
    setStatus({
      step: 'export',
      progress: 0,
      message:
        (settings.coverHardsubs && settings.burnSubs && settings.targetLang !== 'none'
          ? 'Đang xuất (che chữ cũ + chèn bản dịch)'
          : settings.burnSubs && settings.targetLang !== 'none'
            ? settings.captionPlacement === 'above'
              ? 'Đang xuất (chèn bản dịch phía trên)'
              : 'Đang xuất (chèn bản dịch phía dưới)'
            : settings.coverHardsubs
              ? 'Đang xuất (che chữ cũ)'
              : 'Đang xuất') + `${audioHint}…`,
      running: true,
      error: undefined,
    })
    const segs = Array.isArray(exportSegments) ? exportSegments : segments
    if (Array.isArray(exportSegments)) {
      setSegments(exportSegments)
    }
    const res = await api.export(projectId, settings, segs)
    pendingExportUrl.current = res.url
    pendingExportPath.current = res.exports || res.path || null
    setStatus((s) => ({ ...s, running: true }))
  }

  async function onRevealOutput() {
    if (!projectId) return
    try {
      const res = await api.revealOutput(projectId)
      setExportPath(res.path)
    } catch (e) {
      setStatus((s) => ({
        ...s,
        message: e instanceof Error ? e.message : 'Không mở được thư mục',
      }))
    }
  }

  function onViewExport() {
    if (!projectId) return
    setViewExportSrc(`/api/projects/${projectId}/output?t=${Date.now()}`)
  }

  function onCloseViewExport() {
    setViewExportSrc(null)
  }

  async function onCancel() {
    if (!projectId || !status.running) return
    // chỉ chặn double-click cực sớm (mount Huỷ)
    if (Date.now() - busyAt.current < 400) return
    const stepNow = status.step
    // optimistic — đừng chờ server
    setStatus({
      step: stepNow,
      progress: 0,
      message:
        stepNow === 'export'
          ? 'Đã huỷ xuất bản'
          : stepNow === 'dub'
            ? 'Đã huỷ lồng tiếng'
            : 'Đang huỷ…',
      running: false,
      error: 'cancelled',
    })
    try {
      await api.cancel(projectId)
    } catch {
      /* flag server có thể fail; UI đã dừng */
    }
  }

  async function onSegmentChange(seg: Segment) {
    setSegments((prev) => (Array.isArray(prev) ? prev : []).map((s) => (s.id === seg.id ? seg : s)))
    if (!projectId) return
    try {
      await api.updateSegment(projectId, seg)
    } catch {
      /* keep local edit */
    }
  }

  async function onSegmentsReplace(next: Segment[]) {
    const ordered = [...next]
      .sort((a, b) => a.start - b.start || a.end - b.end)
      .map((s, i) => ({ ...s, index: i }))
    setSegments(ordered)
    if (!projectId) return
    try {
      const saved = await api.replaceSegments(projectId, ordered)
      if (Array.isArray(saved)) setSegments(applyDefaultVoice(asSegmentList(saved), settings.defaultVoice))
    } catch {
      /* keep local */
    }
  }

  function onPreviewRebaked(res: {
    segments: Segment[]
    overlays?: TextOverlay[]
    workClipSec: number
    duration: number
    bakedPreferVideo: boolean
    bakedSpeed: number
    videoUrl: string
  }) {
    setSegments(applyDefaultVoice(asSegmentList(res.segments), settings.defaultVoice))
    if (Array.isArray(res.overlays)) setOverlays(res.overlays)
    const wc = Math.max(0, res.workClipSec)
    workClipSecRef.current = wc
    setWorkClipSec(wc)
    if (res.duration > 0) setDuration(res.duration)
    bakedPreferVideoRef.current = res.bakedPreferVideo
    setBakedPreferVideo(res.bakedPreferVideo)
    setBakedSpeed(res.bakedSpeed > 0 ? res.bakedSpeed : res.bakedPreferVideo ? 0.8 : 1)
    setVideoUrl(freshVideoUrl(res.videoUrl))
  }

  useEffect(() => {
    if (!projectId) {
      setOverlays([])
      return
    }
    let cancelled = false
    void api.overlays(projectId)
      .then((items) => {
        if (!cancelled && activeProjectRef.current === projectId) setOverlays(items)
      })
      .catch(() => {
        if (!cancelled && activeProjectRef.current === projectId) setOverlays([])
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  async function onOverlayChange(overlay: TextOverlay, isNew = false) {
    if (!projectId) return
    setOverlays((current) =>
      isNew ? [...current, overlay] : current.map((item) => (item.id === overlay.id ? overlay : item)),
    )
    try {
      const saved = isNew
        ? await api.createOverlay(projectId, overlay)
        : await api.updateOverlay(projectId, overlay)
      setOverlays((current) => current.map((item) => (item.id === saved.id ? saved : item)))
    } catch {
      void api.overlays(projectId).then(setOverlays)
    }
  }

  async function onOverlayDelete(overlayId: string) {
    if (!projectId) return
    setOverlays((current) => current.filter((item) => item.id !== overlayId))
    try {
      await api.deleteOverlay(projectId, overlayId)
    } catch {
      void api.overlays(projectId).then(setOverlays)
    }
  }

  async function onOverlaysReplace(next: TextOverlay[]) {
    setOverlays(next)
    if (!projectId) return
    try {
      const saved = await api.replaceOverlays(projectId, next)
      if (Array.isArray(saved)) setOverlays(saved)
    } catch {
      void api.overlays(projectId).then(setOverlays)
    }
  }

  const step: Step = status.step

  useEffect(() => {
    sidebarWidthRef.current = sidebarWidth
  }, [sidebarWidth])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const drag = sidebarDrag.current
      if (!drag) return
      const next = Math.max(
        SIDEBAR_MIN,
        Math.min(SIDEBAR_MAX, drag.startW + (e.clientX - drag.startX)),
      )
      sidebarWidthRef.current = next
      setSidebarWidth(next)
    }
    const onUp = () => {
      if (!sidebarDrag.current) return
      sidebarDrag.current = null
      document.body.classList.remove('resizing-sidebar')
      try {
        localStorage.setItem(SIDEBAR_W_LS, String(sidebarWidthRef.current))
      } catch {
        /* ignore */
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  const onSidebarResizeStart = (e: ReactMouseEvent) => {
    e.preventDefault()
    sidebarDrag.current = { startX: e.clientX, startW: sidebarWidthRef.current }
    document.body.classList.add('resizing-sidebar')
  }

  const editorOpen = previewEditorOpen && !!videoUrl && !!projectId

  return (
    <div className={editorOpen && appMode === 'clone' ? 'app app--editor' : 'app'}>
      {!(editorOpen && appMode === 'clone') && (
      <Header
        hardware={hw}
        dark={dark}
        mode={appMode}
        onModeChange={(m) => {
          setAppMode(m)
          if (m === 'clone') return
          setPreviewEditorOpen(false)
        }}
        onToggleTheme={() => setDark(d => !d)}
        onOpenConfig={() => {
          setConfigSection(forceSetup && !setupReady ? 'setup' : 'cloud')
          setConfigOpen(true)
        }}
      />
      )}
      <ConfigModal
        open={configOpen}
        initialSection={configSection}
        forceSetup={forceSetup && !setupReady}
        onSetupReady={() => {
          setSetupReady(true)
          setForceSetup(false)
        }}
        onClose={() => {
          if (forceSetup && !setupReady) return
          setConfigOpen(false)
        }}
      />
      {appMode === 'tts' ? (
        <TtsPage
          voices={voices}
          onRefreshVoices={(lang) => {
            const l = lang || (settings.targetLang === 'none' ? 'vi' : settings.targetLang)
            void api.voices(l).then(setVoices).catch(() => {})
          }}
        />
      ) : appMode === 'download' ? (
        <DownloadPage />
      ) : appMode === 'film' ? (
        <FilmPage />
      ) : appMode === 'batch' ? (
        <BatchPage />
      ) : editorOpen ? (
        <LivePreviewEditor
          key={projectId}
          videoUrl={videoUrl}
          mediaDuration={duration}
          workClipSec={workClipSec}
          bakedPreferVideo={bakedPreferVideo}
          bakedSpeed={bakedSpeed}
          projectId={projectId}
          segments={segments}
          settings={settings}
          voices={voices}
          busy={status.running}
          jobStep={status.step}
          jobProgress={status.progress}
          jobMessage={status.message}
          onDub={onDub}
          onBack={() => setPreviewEditorOpen(false)}
          onChange={onSegmentChange}
          onSegmentsReplace={onSegmentsReplace}
          onPreviewRebaked={onPreviewRebaked}
          onExport={onExport}
          onSettings={onSettings}
          overlays={overlays}
          onOverlayChange={onOverlayChange}
          onOverlayDelete={onOverlayDelete}
          onOverlaysReplace={onOverlaysReplace}
        />
      ) : (
      <div
        className="workspace"
        style={{ gridTemplateColumns: `${sidebarWidth}px 6px 1fr` }}
      >
        <ProjectSidebar
          videoUrl={videoUrl}
          settings={settings}
          voices={voices}
          busy={status.running}
          onSettings={onSettings}
          onUpload={onUpload}
          onTranslateAll={() => onTranslateAll(0)}
          onPreview={() =>
            onTranslateAll(Math.max(5, Math.min(600, settings.previewSec || 20)))
          }
          onCancel={onCancel}
        />
        <div
          className="sidebar-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Kéo đổi độ rộng menu"
          onMouseDown={onSidebarResizeStart}
        />
        <main className="main">
          <PipelineStepper
            step={step}
            onDub={onDub}
            onPreviewEditor={() => {
              // Cài Sidebar = quy tắc đầu vào: ghi server rồi mới mở xem/sửa
              if (projectId) {
                void api.saveSettings(projectId, settings).catch(() => { /* ignore */ })
              }
              setPreviewEditorOpen(true)
            }}
            onExport={onExport}
            canDub={segments.length > 0 && !status.running}
            canExport={
              segments.length > 0 &&
              !status.running &&
              (settings.targetLang === 'none' ||
                segments.some((s) => s.translation.trim()))
            }
          />
          <div className="main-head">
            <div>
              <h2>Kịch bản lồng tiếng</h2>
              <p className="status-line">
                {status.running
                  ? `${status.message || 'Đang xử lý…'} — ${Math.round(status.progress)}% (vẫn chạy, % có thể đứng lâu)`
                  : status.message}
              </p>
            </div>
            <div className="meta">
              <span className="seg-count">{segments.length} đoạn thoại</span>
              {duration > 0 && <span>{fmtDuration(duration)}</span>}
            </div>
          </div>
          {exportUrl && (
            <div className="export-banner">
              <div className="export-banner-text">
                <strong>
                  {status.step === 'export'
                    ? 'Video đã xuất xong'
                    : 'Có bản xuất trước — Xuất bản lại nếu vừa dịch mới'}
                </strong>
                <code>{exportPath || `backend/public/exports/${projectId}.mp4`}</code>
              </div>
              <div className="export-banner-actions">
                <button type="button" className="export-dl" onClick={onViewExport}>
                  Xem
                </button>
                <a
                  className="export-dl"
                  href={`/api/projects/${projectId}/output?download=1`}
                  download={`video-clone-${projectId}.mp4`}
                >
                  Tải xuống
                </a>
                <button type="button" className="export-reveal" onClick={onRevealOutput}>
                  Mở thư mục
                </button>
              </div>
            </div>
          )}
          <SegmentList
            segments={segments}
            voices={voices}
            defaultVoice={settings.defaultVoice}
            targetLang={settings.targetLang}
            sourceLang={settings.sourceLang}
            translator={settings.translator}
            videoUrl={videoUrl}
            projectId={projectId}
            onChange={onSegmentChange}
          />
        </main>
      </div>
      )}
      {viewExportSrc && (
        <div
          className="export-modal-backdrop"
          role="presentation"
          onClick={onCloseViewExport}
          onKeyDown={(e) => e.key === 'Escape' && onCloseViewExport()}
        >
          <div
            className="export-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Xem video đã xuất"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="export-modal-head">
              <strong>Video đã xuất</strong>
              <button type="button" className="export-modal-close" onClick={onCloseViewExport}>
                Đóng
              </button>
            </div>
            <video className="export-modal-video" src={viewExportSrc} controls autoPlay playsInline />
          </div>
        </div>
      )}
      <ProgressPopup
        active={status.running || Boolean(status.error && status.error !== 'cancelled')}
        minimized={progressMinimized}
        running={status.running}
        title={
          status.step === 'dub'
            ? 'Lồng tiếng'
            : status.step === 'export'
              ? (status.error ? 'Xuất video thất bại' : 'Xuất video')
              : status.step === 'translate' || status.step === 'asr'
                ? 'Dịch / nhận dạng'
                : 'Đang xử lý'
        }
        message={status.message}
        progress={status.progress}
        error={status.error}
        onMinimize={() => {
          setProgressMinimized(true)
          // Đóng popup lỗi — tránh hiện mãi sau lần xuất fail
          if (!status.running && status.error) {
            setStatus((s) => ({ ...s, error: undefined, message: s.message || 'Đã đóng báo lỗi' }))
          }
        }}
        onRestore={() => setProgressMinimized(false)}
        onCancel={status.running ? onCancel : undefined}
      />
    </div>
  )
}
