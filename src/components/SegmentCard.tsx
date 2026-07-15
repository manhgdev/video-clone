import { useRef, useState } from 'react'
import type { ProjectSettings, Segment } from '../types'
import { api } from '../services/api'
import { IconPlay, IconRefresh } from './Icons'
import './SegmentCard.css'

type Props = {
  segment: Segment
  voices: { id: string; name: string }[]
  defaultVoice: string
  targetLang: string
  sourceLang?: string
  translator?: ProjectSettings['translator']
  videoUrl: string | null
  projectId: string | null
  onChange: (seg: Segment) => void
}

function fmt(t: number, precise = false) {
  const m = Math.floor(t / 60)
  const sec = t % 60
  if (precise) {
    // sub-second title dọc: 0:00.0 – 0:00.1
    return `${m}:${sec.toFixed(1).padStart(4, '0')}`
  }
  const s = Math.floor(sec)
  return `${m}:${String(s).padStart(2, '0')}`
}

/** ponytail: one shared player so next ▶ stops the previous */
let activeMedia: HTMLMediaElement | null = null

function stopActive() {
  if (!activeMedia) return
  activeMedia.pause()
  activeMedia.removeAttribute('src')
  activeMedia.load()
  activeMedia = null
}

export default function SegmentCard({
  segment,
  voices,
  defaultVoice,
  targetLang,
  sourceLang = 'auto',
  translator = 'google',
  videoUrl,
  projectId,
  onChange,
}: Props) {
  const voice = !segment.voice || segment.voice === 'system' ? defaultVoice : segment.voice
  const dur = segment.audioDuration ?? Math.max(0.1, segment.end - segment.start)
  const chars = segment.translation.length || segment.source.length
  const layout = segment.layout || 'horizontal'
  const isOverlay = layout === 'vertical' || layout === 'label'
  const layoutBadge =
    layout === 'vertical' ? 'Dọc' : layout === 'mid' ? 'Mid' : layout === 'label' ? 'Nhãn' : 'Caption'
  const layoutTitle =
    layout === 'vertical'
      ? 'Tiêu đề dọc'
      : layout === 'mid'
        ? 'Chữ giữa khung'
        : layout === 'label'
          ? 'Nhãn trên khung'
          : 'Phụ đề đáy (caption)'
  // vertical/label: mặc định tắt lồng tiếng; hardsub/mid: mặc định bật
  const dubOn = isOverlay ? segment.dub === true : segment.dub !== false
  const [busy, setBusy] = useState(false)
  const [reBusy, setReBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const stopTimer = useRef<number | null>(null)

  function clearStopTimer() {
    if (stopTimer.current != null) {
      window.clearInterval(stopTimer.current)
      stopTimer.current = null
    }
  }

  async function playVideoClip() {
    if (!videoUrl) {
      setErr('Chưa có video')
      return
    }
    const v = document.createElement('video')
    v.src = videoUrl
    v.preload = 'auto'
    activeMedia = v
    await new Promise<void>((resolve, reject) => {
      v.onloadedmetadata = () => resolve()
      v.onerror = () => reject(new Error('Không tải được video'))
    })
    v.currentTime = segment.start
    await v.play()
    stopTimer.current = window.setInterval(() => {
      if (v.currentTime >= segment.end - 0.05) {
        v.pause()
        clearStopTimer()
      }
    }, 50)
  }

  async function play() {
    setErr(null)
    stopActive()
    clearStopTimer()

    try {
      if (projectId && segment.translation.trim()) {
        setBusy(true)
        const res = await api.previewTts(projectId, segment.id, {
          text: segment.translation,
          voice,
          lang: targetLang && targetLang !== 'none' ? targetLang : 'vi',
        })
        const updated = { ...segment, audioUrl: res.audioUrl, audioDuration: res.duration }
        onChange(updated)
        const a = new Audio(res.audioUrl)
        activeMedia = a
        await a.play()
        return
      }

      await playVideoClip()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Nghe thử thất bại')
    } finally {
      setBusy(false)
    }
  }

  async function retranslate() {
    if (!projectId || !segment.source.trim() || targetLang === 'none') return
    setErr(null)
    setReBusy(true)
    try {
      const res = await api.retranslate(projectId, segment.id, {
        text: segment.source,
        sourceLang,
        targetLang,
        translator,
      })
      onChange({
        ...segment,
        translation: res.translation,
        audioUrl: undefined,
        audioFile: undefined,
        audioDuration: undefined,
      })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Dịch lại thất bại')
    } finally {
      setReBusy(false)
    }
  }

  return (
    <article className="seg">
      <div className="seg-body">
        <div className="seg-rail">
          <div className="idx-row">
            <span className="idx">{String(segment.index).padStart(2, '0')}</span>
            <span
              className={`seg-badge seg-badge--${layout}`}
              title={layoutTitle}
            >
              {layoutBadge}
            </span>
          </div>
          <span className="time">
            {(() => {
              const short = segment.end - segment.start < 1.0
              return (
                <span className="time-line" title="Đầu – cuối clip (kéo trên timeline nếu sai)">
                  {fmt(segment.start, short)} – {fmt(segment.end, short)}
                </span>
              )
            })()}
          </span>
          <div className="seg-actions">
            <button
              type="button"
              className="play"
              onClick={play}
              disabled={busy || reBusy || (isOverlay && !dubOn)}
              aria-label="Nghe đoạn"
              title={isOverlay && !dubOn ? 'Bật lồng tiếng để nghe TTS' : 'Nghe TTS bản dịch'}
            >
              {busy ? '…' : <IconPlay size={12} />}
            </button>
            <button
              type="button"
              className="retranslate"
              onClick={retranslate}
              disabled={reBusy || busy || !projectId || !segment.source.trim() || targetLang === 'none'}
              aria-label="Dịch lại"
              title="Tạo lại bản dịch đoạn này"
            >
              {reBusy ? '…' : <IconRefresh size={12} />}
            </button>
          </div>
          {err && <span className="play-err">{err}</span>}
        </div>

        <label className="cell">
          <span>Ngôn ngữ gốc</span>
          <textarea
            value={segment.source}
            rows={2}
            onChange={(e) => onChange({ ...segment, source: e.target.value })}
          />
        </label>

        <label className="cell">
          <span>Bản dịch</span>
          <textarea
            value={segment.translation}
            rows={2}
            onChange={(e) => onChange({ ...segment, translation: e.target.value })}
          />
        </label>

        {isOverlay ? (
          <div className="cell voice-cell voice-toggle-cell">
            <span className="voice-head">
              Giọng đọc
              <label className="dub-check" title="Bật lồng tiếng cho tiêu đề/nhãn">
                <input
                  type="checkbox"
                  checked={dubOn}
                  onChange={(e) =>
                    onChange({
                      ...segment,
                      dub: e.target.checked,
                      ...(e.target.checked
                        ? {}
                        : { audioUrl: undefined, audioFile: undefined, audioDuration: undefined }),
                    })
                  }
                />
                Lồng tiếng
              </label>
            </span>
            {dubOn ? (
              <select
                value={voices.some((v) => v.id === voice) ? voice : defaultVoice}
                onChange={(e) => onChange({ ...segment, dub: true, voice: e.target.value })}
              >
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            ) : (
              <div className="voice-off" aria-hidden>
                Chỉ burn chữ
              </div>
            )}
            <div className="voice-meta">
              <em>{chars} ký tự</em>
              <em>{dur < 1 ? `${dur.toFixed(2)}s` : `${dur.toFixed(1)}s`}</em>
            </div>
          </div>
        ) : (
          <label className="cell voice-cell">
            <span>Giọng đọc</span>
            <select
              value={voices.some((v) => v.id === voice) ? voice : defaultVoice}
              onChange={(e) => onChange({ ...segment, voice: e.target.value })}
            >
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
            <div className="voice-meta">
              <em>{chars} ký tự</em>
              <em>{dur.toFixed(1)}s</em>
            </div>
          </label>
        )}
      </div>

      <div
        className="bar"
        style={{
          ['--p' as string]: `${Math.min(100, (dur / Math.max(0.1, segment.end - segment.start)) * 50)}%`,
        }}
      />
    </article>
  )
}
