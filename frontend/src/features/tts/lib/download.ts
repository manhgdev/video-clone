/** URL tải file job TTS + helper kích hoạt download qua thẻ <a> ẩn. */
import type { HistoryItem } from '../tts.types'
import type { SrtStyle } from './srt'

export function downloadWavHref(url: string | null) {
  if (!url) return undefined
  const u = url.replace(/([?&])t=\d+/, '').replace(/\?$/, '')
  return `${u}${u.includes('?') ? '&' : '?'}download=1`
}

/** URL tải theo loại file cho 1 job lịch sử */
export function historyDownloadUrl(
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

/** Tải file bằng thẻ <a download> tạm — không đổi state UI (caller tự đóng menu). */
export function triggerDownload(url: string | undefined, filename: string) {
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}
