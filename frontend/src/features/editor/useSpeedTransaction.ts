/**
 * Transaction tốc độ video (bake preview) — tách từ LivePreviewEditor.
 * Chỉ revision mới nhất được commit; hủy txn cũ khi Áp dụng lại / Hủy.
 */
import { useEffect, useRef, useState } from 'react'
import type { ProjectSettings, Segment, TextOverlay } from '@/features/project/project.types'
import { api } from '@/features/project/project.api'
import {
  type MediaClip,
  displaySpeedDraft,
  fileBakedSpeed,
  formatSpeedX,
  mediaClipsFrom1xBaseline,
  mediaClipsTo1xBaseline,
} from '@/features/editor/lib'

type SpeedTransactionDeps = {
  projectId: string
  busy: boolean
  segments: Segment[]
  matchDuration: ProjectSettings['matchDuration']
  bakedSpeed: number
  bakedPreferVideo: boolean
  hasBakedSpeed: boolean
  wantNoVocals: boolean
  time: number
  setTime: (t: number) => void
  videoClips: MediaClip[]
  bgClips: MediaClip[]
  setVideoClips: React.Dispatch<React.SetStateAction<MediaClip[]>>
  setBgClips: React.Dispatch<React.SetStateAction<MediaClip[]>>
  videoRef: React.RefObject<HTMLVideoElement | null>
  bgAudioRef: React.RefObject<HTMLAudioElement | null>
  dubHardSyncRef: React.RefObject<boolean>
  dubFinishedIdsRef: React.RefObject<Set<string>>
  dubTokenRef: React.RefObject<string>
  pushHistory: () => void
  pauseDubAudio: () => void
  onSegmentsReplace: (segments: Segment[], opts?: { persist?: boolean }) => void | Promise<void>
  onPreviewRebaked?: (res: {
    segments: Segment[]
    overlays?: TextOverlay[]
    workClipSec: number
    duration: number
    bakedPreferVideo: boolean
    bakedSpeed: number
    videoUrl: string
    timeScale?: number
    prevBakedSpeed?: number
  }) => void
}

