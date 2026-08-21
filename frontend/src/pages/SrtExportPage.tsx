import { useEffect, useRef, useState } from 'react'
import { fetchJson } from '@/shared/api/fetchJson'
import { SRT_STYLE_OPTIONS } from '@/features/tts/lib/srt'
import { IconDownload } from '@/shared/components/Icons'
import { loadSettings } from '@/app/appSettings'
import { localize, useLocale } from '@/app/i18n'
import { BackTitle } from '@/shared/components/BackTitle'
import './SrtExportPage.css'

type SourceKind = 'media' | 'caption' | 'manual' | 'url'
type OutputMode = 'original' | 'translated' | 'bilingual'
type Job = { id: string; filename: string; sourceKind: SourceKind | 'platform'; status: 'queued' | 'processing' | 'done' | 'error' | 'cancelled'; progress: number; message: string; error?: string; files: string[]; options?: { outputMode?: OutputMode; targetLang?: string } }
const CACHE_KEY = 'videoclone.srt-export.source-kind'
const LANGUAGE_LABELS: Record<string, string> = { vi: 'Tiếng Việt', en: 'Tiếng Anh', zh: 'Tiếng Trung', ja: 'Tiếng Nhật', ko: 'Tiếng Hàn' }

function loadKind(): SourceKind {
  try { const value = localStorage.getItem(CACHE_KEY); return value === 'caption' || value === 'manual' || value === 'url' ? value : 'media' } catch { return 'media' }
}

