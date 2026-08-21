/**
 * State media của project đang mở: video URL (cache-bust), cửa sổ hiển thị
 * (workClipSec/duration) và trạng thái bake tốc độ — kèm 2 luồng
 * onPreviewRebaked / onRestoreBakedSpeed từ LivePreviewEditor.
 */
import { useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { api } from './project.api'
import { applyDefaultVoice, asSegmentList } from './useSegmentEditing'
import type { Segment, TextOverlay } from './project.types'

export function useProjectMedia({
  projectId,
  defaultVoice,
  setSegments,
  setOverlays,
}: {
  projectId: string | null
  defaultVoice: string
  setSegments: Dispatch<SetStateAction<Segment[]>>
  setOverlays: Dispatch<SetStateAction<TextOverlay[]>>
}) {
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [duration, setDuration] = useState(0)
  /** Độ dài clip làm việc = lần dịch gần nhất (0 = full). Khác settings.previewSec (ô Preview). */
  const [workClipSec, setWorkClipSec] = useState(0)
  const workClipSecRef = useRef(0)
  const [bakedPreferVideo, setBakedPreferVideo] = useState(false)
  const bakedPreferVideoRef = useRef(false)
  const [bakedSpeed, setBakedSpeed] = useState(1)
  const [hasBakedSpeed, setHasBakedSpeed] = useState(false)
  const videoRevisionRef = useRef(0)

  const freshVideoUrl = (url: string) => {
    const rev = Date.now()
    videoRevisionRef.current = rev
    const base = url.split('?')[0]
    if (base.includes('/video')) {
      const projBase = base.split('/video')[0]
      return `${projBase}/video/${rev}`
    }
    return `${base}?v=${rev}`
  }

  function onPreviewRebaked(res: {
    segments: Segment[]
    overlays?: TextOverlay[]
    workClipSec: number
    duration: number
    bakedPreferVideo: boolean
    bakedSpeed: number
    videoUrl: string
    timeScale?: number
    prevBakedSpeed?: number
    hasBakedSpeed?: boolean
  }) {
    // Segments/overlays đã remap — giữ text nếu list server thiếu translation
    setSegments((prev) => {
      const incoming = applyDefaultVoice(asSegmentList(res.segments), defaultVoice)
      if (!prev.length) return incoming
      const byId = new Map(prev.map((s) => [s.id, s] as const))
      return incoming.map((s) => {
        const loc = byId.get(s.id)
        if (!loc) return s
        // Giữ start/end từ server (đã scale); chỉ heal text/media rỗng
        return {
          ...s,
          translation: (s.translation || '').trim() || loc.translation || s.translation,
          source: (s.source || '').trim() || loc.source || s.source,
          audioUrl: s.audioUrl || loc.audioUrl,
          audioFile: s.audioFile || loc.audioFile,
          audioDuration: s.audioDuration ?? loc.audioDuration,
          bbox: s.bbox ?? loc.bbox,
          captionLayout: s.captionLayout ?? loc.captionLayout,
          layout: s.layout ?? loc.layout,
          voice: s.voice || loc.voice,
        }
      })
    })
    if (Array.isArray(res.overlays)) setOverlays(res.overlays)
    // Cửa sổ display sau bake — thước = xuất
    const wc = Math.max(0, Number(res.workClipSec) || Number(res.duration) || 0)
    workClipSecRef.current = wc
    setWorkClipSec(wc)
    if (wc > 0) setDuration(wc)
    else if (res.duration > 0) setDuration(res.duration)
    const bs = res.bakedSpeed > 0 ? res.bakedSpeed : res.bakedPreferVideo ? 0.8 : 1
    bakedPreferVideoRef.current = Boolean(res.bakedPreferVideo) && Math.abs(bs - 1) > 0.02
    setBakedPreferVideo(bakedPreferVideoRef.current)
    setBakedSpeed(bs)
    setHasBakedSpeed(true)
    setVideoUrl(freshVideoUrl(res.videoUrl))
  }

  /** Undo bake: chỉ đổi workVideo — segments giữ từ history snapshot */
  async function onRestoreBakedSpeed(speed: number, segs?: Segment[]) {
    if (!projectId) return
    // Persist snapshot TRƯỚC khi rebake (tuần tự — tránh race PUT/POST ghi đè
    // nhau): server segments + baseline pop phải theo lineage undo.
    if (segs?.length) {
      const ordered = [...segs]
        .sort((a, b) => a.start - b.start || a.end - b.end)
        .map((s, i) => ({ ...s, index: i }))
      try {
        await api.replaceSegments(projectId, ordered)
      } catch {
        // giữ undo local; export vẫn gửi segments từ editor
      }
    }
    const res = await api.rebakeSpeed(projectId, speed, { skipRemap: true })
    const wc = Math.max(0, res.workClipSec)
    workClipSecRef.current = wc
    setWorkClipSec(wc)
    if (res.duration > 0) setDuration(res.duration)
    const bs = res.bakedSpeed > 0 ? res.bakedSpeed : res.bakedPreferVideo ? 0.8 : 1
    bakedPreferVideoRef.current = Boolean(res.bakedPreferVideo) && Math.abs(bs - 1) > 0.02
    setBakedPreferVideo(bakedPreferVideoRef.current)
    setBakedSpeed(bs)
    setHasBakedSpeed(true)
    setVideoUrl(freshVideoUrl(res.videoUrl || `/api/projects/${projectId}/video`))
  }

  return {
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
  }
}
