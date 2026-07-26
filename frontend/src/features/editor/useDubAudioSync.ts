/**
 * Engine đồng bộ audio preview (TTS + âm gốc / stem xóa lời) — tách từ
 * LivePreviewEditor. Refs sống ở component (còn dùng bởi effect/handler khác).
 */
import { useMemo } from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import {
  dubClipSeconds,
  dubPlaybackSpeed,
  expandSegmentsForPlayback,
  previewVideoRate,
  segmentForDub,
  segmentHasDub,
  speedSegmentAt,
} from '@/features/editor/lib'

type DubAudioSyncDeps = {
  segments: Segment[]
  settings: ProjectSettings
  bakedSpeed: number
  bakedPreferVideo: boolean
  hasBakedSpeed: boolean
  wantNoVocals: boolean
  muteOriginal: boolean
  stemStatus: 'off' | 'loading' | 'ready' | 'error'
  /** trackMute.dub — tắt track Lồng tiếng */
  dubMuted: boolean
  videoToTimelineTime: (value: number) => number
  videoRef: React.RefObject<HTMLVideoElement | null>
  bgAudioRef: React.RefObject<HTMLAudioElement | null>
  dubAudioRef: React.RefObject<HTMLAudioElement | null>
  dubTokenRef: React.RefObject<string>
  dubFinishedIdsRef: React.RefObject<Set<string>>
  dubHardSyncRef: React.RefObject<boolean>
  videoMutedForDubRef: React.RefObject<boolean>
}