export default function SrtExportPage({ onBack }: { onBack: () => void }) {
  const { locale } = useLocale()
  const t = (vi: string, en: string) => localize(locale, vi, en)
  const inputRef = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<SourceKind>(loadKind)
  const [file, setFile] = useState<File | null>(null)
  const [manualText, setManualText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const saved = useRef(loadSettings()).current
  const [outputMode, setOutputMode] = useState<OutputMode>('original')
  const [sourceLang, setSourceLang] = useState(saved.sourceLang)
  const [targetLang, setTargetLang] = useState(saved.targetLang === 'none' ? 'vi' : saved.targetLang)
  const [translator, setTranslator] = useState(saved.translator)

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
      form.append('output_mode', outputMode)
      form.append('source_lang', sourceLang)
      form.append('target_lang', targetLang)
      form.append('translator', translator)
      form.append('workers', String(saved.workers))
      form.append('ollama_mode', saved.ollamaMode)
      form.append('ollama_model', saved.ollamaModel)
      form.append('ollama_local_tier', saved.ollamaLocalTier)
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
  const sourceFiles = job?.files.filter((name) => name.startsWith('subtitles-source-')) || []
  const zipFiles = job?.files.filter((name) => name.endsWith('.zip')) || []
  const translatedFiles = job?.files.filter((name) => !name.startsWith('subtitles-source-') && !name.endsWith('.zip')) || []
  const isBilingualResult = job?.options?.outputMode === 'bilingual' || sourceFiles.length > 0
  const targetLabel = LANGUAGE_LABELS[job?.options?.targetLang || targetLang] || (job?.options?.targetLang || targetLang).toUpperCase()

  return <main className="srt-export-page">
    <header className="srt-export-head">
      <BackTitle onBack={onBack}>{t('Xuất Phụ Đề', 'Export subtitles')}</BackTitle>
      <p>{t('Tạo phụ đề từ audio/video hoặc định dạng lại caption có sẵn để dùng trong CapCut, YouTube và các trình dựng video.', 'Create subtitles from audio/video or reformat existing captions for CapCut, YouTube, and video editors.')}</p>
    </header>
    <section className="srt-export-card">
      <div className="srt-export-tabs" role="tablist" aria-label="Nguồn phụ đề">
        <button className={kind === 'media' ? 'active' : undefined} onClick={() => { setKind('media'); setFile(null) }}>{t('Từ audio / video', 'From audio / video')}</button>
        <button className={kind === 'caption' ? 'active' : undefined} onClick={() => { setKind('caption'); setFile(null) }}>{t('Từ SRT / caption / file', 'From SRT / caption / file')}</button>
        <button className={kind === 'manual' ? 'active' : undefined} onClick={() => { setKind('manual'); setFile(null) }}>{t('Nhập bằng tay', 'Enter manually')}</button>
        <button className={kind === 'url' ? 'active' : undefined} onClick={() => { setKind('url'); setFile(null) }}>{t('Từ URL', 'From URL')}</button>
      </div>
      <div className="srt-export-body">
        <h2>{kind === 'media' ? 'Chọn audio hoặc video' : kind === 'caption' ? 'Chọn file phụ đề có sẵn' : kind === 'manual' ? 'Nhập nội dung caption' : 'Dán URL từ nền tảng hoặc file trực tiếp'}</h2>
        <p>{kind === 'media' ? 'Whisper tự nhận dạng lời nói và giữ mốc thời gian.' : kind === 'manual' ? 'Mỗi dòng là một caption. Với nội dung có timecode, hãy dùng định dạng SRT.' : kind === 'url' ? 'Ưu tiên subtitle/caption sẵn có trên nền tảng. Khi không có, app mới tải audio và dùng Whisper.' : 'Hỗ trợ SRT, VTT và TXT. SRT/VTT giữ timecode; TXT chia mỗi dòng thành một caption ngắn.'}</p>
        {kind === 'manual' ? <textarea className="srt-export-textarea" value={manualText} onChange={(event) => setManualText(event.target.value)} placeholder="Nhập từng câu phụ đề, mỗi dòng một caption…" rows={7} /> : kind === 'url' ? <input className="srt-export-url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://www.tiktok.com/... hoặc https://youtube.com/..." /> : <><input ref={inputRef} type="file" accept={accepted} hidden onChange={(event) => setFile(event.target.files?.[0] || null)} /><button className="srt-export-picker" type="button" onClick={() => inputRef.current?.click()}>{file ? file.name : 'Chọn file'}</button>{file && <span className="srt-export-file">{(file.size / 1024 / 1024).toFixed(file.size > 10 * 1024 * 1024 ? 0 : 1)} MB</span>}</>}
      </div>
      <div className="srt-export-settings">
        <label><span>Kiểu phụ đề</span><select value={outputMode} onChange={(event) => setOutputMode(event.target.value as OutputMode)}><option value="original">Bản gốc</option><option value="translated">Bản dịch</option><option value="bilingual">Song ngữ (2 bộ file: gốc và dịch)</option></select></label>
        <label><span>Ngôn ngữ gốc</span><select value={sourceLang} onChange={(event) => setSourceLang(event.target.value)}><option value="auto">Tự động nhận diện</option><option value="vi">Tiếng Việt</option><option value="en">Tiếng Anh</option><option value="zh">Tiếng Trung</option><option value="ja">Tiếng Nhật</option><option value="ko">Tiếng Hàn</option></select></label>
        {outputMode !== 'original' && <><label><span>Ngôn ngữ dịch</span><select value={targetLang} onChange={(event) => setTargetLang(event.target.value)}><option value="vi">Tiếng Việt</option><option value="en">Tiếng Anh</option><option value="zh">Tiếng Trung</option><option value="ja">Tiếng Nhật</option><option value="ko">Tiếng Hàn</option></select></label><label><span>Công cụ dịch</span><select value={translator} onChange={(event) => setTranslator(event.target.value as typeof translator)}><option value="google">Google Translate</option><option value="mymemory">MyMemory</option><option value="tiktok">TikTok Translate</option><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="gemini">Gemini</option><option value="deepseek">DeepSeek</option><option value="openrouter">OpenRouter</option><option value="grok">Grok (xAI)</option><option value="nvidia">NVIDIA NIM</option></select></label></>}
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
      {job.status === 'done' && (isBilingualResult ? <div className="srt-export-download-groups">
        <section><h2>Phụ đề gốc</h2><div className="srt-export-downloads">{sourceFiles.map((name) => <a key={name} href={`/api/srt-export/jobs/${job.id}/files/${encodeURIComponent(name)}`} download><IconDownload size={15} /><span>{name}</span><small>Tải về</small></a>)}</div></section>
        <section><h2>Bản dịch · {targetLabel}</h2><div className="srt-export-downloads">{translatedFiles.map((name) => <a key={name} href={`/api/srt-export/jobs/${job.id}/files/${encodeURIComponent(name)}`} download><IconDownload size={15} /><span>{name}</span><small>Tải về</small></a>)}</div></section>
        <div className="srt-export-downloads">{zipFiles.map((name) => <a key={name} className="primary" href={`/api/srt-export/jobs/${job.id}/files/${encodeURIComponent(name)}`} download><IconDownload size={15} /><span>{name}</span><small>Tải tất cả</small></a>)}</div>
      </div> : <div className="srt-export-downloads">
        {job.files.map((name) => <a key={name} className={name.endsWith('.zip') ? 'primary' : undefined} href={`/api/srt-export/jobs/${job.id}/files/${encodeURIComponent(name)}`} download><IconDownload size={15} /><span>{name}</span><small>Tải về</small></a>)}
      </div>)}
    </section>}
  </main>
}