export function useSpeedTransaction(deps: SpeedTransactionDeps) {
  const {
    projectId,
    busy,
    segments,
    matchDuration,
    bakedSpeed,
    bakedPreferVideo,
    hasBakedSpeed,
    wantNoVocals,
    time,
    setTime,
    videoClips,
    bgClips,
    setVideoClips,
    setBgClips,
    videoRef,
    bgAudioRef,
    dubHardSyncRef,
    dubFinishedIdsRef,
    dubTokenRef,
    pushHistory,
    pauseDubAudio,
    onSegmentsReplace,
    onPreviewRebaked,
  } = deps

  // Slider = tốc độ file đã Áp dụng (1.00× nếu chưa bake)
  const [speedDraft, setSpeedDraft] = useState(() =>
    displaySpeedDraft(matchDuration, bakedSpeed, bakedPreferVideo, hasBakedSpeed),
  )
  const [speedBusy, setSpeedBusy] = useState(false)
  const [speedCancelling, setSpeedCancelling] = useState(false)
  const speedCancelRequestedRef = useRef(false)
  const [speedError, setSpeedError] = useState<string | null>(null)
  const [speedProgress, setSpeedProgress] = useState(0)
  const [speedMessage, setSpeedMessage] = useState('')
  /** Transaction tốc độ: chỉ revision mới nhất được commit */
  const speedTxnRef = useRef<{
    rev: number
    ac: AbortController | null
    debounce: ReturnType<typeof setTimeout> | null
    pendingRate: number | null
  }>({ rev: 0, ac: null, debounce: null, pendingRate: null })
  /** Baseline clip Video/BG ở 1× — mọi bake scale từ đây, không cascade */
  const mediaClips1xRef = useRef<{ video: MediaClip[]; bg: MediaClip[] } | null>(null)

  useEffect(() => {
    setSpeedDraft(
      displaySpeedDraft(matchDuration, bakedSpeed, bakedPreferVideo, hasBakedSpeed),
    )
    // Đổi project → baseline clip 1× phải chụp lại
    mediaClips1xRef.current = null
  }, [projectId, bakedSpeed, bakedPreferVideo, hasBakedSpeed, matchDuration])

  /** Áp dụng ngay (nút Áp dụng) — vẫn hủy txn cũ + tăng revision */
  function applyVideoSpeed(_scope: 'one' | 'all', speed?: number) {
    const raw = typeof speed === 'number' && Number.isFinite(speed) ? speed : speedDraft
    const v = Math.round(Math.max(0.5, Math.min(2, raw)) * 100) / 100
    setSpeedDraft(v)
    speedTxnRef.current.pendingRate = v
    if (speedTxnRef.current.debounce) {
      clearTimeout(speedTxnRef.current.debounce)
      speedTxnRef.current.debounce = null
    }
    void executeSpeedTransaction(v)
  }

  async function executeSpeedTransaction(v: number) {
    if (busy && !speedBusy) {
      setSpeedError('Đang có job khác — đợi xong rồi Áp dụng tốc độ.')
      return
    }
    const prevBaked = fileBakedSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed)
    // Đã khóa cùng số → no-op
    if (hasBakedSpeed && Math.abs(prevBaked - v) < 0.005) {
      return
    }

    // Hủy transaction / request cũ
    if (speedTxnRef.current.ac) {
      try { speedTxnRef.current.ac.abort() } catch { /* ignore */ }
    }
    speedCancelRequestedRef.current = true
    try { await api.cancel(projectId) } catch { /* ignore */ }
    speedCancelRequestedRef.current = false

    let rev = ++speedTxnRef.current.rev
    const ac = new AbortController()
    speedTxnRef.current.ac = ac

    setSpeedBusy(true)
    setSpeedCancelling(false)
    setSpeedProgress(3)
    setSpeedMessage(`Đang áp dụng ${formatSpeedX(v)}…`)
    setSpeedError(null)

    const prevT = videoRef.current?.currentTime ?? time
    const localById = new Map(segments.map((s) => [s.id, s] as const))
    const clipsSnap = {
      video: videoClips.map((c) => ({ ...c })),
      bg: bgClips.map((c) => ({ ...c })),
    }

    let pollId = 0
    const pollStatus = () => {
      if (rev !== speedTxnRef.current.rev || ac.signal.aborted) return
      void api.status(projectId).then((s) => {
        if (rev !== speedTxnRef.current.rev || ac.signal.aborted) return
        if (typeof s.progress === 'number' && s.progress > 0) {
          setSpeedProgress(Math.max(3, Math.min(99, s.progress)))
        }
        if (s.message) setSpeedMessage(s.message)
      }).catch(() => { /* ignore */ })
    }
    pollId = window.setInterval(pollStatus, 400)
    pollStatus()

    const isLatest = () => rev === speedTxnRef.current.rev && !ac.signal.aborted

    try {
      await onSegmentsReplace(segments, { persist: true })
      if (!isLatest()) return
      setSpeedProgress((p) => Math.max(p, 12))
      let res = await api.rebakeSpeed(projectId, v, {
        speedRevision: rev,
        signal: ac.signal,
      })
      if (!isLatest()) return
      if (
        res.ignored
        && res.reason === 'STALE_SPEED_REVISION'
        && typeof res.speedRevision === 'number'
        && res.speedRevision >= rev
      ) {
        // meta lưu revision từ phiên trước; FE mới mở đếm lại từ 1 → server
        // coi request là cũ. Đồng bộ theo server rồi thử lại đúng một lần.
        rev = res.speedRevision + 1
        speedTxnRef.current.rev = rev
        res = await api.rebakeSpeed(projectId, v, {
          speedRevision: rev,
          signal: ac.signal,
        })
        if (!isLatest()) return
      }
      if (res.ignored) {
        setSpeedMessage('Bỏ qua (đã có tốc độ mới hơn)')
        return
      }

      setSpeedProgress(92)
      setSpeedMessage('Đang cập nhật timeline…')
      const applied =
        typeof res.bakedSpeed === 'number' && res.bakedSpeed > 0
          ? Math.round(res.bakedSpeed * 100) / 100
          : v

      // Commit atomic — chỉ khi vẫn là revision mới nhất
      if (!isLatest()) return

      pushHistory()
      setSpeedDraft(applied)

      if (!mediaClips1xRef.current) {
        mediaClips1xRef.current = {
          video: mediaClipsTo1xBaseline(clipsSnap.video, prevBaked),
          bg: mediaClipsTo1xBaseline(clipsSnap.bg, prevBaked),
        }
      }
      const nextVideo = mediaClipsFrom1xBaseline(mediaClips1xRef.current.video, applied)
      const nextBg = mediaClipsFrom1xBaseline(mediaClips1xRef.current.bg, applied)
      setVideoClips(nextVideo)
      setBgClips(nextBg)

      const scale =
        typeof res.timeScale === 'number' && res.timeScale > 0
          ? res.timeScale
          : prevBaked / Math.max(0.5, applied)

      const mergedSegs = (Array.isArray(res.segments) ? res.segments : []).map((s, i) => {
        const loc = localById.get(s.id)
        if (!loc) return { ...s, index: i }
        return {
          ...loc,
          ...s,
          index: i,
          translation: (s.translation || '').trim() || loc.translation || s.translation,
          source: (s.source || '').trim() || loc.source || s.source,
          audioUrl: s.audioUrl || loc.audioUrl,
          audioFile: s.audioFile || loc.audioFile,
          audioDuration: s.audioDuration ?? loc.audioDuration,
          bbox: s.bbox ?? loc.bbox,
          captionLayout: s.captionLayout ?? loc.captionLayout,
          layout: s.layout ?? loc.layout,
          voice: s.voice || loc.voice,
          compoundChildren: s.compoundChildren?.length
            ? s.compoundChildren
            : loc.compoundChildren,
        }
      })
      const displayDur = Math.max(
        0,
        Number(res.duration) || Number(res.workClipSec) || 0,
      )
      if (!isLatest()) return

      const mergedRes = {
        ...res,
        segments: mergedSegs,
        duration: displayDur || res.duration,
        workClipSec: displayDur || res.workClipSec,
        bakedSpeed: applied,
        hasBakedSpeed: true as const,
      }
      onPreviewRebaked?.(mergedRes)
      if (!onPreviewRebaked) {
        void onSegmentsReplace(mergedSegs, { persist: true })
      }

      const nextT = Math.max(0, prevT * scale)
      const vid = videoRef.current
      if (vid) {
        try {
          vid.playbackRate = 1
          const dur = Number(vid.duration)
          const t = Number.isFinite(dur) && dur > 0 ? Math.min(nextT, Math.max(0, dur - 0.05)) : nextT
          vid.currentTime = t
          setTime(t)
        } catch {
          setTime(nextT)
        }
      } else {
        setTime(nextT)
      }
      dubHardSyncRef.current = true
      dubFinishedIdsRef.current.clear()
      dubTokenRef.current = ''
      pauseDubAudio()
      const bg = bgAudioRef.current
      if (bg && wantNoVocals) {
        try {
          bg.currentTime = nextT * applied
          bg.playbackRate = applied
        } catch { /* ignore */ }
      }
      setSpeedProgress(100)
      setSpeedMessage(`Đã áp dụng ${formatSpeedX(applied)}`)
    } catch (e) {
      if (!isLatest()) return
      const aborted =
        (e instanceof DOMException && e.name === 'AbortError')
        || speedCancelRequestedRef.current
        || ac.signal.aborted
      if (!aborted) {
        setSpeedError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      window.clearInterval(pollId)
      if (rev === speedTxnRef.current.rev) {
        setSpeedBusy(false)
        setSpeedCancelling(false)
        speedCancelRequestedRef.current = false
        window.setTimeout(() => {
          if (rev === speedTxnRef.current.rev) {
            setSpeedProgress(0)
            setSpeedMessage('')
          }
        }, 600)
      }
    }
  }

  async function cancelVideoSpeed() {
    if (!speedBusy || speedCancelling) return
    speedCancelRequestedRef.current = true
    setSpeedCancelling(true)
    setSpeedError(null)
    if (speedTxnRef.current.debounce) {
      clearTimeout(speedTxnRef.current.debounce)
      speedTxnRef.current.debounce = null
    }
    try { speedTxnRef.current.ac?.abort() } catch { /* ignore */ }
    speedTxnRef.current.rev += 1
    try {
      await api.cancel(projectId)
    } catch (e) {
      if (!speedCancelRequestedRef.current) {
        setSpeedError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setSpeedBusy(false)
      setSpeedCancelling(false)
      setSpeedProgress(0)
      setSpeedMessage('')
    }
  }

  return {
    speedDraft,
    setSpeedDraft,
    speedBusy,
    speedCancelling,
    speedError,
    setSpeedError,
    speedProgress,
    speedMessage,
    applyVideoSpeed,
    cancelVideoSpeed,
  }
}
