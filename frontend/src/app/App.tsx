import {
  Suspense,
  lazy,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import Header, { type AppMode } from '@/shared/components/Header'
import ProgressPopup from '@/shared/components/ProgressPopup'
import ProjectSidebar from '@/features/project/ProjectSidebar'
import PipelineStepper from '@/features/project/PipelineStepper'
import SegmentList from '@/features/project/SegmentList'
import ConfigModal from '@/features/configuration/ConfigModal'

// Hai màn nặng nhất (editor + TTS studio) tải theo nhu cầu — bundle chính
// không còn vượt cảnh báo 600KB của vite.
const LivePreviewEditor = lazy(() => import('@/features/editor/LivePreviewEditor'))
const TtsPage = lazy(() => import('@/pages/TtsPage'))
import DownloadPage from '@/pages/DownloadPage'
import FilmPage from '@/pages/FilmPage'
import BatchPage from '@/pages/BatchPage'
import RendersPage from '@/pages/RendersPage'
import VideoCleanerPage from '@/pages/VideoCleanerPage'
import SrtImagePage from '@/pages/SrtImagePage'
import SrtExportPage from '@/pages/SrtExportPage'
import LicensePage from '@/features/license/LicensePage'
import { licenseApi, type LicenseStatus } from '@/features/license/license.api'
import { ExportSuccessModal } from '@/features/editor/ExportSuccessModal'
import { api } from '@/features/project/project.api'
import { expandSegmentsForList } from '@/features/project/expandCompound'
import type { HardwareInfo, JobStatus, ProjectSettings, Step, TextOverlay } from '@/features/project/project.types'
import { loadAppMode, persistAppMode } from '@/app/appMode'
import {
  applyDefaultVoice,
  asSegmentList,
  useSegmentEditing,
} from '@/features/project/useSegmentEditing'
import { useProjectMedia } from '@/features/project/useProjectMedia'
import { useDubControl } from '@/features/project/useDubControl'
import { useExportFlow } from '@/features/project/useExportFlow'
import { useJobPolling } from '@/features/project/useJobPolling'
import {
  SIDEBAR_MAX,
  SIDEBAR_W_LS,
  THEME_LS,
  SIDEBAR_MIN,
  applyEngineProfile,
  idleStatus,
  loadSetupGate,
  loadSettings,
  loadSidebarWidth,
  loadTheme,
  persistSession,
  persistSettings,
  persistSetupGate,
  snapshotEngineProfile,
  useSessionRestore,
} from '@/app/useProjectSession'
import './App.css'

function fmtDuration(sec: number) {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

const EMPTY_LICENSE: LicenseStatus = {
  valid: false,
  configured: false,
  keyMasked: '',
  remainingDay: 0,
  expiresAt: null,
  activationLimit: 0,
  message: 'Đang kiểm tra bản quyền…',
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
  const [configSection, setConfigSection] = useState<'setup' | 'cloud' | 'tts'>(() =>
    loadSetupGate() ? 'cloud' : 'setup',
  )
  const [setupGatePassed, setSetupGatePassed] = useState(loadSetupGate)
  /** Backend checks done once — không chặn UI sau khi đã qua cổng thiết lập. */
  const [setupChecked, setSetupChecked] = useState(false)
  const [setupMissingRequired, setSetupMissingRequired] = useState(false)
  const [licenseStatus, setLicenseStatus] = useState<LicenseStatus | null>(null)
  const [sidebarWidth, setSidebarWidth] = useState(loadSidebarWidth)
  const sidebarWidthRef = useRef(sidebarWidth)
  const sidebarDrag = useRef<{ startX: number; startW: number } | null>(null)
  const [projectId, setProjectId] = useState<string | null>(null)
  const [overlays, setOverlays] = useState<TextOverlay[]>([])
  const [status, setStatus] = useState<JobStatus>(idleStatus)
  /** App desktop: file đã trên máy — chỉ Xem / Mở thư mục */
  const [isDesktopApp, setIsDesktopApp] = useState(false)
  const [previewEditorOpen, setPreviewEditorOpen] = useState(false)
  const [progressMinimized, setProgressMinimized] = useState(false)
  const [ttsSideOpen, setTtsSideOpen] = useState(false)
  const [clearingCache, setClearingCache] = useState(false)
  const [cacheToast, setCacheToast] = useState<string | null>(null)
  /** Guards project/video switches from late restore, upload, and poll responses. */
  const projectSwitchRef = useRef(0)
  const activeProjectRef = useRef<string | null>(null)

  const {
    segments,
    setSegments,
    flushSegmentSave,
    onSegmentChange,
    onSegmentsReplace,
  } = useSegmentEditing({ projectId, defaultVoice: settings.defaultVoice })
  const {
    videoUrl,
    setVideoUrl,
    duration,
    setDuration,
    workClipSec,
    setWorkClipSec,
    workClipSecRef,
    bakedPreferVideo,
    setBakedPreferVideo,
    bakedPreferVideoRef,
    bakedSpeed,
    setBakedSpeed,
    hasBakedSpeed,
    setHasBakedSpeed,
    freshVideoUrl,
    onPreviewRebaked,
    onRestoreBakedSpeed,
  } = useProjectMedia({
    projectId,
    defaultVoice: settings.defaultVoice,
    setSegments,
    setOverlays,
  })
  const { busyAt, releaseDubLock, onDub, onCancel } = useDubControl({
    projectId,
    status,
    setStatus,
    settings,
    setSegments,
    setProgressMinimized,
    flushSegmentSave,
  })
  const {
    exportUrl,
    setExportUrl,
    exportPath,
    setExportPath,
    viewExportSrc,
    setViewExportSrc,
    exportSuccessOpen,
    setExportSuccessOpen,
    lastExportedTypes,
    pendingExportUrl,
    applyExportDone,
    onExport,
    onRevealOutput,
    onViewExport,
  } = useExportFlow({
    projectId,
    status,
    setStatus,
    settings,
    segments,
    setSegments,
    busyAt,
    setProgressMinimized,
  })
  useJobPolling({
    projectId,
    running: status.running,
    activeProjectRef,
    setStatus,
    releaseDubLock,
    defaultVoice: settings.defaultVoice,
    setSegments,
    workClipSecRef,
    setWorkClipSec,
    setDuration,
    setVideoUrl,
    freshVideoUrl,
    bakedPreferVideoRef,
    setBakedPreferVideo,
    setBakedSpeed,
    setHasBakedSpeed,
    pendingExportUrl,
    applyExportDone,
  })
  useSessionRestore({
    projectSwitchRef,
    activeProjectRef,
    setProjectId,
    settings,
    setSettings,
    setStatus,
    setSegments,
    workClipSecRef,
    setWorkClipSec,
    setDuration,
    setVideoUrl,
    freshVideoUrl,
    bakedPreferVideoRef,
    setBakedPreferVideo,
    setBakedSpeed,
    setHasBakedSpeed,
    setExportUrl,
    setExportPath,
    releaseDubLock,
    busyAt,
  })

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
    api.getConfig()
      .then((c) => setIsDesktopApp(Boolean(c.desktop)))
      .catch(() => setIsDesktopApp(false))
  }, [])

  useEffect(() => {
    if (!setupGatePassed) return
    let cancelled = false
    const refresh = () => licenseApi.status()
      .then((next) => { if (!cancelled) setLicenseStatus(next) })
      .catch((error) => {
        if (!cancelled) setLicenseStatus({
          ...EMPTY_LICENSE,
          message: error instanceof Error ? error.message : 'Không thể kiểm tra key',
        })
      })
    void refresh()
    const timer = window.setInterval(refresh, 10 * 60 * 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [setupGatePassed])

  useEffect(() => {
    api.hardware().then(setHw).catch(() => setHw({ label: 'Local', accel: 'cpu' }))
  }, [])

  function passSetupGate() {
    persistSetupGate()
    setSetupGatePassed(true)
    setSetupMissingRequired(false)
    setConfigSection('cloud')
    setConfigOpen(false)
    void api.passSetupGate().catch(() => {
      /* file gate — localStorage vẫn giữ */
    })
    const l = settings.targetLang === 'none' ? 'vi' : settings.targetLang
    void api.voices(l).then(setVoices).catch(() => {})
  }

  // Lần đầu: Thiết lập. Lần sau: đọc gate từ disk (ổn định hơn localStorage theo port).
  useEffect(() => {
    let cancelled = false

    async function bootstrapSetup() {
      let gatePassed = loadSetupGate()

      for (let attempt = 0; attempt < 60; attempt++) {
        if (cancelled) return
        try {
          await api.health()
          break
        } catch {
          await new Promise((r) => window.setTimeout(r, 200))
        }
      }
      if (cancelled) return

      try {
        const g = await api.getSetupGate()
        if (cancelled) return
        if (g.passed) {
          gatePassed = true
          persistSetupGate()
          setSetupGatePassed(true)
        } else if (gatePassed) {
          // localStorage cũ → đồng bộ lên disk lần này
          await api.passSetupGate().catch(() => {})
          setSetupGatePassed(true)
        }
      } catch {
        /* giữ localStorage */
      }

      if (!gatePassed) {
        setConfigSection('setup')
        setConfigOpen(true)
      }

      try {
        const c = await api.systemChecks(false, false)
        if (cancelled) return
        setSetupChecked(true)
        setSetupMissingRequired(!c.ok)
        // Chỉ ép mở Thiết lập khi chưa qua cổng — lần sau vào thẳng app.
        if (!c.ok && !gatePassed && !loadSetupGate()) {
          setConfigSection('setup')
          setConfigOpen(true)
        }
      } catch {
        if (cancelled) return
        setSetupChecked(true)
        if (!gatePassed) setSetupMissingRequired(true)
      }
    }

    void bootstrapSetup()
    return () => {
      cancelled = true
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
        message: e instanceof Error ? e.message : 'Tải video thất bại — kiểm tra server API',
        running: false,
        error: 'upload',
      })
    }
  }

  async function onClearCache(parts: string[]) {
    if (!projectId || clearingCache || !parts.length) return
    setClearingCache(true)
    setCacheToast(null)
    try {
      if (status.running) {
        try {
          await api.cancel(projectId)
        } catch {
          /* best-effort */
        }
      }
      const res = await api.clearProjectCache(projectId, parts)
      // Reset UI theo phần đã xóa — giữ video nguồn
      if (res.clearedSegments) {
        setSegments([])
        setOverlays([])
        setPreviewEditorOpen(false)
      } else {
        const dropCover = Boolean(res.clearedCovers)
        const dropTts = Boolean(res.clearedTts)
        if (dropCover || dropTts) {
          setSegments((segs) =>
            (Array.isArray(segs) ? segs : []).map((s) => ({
              ...s,
              ...(dropCover
                ? {
                    bbox: undefined,
                    captionLayout: undefined,
                    bboxInherited: undefined,
                  }
                : {}),
              ...(dropTts
                ? {
                    audioFile: undefined,
                    audioUrl: undefined,
                    audioDuration: undefined,
                    videoSpeed: undefined,
                  }
                : {}),
            })),
          )
        }
      }
      if (res.clearedSegments || parts.includes('render') || parts.includes('preview')) {
        setExportUrl(null)
        setExportPath(null)
        setViewExportSrc(null)
      }
      if (parts.includes('preview') || res.clearedSegments) {
        setWorkClipSec(0)
        workClipSecRef.current = 0
        setBakedPreferVideo(false)
        bakedPreferVideoRef.current = false
        setBakedSpeed(1)
        setHasBakedSpeed(false)
      }
      if (res.clearedFrontend || parts.includes('frontend')) {
        try {
          localStorage.removeItem(`videoclone.videoClips.${projectId}`)
          localStorage.removeItem(`videoclone.bgClips.${projectId}`)
        } catch {
          /* ignore */
        }
      }
      setVideoUrl(freshVideoUrl(`/api/projects/${projectId}/video`))
      setStatus({
        step: res.clearedSegments ? 'video' : status.step,
        progress: 100,
        message: res.message || 'Đã xóa cache',
        running: false,
        error: undefined,
      })
      setCacheToast(
        res.ok
          ? res.message?.startsWith('Đã xóa toàn bộ')
            ? '✅ Đã xóa toàn bộ cache.'
            : `✅ ${res.message || 'Đã xóa cache đã chọn.'}`
          : 'Một số cache chưa được xóa.',
      )
      window.setTimeout(() => setCacheToast(null), 4200)
    } catch (e) {
      setCacheToast(
        e instanceof Error ? e.message : 'Một số cache chưa được xóa.',
      )
      window.setTimeout(() => setCacheToast(null), 5200)
    } finally {
      setClearingCache(false)
    }
  }

  async function onTranslateAll(runWindowSec = 0) {
    if (!projectId || status.running) return
    setExportUrl(null)
    // 0 = Dịch cả video (full); >0 = ▶ Preview Ns — tách khỏi ô settings.previewSec
    const wc = Math.max(0, runWindowSec)
    workClipSecRef.current = wc
    setWorkClipSec(wc)
    // Full: xóa clip timeline local (đang kẹt độ dài preview cũ)
    if (wc <= 0 && typeof localStorage !== 'undefined') {
      try {
        localStorage.removeItem(`videoclone.videoClips.${projectId}`)
        localStorage.removeItem(`videoclone.bgClips.${projectId}`)
      } catch { /* ignore */ }
    }
    // Bust video URL ngay — tránh stream preview_Ns / full lẫn nhau
    setVideoUrl(freshVideoUrl(`/api/projects/${projectId}/video`))
    // Xóa segments UI ngay — tránh hiện bản full trong lúc chạy preview (và ngược lại)
    setSegments([])
    busyAt.current = Date.now()
    setStatus({
      step: 'asr',
      progress: 0,
      message: wc > 0 ? `Preview ${wc}s…` : 'Dịch cả video (full)…',
      running: true,
      error: undefined,
    })
    // previewSec = ô UI (giữ nguyên); runPreviewSec = cửa sổ lần này
    await api.run(projectId, {
      ...settings,
      runPreviewSec: wc,
    })
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

  async function editRenderedProject(id: string) {
    const switchVersion = ++projectSwitchRef.current
    const [st, segs] = await Promise.all([api.status(id), api.segments(id)])
    if (projectSwitchRef.current !== switchVersion) return
    activeProjectRef.current = id
    persistSession(id)
    setProjectId(id)
    setVideoUrl(freshVideoUrl(`/api/projects/${id}/video`))
    const wc = typeof st.workClipSec === 'number' ? Math.max(0, st.workClipSec) : 0
    workClipSecRef.current = wc
    setWorkClipSec(wc)
    const dur = Number(st.duration || 0)
    setDuration(wc > 0 ? wc : dur)
    const bs = typeof st.bakedSpeed === 'number' && st.bakedSpeed > 0 ? st.bakedSpeed : 1
    const speedOff1 = Math.abs(bs - 1) > 0.02
    const baked = Boolean(st.bakedPreferVideo) && speedOff1
    bakedPreferVideoRef.current = baked
    setBakedPreferVideo(baked)
    setBakedSpeed(bs)
    setHasBakedSpeed(Boolean((st as { hasBakedSpeed?: boolean }).hasBakedSpeed) || speedOff1)
    const extra = st as JobStatus & { settings?: Partial<ProjectSettings> }
    const mergedVoice = extra.settings?.defaultVoice || settings.defaultVoice
    setSegments(applyDefaultVoice(asSegmentList(segs), mergedVoice))
    if (extra.settings && typeof extra.settings === 'object') {
      setSettings((current) => {
        const next = { ...current, ...extra.settings }
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
    setExportUrl(!st.running && st.outputRel && (st.progress || 0) >= 100 ? `/api/projects/${id}/output` : null)
    setExportPath(!st.running && st.outputRel && (st.progress || 0) >= 100 ? st.outputRel : null)
    setViewExportSrc(null)
    setAppMode('clone')
    setPreviewEditorOpen(true)
  }

  // Chỉ sau Bắt đầu / đã lưu cổng — tránh Header+Modal+workspace cùng chiếm CSS grid → trắng.
  const firstRunBlocked = !setupGatePassed
  const appUsable = setupGatePassed
  const licenseBlocked = appUsable && !licenseStatus?.valid
  const configModalOpen = configOpen || firstRunBlocked

  return (
    <div className={editorOpen && appMode === 'clone' ? 'app app--editor' : 'app'}>
      {!appUsable ? (
        <p className="cfg-boot-msg">
          {setupChecked
            ? setupMissingRequired
              ? 'Cài đủ thành phần bắt buộc rồi bấm Bắt đầu'
              : 'Sẵn sàng — bấm Bắt đầu'
            : 'Đang kết nối backend…'}
        </p>
      ) : null}
      {appUsable && !licenseBlocked && !(editorOpen && appMode === 'clone') && (
      <Header
        hardware={hw}
        dark={dark}
        mode={appMode}
        licenseStatus={licenseStatus || undefined}
        menuOpen={ttsSideOpen}
        onMenuClick={
          appMode === 'tts' ? () => setTtsSideOpen((o) => !o) : undefined
        }
        onModeChange={(m) => {
          setAppMode(m)
          setTtsSideOpen(false)
          if (m === 'clone') return
          setPreviewEditorOpen(false)
        }}
        onToggleTheme={() => setDark(d => !d)}
        onOpenConfig={() => {
          if (setupGatePassed) setConfigSection('cloud')
          setConfigOpen(true)
        }}
      />
      )}
      <ConfigModal
        open={configModalOpen}
        initialSection={configSection}
        forceSetup={firstRunBlocked}
        onSetupReady={passSetupGate}
        onSaved={() => {
          const l = settings.targetLang === 'none' ? 'vi' : settings.targetLang
          void api.voices(l).then(setVoices).catch(() => {})
        }}
        onClose={() => {
          if (firstRunBlocked) return
          setConfigOpen(false)
          // đóng config cũng refresh voices (user có thể vừa lưu key)
          const l = settings.targetLang === 'none' ? 'vi' : settings.targetLang
          void api.voices(l).then(setVoices).catch(() => {})
        }}
      />
      {licenseBlocked && (
        <LicensePage
          status={licenseStatus || EMPTY_LICENSE}
          gate
          onStatusChange={setLicenseStatus}
        />
      )}
      <Suspense fallback={<div className="page-loading" role="status">Đang tải…</div>}>
      {appUsable && !licenseBlocked ? (
      appMode === 'tts' ? (
        <TtsPage
          voices={voices}
          sideOpen={ttsSideOpen}
          onSideOpenChange={setTtsSideOpen}
          onRefreshVoices={(lang) => {
            const l = lang || (settings.targetLang === 'none' ? 'vi' : settings.targetLang)
            void api.voices(l).then(setVoices).catch(() => {})
          }}
        />
      ) : appMode === 'license' ? (
        <LicensePage status={licenseStatus || EMPTY_LICENSE} onStatusChange={setLicenseStatus} />
      ) : appMode === 'download' ? (
        <DownloadPage
          onUseInClone={(pid, meta) => {
            const switchVersion = ++projectSwitchRef.current
            activeProjectRef.current = pid
            persistSession(pid)
            setProjectId(pid)
            setVideoUrl(freshVideoUrl(meta.videoUrl || `/api/projects/${pid}/video`))
            setDuration(meta.duration || 0)
            setExportUrl(null)
            setExportPath(null)
            setSegments([])
            setOverlays([])
            setWorkClipSec(0)
            workClipSecRef.current = 0
            setBakedPreferVideo(false)
            bakedPreferVideoRef.current = false
            setBakedSpeed(1)
            setHasBakedSpeed(false)
            setViewExportSrc(null)
            setPreviewEditorOpen(false)
            setStatus({
              step: 'video',
              progress: 100,
              message: 'Video sẵn sàng (từ Download)',
              running: false,
            })
            if (projectSwitchRef.current === switchVersion) {
              setAppMode('clone')
            }
          }}
        />
      ) : appMode === 'film' ? (
        <FilmPage />
      ) : appMode === 'batch' ? (
        <BatchPage />
      ) : appMode === 'cleaner' ? (
        <VideoCleanerPage />
      ) : appMode === 'srt-image' ? (
        <SrtImagePage />
      ) : appMode === 'srt-export' ? (
        <SrtExportPage />
      ) : appMode === 'renders' ? (
        <RendersPage onEdit={editRenderedProject} />
      ) : editorOpen ? (
        <LivePreviewEditor
          key={projectId}
          videoUrl={videoUrl}
          mediaDuration={duration}
          workClipSec={workClipSec}
          bakedPreferVideo={bakedPreferVideo}
          bakedSpeed={bakedSpeed}
          hasBakedSpeed={hasBakedSpeed}
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
          onRestoreBakedSpeed={onRestoreBakedSpeed}
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
          onPreview={(previewSec) => {
            const sec = Math.max(5, Math.min(600, previewSec || settings.previewSec || 20))
            // Chỉ ▶ Preview mới ghi ô settings.previewSec — Dịch cả không đụng
            if (sec !== settings.previewSec) {
              const next = { ...settings, previewSec: sec }
              setSettings(next)
              void api.saveSettings(projectId!, next).catch(() => {})
            }
            void onTranslateAll(sec)
          }}
          onCancel={onCancel}
          onClearCache={onClearCache}
          clearingCache={clearingCache}
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
            canDub={
              (segments.length > 0 || expandSegmentsForList(segments).length > 0)
              && !status.running
            }
            canPreviewEditor={Boolean(projectId && videoUrl)}
            canExport={(() => {
              if (status.running) return false
              // Alt+G: shell rỗng chữ — check children đã bung
              const flat = expandSegmentsForList(segments)
              if (!flat.length && !segments.length) return false
              if (settings.targetLang === 'none') return true
              return flat.some((s) => (s.translation || '').trim())
                || segments.some((s) => (s.translation || '').trim())
            })()}
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
              <span className="seg-count">
                {expandSegmentsForList(segments).length || segments.length} đoạn thoại
              </span>
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
                {!isDesktopApp && (
                  <a
                    className="export-dl"
                    href={`/api/projects/${projectId}/output?download=1`}
                    download={`video-clone-${projectId}.mp4`}
                  >
                    Tải xuống
                  </a>
                )}
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
      )
      ) : null}
      </Suspense>
      {cacheToast && (
        <div className="cache-toast" role="status">
          {cacheToast}
        </div>
      )}
      <ExportSuccessModal
        isOpen={exportSuccessOpen}
        onClose={() => setExportSuccessOpen(false)}
        onRevealFolder={() => {
          void onRevealOutput()
        }}
        onOpenProject={() => { setExportSuccessOpen(false); setPreviewEditorOpen(true) }}
        videoSrc={viewExportSrc}
        message={status.message || ''}
        exportedTypes={lastExportedTypes}
        renderName={exportPath?.split('/').pop()?.replace(/\.[^.]+$/i, '') || undefined}
      />
      <ProgressPopup
        active={
          status.running
          || Boolean(
            status.error
            && status.error !== 'cancelled'
            && status.error !== 'stale_job',
          )
        }
        minimized={progressMinimized}
        running={status.running}
        title={
          status.step === 'dub'
            ? (
                status.error && status.error !== 'cancelled'
                  ? 'Lồng tiếng thất bại'
                  : status.running
                    ? 'Đang lồng tiếng'
                    : 'Lồng tiếng'
              )
            : status.step === 'export'
              ? (status.error ? 'Xuất video thất bại' : 'Xuất video')
              : status.step === 'translate' || status.step === 'asr'
                ? (status.error ? 'Dịch / nhận dạng thất bại' : 'Dịch / nhận dạng')
                : 'Đang xử lý'
        }
        message={
          // error code ngắn ("dub") → đừng thay message; đã xử lý trong ProgressPopup
          status.message
          || (
            status.error && status.error !== 'cancelled' && status.error.length > 24
              ? status.error
              : status.error === 'dub'
                ? 'Lồng tiếng thất bại — xem log backend hoặc thử lại'
                : undefined
          )
        }
        progress={status.progress}
        error={
          status.error === 'dub' && !(status.message || '').trim()
            ? 'Lồng tiếng thất bại'
            : status.error
        }
        onMinimize={() => {
          // Job đang chạy: chỉ thu nhỏ (chạy nền). Lỗi xong: dismiss hẳn.
          if (status.running) {
            setProgressMinimized(true)
            return
          }
          if (status.error && status.error !== 'cancelled') {
            setStatus((s) => ({
              ...s,
              error: undefined,
              message: '',
              progress: 0,
            }))
            setProgressMinimized(false)
            if (projectId) {
              void api.dismissStatus(projectId).catch(() => {
                /* offline — UI đã clear */
              })
            }
            return
          }
          setProgressMinimized(true)
        }}
        onRestore={() => setProgressMinimized(false)}
        onCancel={status.running ? onCancel : undefined}
      />
    </div>
  )
}
