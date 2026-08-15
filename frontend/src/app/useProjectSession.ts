/**
 * Project session helpers — localStorage keys, idle status và hook
 * useSessionRestore (F5 mở lại project đang làm). Upload/switch project
 * vẫn ở app/App.tsx.
 */
import { useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { api } from '@/features/project/project.api'
import { applyDefaultVoice, asSegmentList } from '@/features/project/useSegmentEditing'
import type { JobStatus, ProjectSettings, Segment } from '@/features/project/project.types'
import { SESSION_LS as SESSION_KEY, persistSession, persistSettings } from './appSettings'

/** F5 / Vite HMR: mở lại project đang làm (kể cả đang export) — chạy 1 lần khi mount. */
export function useSessionRestore(deps: {
  projectSwitchRef: MutableRefObject<number>
  activeProjectRef: MutableRefObject<string | null>
  setProjectId: (id: string | null) => void
  settings: ProjectSettings
  setSettings: Dispatch<SetStateAction<ProjectSettings>>
  setStatus: Dispatch<SetStateAction<JobStatus>>
  setSegments: Dispatch<SetStateAction<Segment[]>>
  // media
  workClipSecRef: MutableRefObject<number>
  setWorkClipSec: (sec: number) => void
  setDuration: (sec: number) => void
  setVideoUrl: (url: string | null) => void
  freshVideoUrl: (url: string) => string
  bakedPreferVideoRef: MutableRefObject<boolean>
  setBakedPreferVideo: (baked: boolean) => void
  setBakedSpeed: (speed: number) => void
  setHasBakedSpeed: (has: boolean) => void
  // export + job
  setExportUrl: (url: string | null) => void
  setExportPath: (path: string | null) => void
  releaseDubLock: () => void
  busyAt: MutableRefObject<number>
}) {
  const {
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
  } = deps

  useEffect(() => {
    const switchVersion = projectSwitchRef.current
    let id = ''
    try {
      id = localStorage.getItem(SESSION_KEY) || ''
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
        const wc =
          typeof st.workClipSec === 'number' ? Math.max(0, st.workClipSec) : 0
        workClipSecRef.current = wc
        setWorkClipSec(wc)
        // duration = cửa sổ hiển thị (status đã clamp preview/bake); không lấy full source
        const dur = Number(st.duration || 0)
        if (wc > 0) setDuration(wc)
        else if (dur > 0) setDuration(dur)
        const bs =
          typeof st.bakedSpeed === 'number' && st.bakedSpeed > 0 ? st.bakedSpeed : 1
        const userBake = Boolean((st as { hasBakedSpeed?: boolean }).hasBakedSpeed)
        const speedOff1 = Math.abs(bs - 1) > 0.02
        const baked = Boolean(st.bakedPreferVideo) && speedOff1
        bakedPreferVideoRef.current = baked
        setBakedPreferVideo(baked)
        setBakedSpeed(bs)
        // hasBakedSpeed true cả khi user Áp dụng 1×
        setHasBakedSpeed(userBake || speedOff1)
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
        // Không restore popup từ cancel/stale/code trần "dub"
        const rawErr = st.error && st.error !== 'cancelled' && st.error !== 'stale_job'
          ? String(st.error)
          : undefined
        const errMsg =
          rawErr === 'dub' || rawErr === 'export'
            ? (st.message && st.message.length > 3
                ? st.message
                : rawErr === 'dub'
                  ? 'Lồng tiếng thất bại — bấm Lồng tiếng để thử lại'
                  : 'Xuất thất bại')
            : rawErr
        setStatus({
          step: st.step || 'video',
          progress: st.progress || 0,
          message:
            st.message
            || (errMsg && !st.running ? errMsg : '')
            || 'Đã mở lại project',
          running: Boolean(st.running),
          error: errMsg,
          outputRel: st.outputRel,
          logoDetection: st.logoDetection,
        })
        if (!st.running) releaseDubLock()
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}

export {
  SETTINGS_LS,
  SESSION_LS,
  SIDEBAR_W_LS,
  THEME_LS,
  SETUP_GATE_LS,
  SIDEBAR_MIN,
  SIDEBAR_MAX,
  SIDEBAR_DEFAULT,
  loadSettings,
  persistSettings,
  persistSession,
  loadTheme,
  loadSidebarWidth,
  loadSetupGate,
  persistSetupGate,
  idleStatus,
  defaultSettings,
  applyEngineProfile,
  snapshotEngineProfile,
} from './appSettings'
