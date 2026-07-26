/**
 * Poll /status 1.5s khi job đang chạy: cập nhật status, cửa sổ media/bake,
 * đồng bộ segments khi job xong và chốt kết quả export.
 */
import { useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { api } from './project.api'
import { applyDefaultVoice, asSegmentList } from './useSegmentEditing'
import type { JobStatus, Segment } from './project.types'

export function useJobPolling(deps: {
  projectId: string | null
  running: boolean
  activeProjectRef: MutableRefObject<string | null>
  setStatus: Dispatch<SetStateAction<JobStatus>>
  releaseDubLock: () => void
  defaultVoice: string
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
  // export
  pendingExportUrl: MutableRefObject<string | null>
  applyExportDone: (s: JobStatus, pid: string) => void
}) {
  const pollRef = useRef<number | null>(null)
  const pollInFlight = useRef(false)
  const pollFailStreak = useRef(0)
  const {
    projectId,
    running,
    activeProjectRef,
    setStatus,
    releaseDubLock,
    defaultVoice,
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
  } = deps

  useEffect(() => {
    if (!projectId || !running) {
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
            if (wc > 0) setDuration(wc)
            // Clip preview/full đổi kích thước — phải đổi URL kẻo Range cũ 416
            setVideoUrl(freshVideoUrl(`/api/projects/${projectId}/video`))
          }
        }
        const pollDur = Number(s.duration || 0)
        if (pollDur > 0 && !(typeof s.workClipSec === 'number' && s.workClipSec > 0)) {
          setDuration(pollDur)
        } else if (pollDur > 0 && typeof s.workClipSec === 'number' && s.workClipSec > 0) {
          setDuration(Math.min(pollDur, s.workClipSec) || s.workClipSec)
        }
        const bs =
          typeof s.bakedSpeed === 'number' && s.bakedSpeed > 0 ? s.bakedSpeed : 1
        const userBake = Boolean((s as { hasBakedSpeed?: boolean }).hasBakedSpeed)
        const speedOff1 = Math.abs(bs - 1) > 0.02
        const baked = Boolean(s.bakedPreferVideo) && speedOff1
        if (baked !== bakedPreferVideoRef.current) {
          bakedPreferVideoRef.current = baked
          setBakedPreferVideo(baked)
          setVideoUrl(freshVideoUrl(`/api/projects/${projectId}/video`))
        }
        setBakedSpeed(bs)
        setHasBakedSpeed(userBake || speedOff1)
        if (!s.running) {
          releaseDubLock()
          try {
            const segs = await api.segments(projectId)
            if (activeProjectRef.current !== projectId) return
            // Cache-bust ổn định theo audioDuration (không Date.now mỗi poll → storm Range)
            const list = applyDefaultVoice(asSegmentList(segs), defaultVoice).map((seg) => {
              if (!seg.audioUrl || !seg.audioFile) return seg
              const base = seg.audioUrl.split('?')[0]
              const v = Math.round((seg.audioDuration || 0) * 1000)
              return { ...seg, audioUrl: `${base}?v=${v}` }
            })
            // Không xóa list đang hiện nếu server trả rỗng (race / meta lock)
            if (list.length > 0) setSegments(list)
          } catch {
            /* status đã xong — giữ segments local */
          }
          if (exportDone) {
            applyExportDone(s, projectId)
          }
        }
      } catch {
        pollFailStreak.current += 1
        // ~7.5s (5×1.5s) backend down
        if (pollFailStreak.current >= 5) {
          releaseDubLock()
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, running])
}