export function useDubAudioSync(deps: DubAudioSyncDeps) {
  const {
    segments,
    settings,
    bakedSpeed,
    bakedPreferVideo,
    hasBakedSpeed,
    wantNoVocals,
    muteOriginal,
    stemStatus,
    dubMuted,
    videoToTimelineTime,
    videoRef,
    bgAudioRef,
    dubAudioRef,
    dubTokenRef,
    dubFinishedIdsRef,
    dubHardSyncRef,
    videoMutedForDubRef,
  } = deps

  /** TTS: luôn bung children (timing từng câu). Shell mix chỉ khi không bung được. */
  const dubPlaySegments = useMemo(() => {
    const expanded = expandSegmentsForPlayback(segments)
    const withDub = expanded.filter((s) => segmentHasDub(s) && s.audioUrl)
    if (withDub.length) return withDub
    // Fallback shell mix (không có TTS từng câu)
    return segments.filter((s) => s.isCompound && segmentHasDub(s) && s.audioUrl)
  }, [segments])

  function syncOriginalBg(
    videoTime: number,
    isPlaying: boolean,
    dubActive: boolean,
    playRate = 1,
    hardSync = false,
  ) {
    const video = videoRef.current
    if (!video) return
    const volMul = Math.max(0, Math.min(2, (settings.originalAudioVolume ?? 100) / 100))
    const bg = bgAudioRef.current
    const playStem = wantNoVocals && stemStatus === 'ready' && !!bg
    // Âm gốc chỉ điều khiển qua track «Âm gốc» (không duplicate mute trên Video)
    const playVideoAudio = !muteOriginal
    const rate = Math.max(0.5, Math.min(2, playRate))
    // Stem file luôn 1× nguồn; timeline display sau bake → map sourceTime = t * bakedSpeed
    const bakeSp =
      typeof bakedSpeed === 'number' && bakedSpeed > 0.2
        ? bakedSpeed
        : bakedPreferVideo
          ? 0.8
          : 1

    if (playStem && bg) {
      video.muted = true
      video.volume = 1
      videoMutedForDubRef.current = true
      bg.volume = Math.min(1, volMul * (dubActive ? 0.62 : 1))
      // Cùng wall-clock với video bake: rate_stem = rate_video * bakeSp
      const stemRate = Math.max(0.5, Math.min(2, rate * bakeSp))
      if (Math.abs(bg.playbackRate - stemRate) > 0.01) bg.playbackRate = stemRate
      if (hardSync) {
        try {
          bg.currentTime = Math.max(0, videoTime * bakeSp)
        } catch { /* ignore */ }
      }
      if (isPlaying) {
        if (bg.paused) void bg.play().catch(() => { /* autoplay */ })
      } else {
        bg.pause()
      }
      return
    }

    bg?.pause()
    if (!playVideoAudio) {
      video.muted = true
      videoMutedForDubRef.current = true
      return
    }
    video.muted = false
    videoMutedForDubRef.current = false
    video.volume = Math.min(1, Math.max(0, volMul * (dubActive ? 0.14 : 0.42)))
  }

  function pauseDubAudio() {
    // Giữ token — pause/play không load lại file (tránh ngắt đầu câu)
    dubAudioRef.current?.pause()
    bgAudioRef.current?.pause()
    const video = videoRef.current
    const t = video?.currentTime ?? 0
    const at = speedSegmentAt(segments, videoToTimelineTime(t))
    const playRate = previewVideoRate(
      settings.matchDuration,
      bakedPreferVideo,
      at?.videoSpeed,
      bakedSpeed,
      hasBakedSpeed,
    )
    syncOriginalBg(t, false, Boolean(dubTokenRef.current), playRate, false)
  }

  /** Đồng bộ clip TTS (+ nền). Free-run 1 lần / câu; không restart khi ended. */
  function syncDubAudio(videoTime: number, isPlaying: boolean) {
    const video = videoRef.current
    if (!video || !isPlaying) {
      pauseDubAudio()
      return
    }

    // Tua ngược / ra khỏi cửa sổ → cho phép đọc lại
    const finished = dubFinishedIdsRef.current
    const dubSegs = dubPlaySegments
    for (const s of dubSegs) {
      if (!finished.has(s.id)) continue
      if (videoTime < s.start - 0.15) finished.delete(s.id)
    }

    const hardSync = dubHardSyncRef.current
    dubHardSyncRef.current = false

    let a = dubAudioRef.current
    if (!a) {
      a = new Audio()
      a.preload = 'auto'
      a.loop = false
      dubAudioRef.current = a
    }

    // Đang phát dở → giữ nguyên câu (không nhảy / không lặp)
    const holdId = dubTokenRef.current.split('|')[0]
    const held = holdId ? dubSegs.find((s) => s.id === holdId) : undefined
    const heldClipEnded = held
      ? videoTime >= held.start + dubClipSeconds(
          held,
          dubSegs,
          previewVideoRate(settings.matchDuration, bakedPreferVideo, held.videoSpeed, bakedSpeed, hasBakedSpeed),
          bakedSpeed,
        ) - 0.01
      : false
    if (held?.audioUrl && !heldClipEnded && !a.ended && a.currentTime > 0.02 && videoTime >= held.start - 0.08) {
      const playRate = previewVideoRate(
        settings.matchDuration,
        bakedPreferVideo,
        held.videoSpeed,
        bakedSpeed,
        hasBakedSpeed,
      )
      if (Math.abs(video.playbackRate - playRate) > 0.01) video.playbackRate = playRate
      const speed = dubPlaybackSpeed(held, bakedSpeed)
      a.playbackRate = speed
      a.volume = Math.min(1, Math.max(0, (held.ttsVolume ?? 100) / 100))
      if (hardSync) {
        // TTS wav 1×: offset = wall * bake; wall ≈ (videoTime-start)/playRate
        const wantTime = Math.max(0, ((videoTime - held.start) / Math.max(0.2, playRate)) * speed)
        try {
          if (Math.abs(a.currentTime - wantTime) > 0.2) a.currentTime = wantTime
        } catch { /* ignore */ }
      }
      if (a.paused) void a.play().catch(() => { /* autoplay */ })
      syncOriginalBg(videoTime, true, true, playRate, hardSync)
      return
    }

    // Vừa xong câu → đánh dấu, không play lại
    if (held && (a.ended || heldClipEnded)) {
      if (heldClipEnded) a.pause()
      finished.add(held.id)
      dubTokenRef.current = ''
    }

    const at = speedSegmentAt(segments, videoTime)
    const playRateProbe = previewVideoRate(
      settings.matchDuration,
      bakedPreferVideo,
      at?.videoSpeed,
      bakedSpeed,
      hasBakedSpeed,
    )
    const seg = dubMuted
      ? null
      : segmentForDub(dubSegs, videoTime, playRateProbe, finished, bakedSpeed)

    if (!seg?.audioUrl) {
      if (dubTokenRef.current) {
        a.pause()
        dubTokenRef.current = ''
      }
      const idleRate = previewVideoRate(
        settings.matchDuration,
        bakedPreferVideo,
        at?.videoSpeed,
        bakedSpeed,
        hasBakedSpeed,
      )
      if (Math.abs(video.playbackRate - idleRate) > 0.01) video.playbackRate = idleRate
      syncOriginalBg(videoTime, true, false, idleRate, hardSync)
      return
    }

    const playRate = previewVideoRate(
      settings.matchDuration,
      bakedPreferVideo,
      seg.videoSpeed,
      bakedSpeed,
      hasBakedSpeed,
    )
    if (Math.abs(video.playbackRate - playRate) > 0.01) video.playbackRate = playRate

    const speed = dubPlaybackSpeed(seg, bakedSpeed)
    const vol = Math.min(1, Math.max(0, (seg.ttsVolume ?? 100) / 100))
    const wantTime = Math.max(0, ((videoTime - seg.start) / Math.max(0.2, playRate)) * speed)
    const token = `${seg.id}|${seg.audioUrl}`

    syncOriginalBg(videoTime, true, true, playRate, hardSync)

    // Cùng token + chưa ended → chỉ resume, không gán src lại (tránh lặp đầu câu)
    if (dubTokenRef.current === token && !a.ended) {
      a.playbackRate = speed
      a.volume = vol
      if (hardSync) {
        try {
          if (Math.abs(a.currentTime - wantTime) > 0.2) a.currentTime = wantTime
        } catch { /* ignore */ }
      }
      if (a.paused) void a.play().catch(() => { /* autoplay */ })
      return
    }

    // Đã finished id này → bỏ
    if (finished.has(seg.id) && !hardSync) {
      syncOriginalBg(videoTime, true, false, playRate, hardSync)
      return
    }

    // Đổi câu mới — play 1 lần từ đầu (hoặc scrub offset)
    if (hardSync) finished.delete(seg.id)
    dubTokenRef.current = token
    a.pause()
    a.loop = false
    a.src = seg.audioUrl
    a.playbackRate = speed
    a.volume = vol
    const startAt = () => {
      try {
        a.currentTime = hardSync ? wantTime : 0
      } catch { /* ignore */ }
      void a.play().catch(() => { /* autoplay */ })
    }
    if (a.readyState >= 1) startAt()
    else a.addEventListener('loadedmetadata', startAt, { once: true })
  }

  return { syncOriginalBg, pauseDubAudio, syncDubAudio }
}
