import { useEffect, useRef, useState } from 'react'
import { fetchJson } from '@/shared/api/fetchJson'
import { SRT_STYLE_OPTIONS } from '@/features/tts/lib/srt'
import { IconDownload } from '@/shared/components/Icons'
import './SrtExportPage.css'

type SourceKind = 'media' | 'caption' | 'manual' | 'url'
type Job = { id: string; filename: string; sourceKind: SourceKind; status: 'queued' | 'processing' | 'done' | 'error' | 'cancelled'; progress: number; message: string; error?: string; files: string[] }
const CACHE_KEY = 'videoclone.srt-export.source-kind'

function loadKind(): SourceKind {
  try { const value = localStorage.getItem(CACHE_KEY); return value === 'caption' || value === 'manual' || value === 'url' ? value : 'media' } catch { return 'media' }
}

export default function SrtExportPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<SourceKind>(loadKind)
  const [file, setFile] = useState<File | null>(null)
  const [manualText, setManualText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { try { localStorage.setItem(CACHE_KEY, kind) } catch {} }, [kind])
  useEffect(() => {
    if (!job || !['queued', 'processing'].includes(job.status)) return
    const timer = window.setInterval(() => fetchJson<Job>(`/api/srt-export/jobs/${job.id}`).then(setJob).catch(() => {}), 900)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status])

  async function submit() {
    if ((!file && kind !== 'manual' && kind !== 'url') || (kind === 'manual' && !manualText.trim()) || (kind === 'url' && !sourceUrl.trim()) || busy) return
    setBusy(true); setError('')
    try {
      const form = new FormData()
      if (file) form.append('file', file)
      form.append('source_kind', kind)
      form.append('manual_text', manualText)
      form.append('source_url', sourceUrl)
      setJob(await fetchJson<Job>('/api/srt-export/jobs', { method: 'POST', body: form }, 60_000))
    } catch (e) { setError(e instanceof Error ? e.message : 'Không thể tạo phụ đề') }
    finally { setBusy(false) }
  }

  async function cancel() {
    if (!job) return
    await fetchJson(`/api/srt-export/jobs/${job.id}/cancel`, { method: 'POST' })
    setJob({ ...job, status: 'cancelled', message: 'Đã hủy' })
  }

  const accepted = kind === 'media'
    ? '.mp3,.wav,.m4a,.aac,.flac,.ogg,.mp4,.mov,.mkv,.webm,.avi,.m4v'
    : '.srt,.vtt,.txt'

  return <main className="srt-export-page">
    <header className="srt-export-head">
      <h1>Xuất Phụ Đề</h1>
      <p>Tạo phụ đề từ audio/video hoặc định dạng lại caption có sẵn để dùng trong CapCut, YouTube và các trình dựng video.</p>
    </header>
    <section className="srt-export-card">
      <div className="srt-export-tabs" role="tablist" aria-label="Nguồn phụ đề">
        <button className={kind === 'media' ? 'active' : undefined} onClick={() => { setKind('media'); setFile(null) }}>Từ audio / video</button>
        <button className={kind === 'caption' ? 'active' : undefined} onClick={() => { setKind('caption'); setFile(null) }}>Từ SRT / caption / file</button>
        <button className={kind === 'manual' ? 'active' : undefined} onClick={() => { setKind('manual'); setFile(null) }}>Nhập bằng tay</button>
        <button className={kind === 'url' ? 'active' : undefined} onClick={() => { setKind('url'); setFile(null) }}>Từ URL</button>
      </div>
      <div className="srt-export-body">
        <h2>{kind === 'media' ? 'Chọn audio hoặc video' : kind === 'caption' ? 'Chọn file phụ đề có sẵn' : kind === 'manual' ? 'Nhập nội dung caption' : 'Dán URL audio, video hoặc caption'}</h2>
        <p>{kind === 'media' ? 'Whisper tự nhận dạng lời nói và giữ mốc thời gian.' : kind === 'manual' ? 'Mỗi dòng là một caption. Với nội dung có timecode, hãy dùng định dạng SRT.' : kind === 'url' ? 'URL phải truy cập được trực tiếp và có đuôi media hoặc .srt/.vtt.' : 'Hỗ trợ SRT, VTT và TXT. SRT/VTT giữ timecode; TXT chia mỗi dòng thành một caption ngắn.'}</p>
        {kind === 'manual' ? <textarea className="srt-export-textarea" value={manualText} onChange={(event) => setManualText(event.target.value)} placeholder="Nhập từng câu phụ đề, mỗi dòng một caption…" rows={7} /> : kind === 'url' ? <input className="srt-export-url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://example.com/audio.mp3" /> : <><input ref={inputRef} type="file" accept={accepted} hidden onChange={(event) => setFile(event.target.files?.[0] || null)} /><button className="srt-export-picker" type="button" onClick={() => inputRef.current?.click()}>{file ? file.name : 'Chọn file'}</button>{file && <span className="srt-export-file">{(file.size / 1024 / 1024).toFixed(file.size > 10 * 1024 * 1024 ? 0 : 1)} MB</span>}</>}
      </div>
      <div className="srt-export-outputs">
        <strong>File xuất tự động</strong>
        <div className="srt-export-chips">
          {SRT_STYLE_OPTIONS.map((style) => <span key={style.id}>{style.label}</span>)}
          <span>WebVTT</span><span>TXT</span><span>ZIP (tất cả)</span>
        </div>
      </div>
      {error && <p className="srt-export-error">{error}</p>}
      <footer className="srt-export-actions">
        <button className="srt-export-run" disabled={(!file && kind !== 'manual' && kind !== 'url') || (kind === 'manual' && !manualText.trim()) || (kind === 'url' && !sourceUrl.trim()) || busy || job?.status === 'processing'} onClick={submit}>{busy ? 'Đang gửi nguồn…' : kind === 'media' ? 'Tạo phụ đề' : 'Xuất phụ đề'}</button>
        {job && ['queued', 'processing'].includes(job.status) && <button className="srt-export-cancel" onClick={cancel}>Hủy</button>}
      </footer>
    </section>
    {job && <section className="srt-export-card srt-export-result">
      <div className="srt-export-result-head"><strong>{job.filename}</strong><span className={`srt-export-status ${job.status}`}>{job.message}</span></div>
      <div className="srt-export-progress"><i style={{ width: `${job.progress}%` }} /></div>
      {job.error && <p className="srt-export-error">{job.error}</p>}
      {job.status === 'done' && <div className="srt-export-downloads">
        {job.files.map((name) => <a key={name} className={name.endsWith('.zip') ? 'primary' : undefined} href={`/api/srt-export/jobs/${job.id}/files/${encodeURIComponent(name)}`} download><IconDownload size={15} /><span>{name}</span><small>Tải về</small></a>)}
      </div>}
    </section>}
  </main>
}
