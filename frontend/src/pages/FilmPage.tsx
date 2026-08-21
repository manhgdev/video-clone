import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { localize, useLocale } from '@/app/i18n'
import { api } from '@/features/project/project.api'
import type { HardwareInfo } from '@/features/project/project.types'
import { studioApi, type QueueJob } from '@/features/studio/studio.api'
import { AudioSlider, CaptionModePicker, ReviewLangFields, ReviewLeftPanel, ReviewRightPanel, Stepper, useVoices } from '@/features/studio/ReviewSettingsPanel'
import { DEFAULT_REVIEW_SETTINGS, STYLE_TO_PIPE, modeLabel, resolveBuildMode, type ReviewSettings } from '@/features/studio/reviewSettings'
import { BackTitle } from '@/shared/components/BackTitle'
import './FilmPage.css'

type Props = { onBack: () => void; onOpenEditor?: (projectId: string) => void }
type View = 'list' | 'create'

type Draft = ReviewSettings & {
  source: string
  outputDir: string
  seriesOn: boolean
  seriesTitle: string
  seriesEpisode: number
}

const DRAFT_LS = 'videoclone.reviewDraft'

const DEFAULT_DRAFT: Draft = {
  ...DEFAULT_REVIEW_SETTINGS,
  source: '',
  outputDir: '',
  seriesOn: false,
  seriesTitle: '',
  seriesEpisode: 1,
}

function loadDraft(): Draft {
  try {
    const raw = localStorage.getItem(DRAFT_LS)
    if (!raw) return DEFAULT_DRAFT
    const parsed = JSON.parse(raw) as Partial<Draft> & { cutMode?: string }
    const buildMode = resolveBuildMode(parsed as Record<string, unknown>)
    delete parsed.cutMode
    return { ...DEFAULT_DRAFT, ...parsed, buildMode }
  } catch {
    return DEFAULT_DRAFT
  }
}

function jobTitle(job: QueueJob) {
  const snap = job.settings_snapshot || {}
  const named = String(snap.seriesTitle || '').trim()
  if (named) return named
  const src = job.source || ''
  if (src.startsWith('http')) return src
  return src.split(/[/\\]/).pop() || job.id
}

function jobLog(job: QueueJob) {
  const rows = Array.isArray(job.log) ? job.log.filter(Boolean) : []
  if (!rows.length && job.stage) {
    rows.push(`[Hệ thống] ${job.stage} — ${Math.round((job.progress || 0) * 100)}%`)
  }
  if (job.error && !rows.some((line) => line.includes(job.error || ''))) {
    rows.push(job.error)
  }
  return rows
}

function statusLabel(status: string, t: (vi: string, en: string) => string) {
  if (status === 'running' || status === 'queued') return t('Đang chạy', 'Running')
  if (status === 'paused') return t('Đã dừng', 'Paused')
  if (status === 'interrupted') return t('Gián đoạn', 'Interrupted')
  if (status === 'done') return t('Hoàn thành', 'Done')
  if (status === 'failed' || status === 'cancelled') return t('Đã huỷ / Lỗi', 'Cancelled / Error')
  return status
}

function partStatusLabel(status: string | undefined, t: (vi: string, en: string) => string) {
  if (status === 'running') return t('Đang chạy', 'Running')
  if (status === 'paused') return t('Đã dừng', 'Paused')
  if (status === 'pending') return t('Đang chờ', 'Pending')
  if (status === 'failed') return t('Lỗi', 'Failed')
  return t('Đã huỷ', 'Cancelled')
}

function reviewErrorLabel(error: string, t: (vi: string, en: string) => string) {
  if (error.includes('REVIEW_LLM_REQUIRED')) {
    return t(
      'Review Phim cần ít nhất một model Ollama để viết kịch bản. Hãy cài hoặc chọn model trong Cấu hình rồi chạy lại.',
      'Movie Review requires at least one Ollama model to write the script. Install or select one in Settings, then retry.',
    )
  }
  if (error.includes('REVIEW_LLM_MODEL_UNAVAILABLE')) {
    return t(
      'Model Ollama đã chọn không còn khả dụng. Hãy chọn lại model hoặc bật Ollama.',
      'The selected Ollama model is unavailable. Choose another model or start Ollama.',
    )
  }
  if (error.includes('REVIEW_CLOUD_KEY_REQUIRED')) {
    return t(
      'Chưa có API key cho Cloud AI đã chọn. Mở Cấu hình → Cloud, lưu key rồi chạy lại.',
      'The selected Cloud AI has no API key. Open Settings → Cloud, save its key, then retry.',
    )
  }
  if (error.includes('REVIEW_CLOUD_REQUEST_FAILED')) {
    return t(
      'Cloud AI từ chối hoặc không nhận được request. Kiểm tra API key, Base URL và Model AI Review.',
      'Cloud AI rejected or did not receive the request. Check the API key, base URL, and Review AI model.',
    )
  }
  if (error.includes('REVIEW_LLM_EVIDENCE_INVALID')) {
    return t(
      'AI không tạo được kịch bản có dữ kiện phim đáng tin cậy. Hãy thử lại với model Ollama khác.',
      'AI could not produce a script grounded in reliable movie evidence. Retry with another Ollama model.',
    )
  }
  if (error.includes('REVIEW_TRANSLATION_EMPTY')) {
    return t(
      'Không thể dịch đủ phụ đề nguồn. Hãy kiểm tra ngôn ngữ nguồn hoặc dùng chế độ AI recap.',
      'Not enough source subtitles could be translated. Check the source language or use AI recap mode.',
    )
  }
  return error
}

function fmtDate(job: QueueJob, locale: string) {
  if (!job.createdAt) return job.id
  return new Date(job.createdAt * 1000).toLocaleString(locale === 'vi' ? 'vi-VN' : 'en-US')
}

function relTime(ts: number | undefined, t: (vi: string, en: string) => string) {
  if (!ts) return ''
  const sec = Math.max(0, Date.now() / 1000 - ts)
  if (sec < 60) return t('Vừa xong', 'Just now')
  if (sec < 3600) return `${Math.floor(sec / 60)} ${t('phút trước', 'min ago')}`
  if (sec < 86400) return `${Math.floor(sec / 3600)} ${t('giờ trước', 'hours ago')}`
  return `${Math.floor(sec / 86400)} ${t('ngày trước', 'days ago')}`
}

function downloadHref(jobId: string, part?: number) {
  return studioApi.fileUrl(jobId, { part, download: true })
}

function deviceBadge(hw: HardwareInfo) {
  const accel = (hw.accel || 'cpu').toLowerCase()
  const gpu = accel === 'cuda' || accel === 'metal' || accel === 'mps'
  const name = (hw.gpuName || hw.label || '').trim()
  if (gpu) {
    const show = name && !/^(cpu|gpu)$/i.test(name)
    return show ? `⚡ GPU · ${name}` : '⚡ GPU'
  }
  return '⚡ CPU'
}

function AutoLog({ className, text }: { className?: string; text: string }) {
  const ref = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const node = ref.current
    if (node) node.scrollTop = node.scrollHeight
  }, [text])
  return <pre ref={ref} className={className}>{text}</pre>
}

export default function FilmPage({ onBack, onOpenEditor }: Props) {
  const { locale } = useLocale()
  const t = (vi: string, en: string) => localize(locale, vi, en)
  const [view, setView] = useState<View>('list')
  const [filter, setFilter] = useState<'all' | 'running' | 'done' | 'failed'>('all')
  const [draft, setDraft] = useState<Draft>(loadDraft)
  const [jobs, setJobs] = useState<QueueJob[]>([])
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [logOpen, setLogOpen] = useState(true)
  const [partsOpen, setPartsOpen] = useState<Record<string, boolean>>({})
  const [jobLogOpen, setJobLogOpen] = useState<Record<string, boolean>>({})
  const [preview, setPreview] = useState<{ jobId: string; part?: number; title: string } | null>(null)
  const [cacheOpen, setCacheOpen] = useState(false)
  const [clearingCache, setClearingCache] = useState(false)
  const [editingJobId, setEditingJobId] = useState<string | null>(null)
  const [hw, setHw] = useState<HardwareInfo>({ label: '', accel: 'cpu' })
  const voices = useVoices(draft.language)
  const hasActiveJobs = jobs.some((job) => job.status === 'running' || job.status === 'queued')

  const set = (patch: Partial<Draft>) => setDraft((cur) => {
    const next = { ...cur, ...patch } as Draft & { cutMode?: string }
    delete next.cutMode
    return next
  })

  useEffect(() => {
    if (editingJobId) return
    try {
      localStorage.setItem(DRAFT_LS, JSON.stringify(draft))
    } catch {}
  }, [draft, editingJobId])

  const pullRef = useRef<() => void>(() => undefined)
  useEffect(() => {
    const pull = () => studioApi.queue().then((snap) => setJobs((snap.jobs || []).filter((j) => j.type === 'review'))).catch(() => undefined)
    pullRef.current = pull
    void pull()
    if (!hasActiveJobs) return
    const timer = window.setInterval(() => void pull(), 3000)
    const onVisible = () => { if (document.visibilityState === 'visible') void pull() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [hasActiveJobs])

  useEffect(() => {
    api.hardware().then(setHw).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (error) {
      toast.error(error)
      setError('')
    } else if (note) {
      toast.success(note)
      setNote('')
    }
  }, [note, error])

  const counts = useMemo(() => ({
    all: jobs.length,
    running: jobs.filter((j) => j.status === 'running' || j.status === 'queued').length,
    done: jobs.filter((j) => j.status === 'done').length,
    failed: jobs.filter((j) => j.status === 'failed' || j.status === 'cancelled' || j.status === 'interrupted' || j.status === 'paused').length,
  }), [jobs])

  const visible = jobs.filter((j) => {
    if (filter === 'running') return j.status === 'running' || j.status === 'queued'
    if (filter === 'done') return j.status === 'done'
    if (filter === 'failed') return j.status === 'failed' || j.status === 'cancelled' || j.status === 'interrupted' || j.status === 'paused'
    return true
  })

  const active = jobs.find((j) => j.status === 'running' || j.status === 'queued')
  const logLines = active
    ? jobLog(active)
    : [t('[Hệ thống] Khởi động Review. Sẵn sàng...', '[System] Review ready. Waiting to start...')]

  function saveDraft() {
    localStorage.setItem(DRAFT_LS, JSON.stringify(draft))
    setNote(t('Đã lưu nháp.', 'Draft saved.'))
  }

  function askClearCache() {
    const src = draft.source.trim()
    if (!src || src.startsWith('http')) {
      setError(t('Hãy chọn video trên máy để xóa cache.', 'Choose a local video to clear cache.'))
      return
    }
    setError('')
    setCacheOpen(true)
  }

  async function confirmClearCache() {
    const src = draft.source.trim()
    if (!src || src.startsWith('http')) {
      setCacheOpen(false)
      setError(t('Hãy chọn video trên máy để xóa cache.', 'Choose a local video to clear cache.'))
      return
    }
    setClearingCache(true)
    setError('')
    setNote('')
    try {
      const res = await studioApi.clearReviewCache(src)
      setNote(res.cleared ? t('Đã xóa cache', 'Cache cleared') : t('Không có cache cho video này.', 'No cache for this video.'))
      setCacheOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setCacheOpen(false)
    } finally {
      setClearingCache(false)
    }
  }

  function openCreate(from?: QueueJob, edit = false) {
    if (from?.settings_snapshot) {
      const snap = from.settings_snapshot
      setDraft({ ...DEFAULT_DRAFT, ...snap, source: from.source, buildMode: resolveBuildMode(snap) } as Draft)
    }
    setEditingJobId(edit ? from?.id || null : null)
    setView('create')
    setError('')
  }

  async function pickVideo() {
    const res = await studioApi.pickVideos()
    if (res.paths?.[0]) set({ source: res.paths[0] })
  }

  async function pickOut() {
    const res = await studioApi.pickFolder()
    if (res.path) set({ outputDir: res.path })
  }

  async function createAndRun() {
    if (!draft.source.trim()) {
      setError(t('Hãy dán link hoặc chọn video.', 'Paste a link or choose a video.'))
      return
    }
    if (draft.buildMode === 'smart' && draft.source.startsWith('http')) {
      setError(t('Cắt thông minh cần video có sẵn trên máy. Hãy bấm Chọn video.', 'Smart cut needs a local video file. Click Choose video.'))
      return
    }
    setBusy(true)
    setError('')
    saveDraft()
    try {
      const res = await studioApi.generateReview({
        ...draft,
        cutMode: undefined,
        buildMode: draft.buildMode,
        style: STYLE_TO_PIPE[draft.scriptStyle],
        durationSec: draft.chunkMinutes * 60,
        subtitle: true,
        quality: '1080p',
        ratio: '16:9',
        spoiler: 'none',
        headless: true,
        naming: '{name}_review',
      })
      const createdJob = res.job
      if (createdJob) setJobs((current) => [createdJob, ...current.filter((job) => job.id !== createdJob.id)])
      setView('list')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function saveJobSettings() {
    if (!editingJobId) return
    setBusy(true)
    setError('')
    try {
      const snap = await studioApi.updateJobSettings(editingJobId, {
        ...draft, cutMode: undefined, buildMode: draft.buildMode,
        style: STYLE_TO_PIPE[draft.scriptStyle], durationSec: draft.chunkMinutes * 60,
        subtitle: true, quality: '1080p', ratio: '16:9', spoiler: 'none',
      })
      setJobs((snap.jobs || []).filter((j) => j.type === 'review'))
      setNote(t('Đã lưu cài đặt dự án.', 'Project settings saved.'))
      setEditingJobId(null)
      setView('list')
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message.includes('Không thể đổi cài đặt khi job đang chạy')
        ? t('Không thể lưu cài đặt khi job đang chạy. Hãy dừng job trước.', 'Settings cannot be saved while the job is running. Pause it first.')
        : message)
    } finally {
      setBusy(false)
    }
  }

  function copyLog() {
    navigator.clipboard?.writeText(logLines.join('\n')).catch(() => undefined)
  }

  async function removeJob(job: QueueJob) {
    if (!window.confirm(t(`Xoá dự án «${jobTitle(job)}»?`, `Delete project “${jobTitle(job)}”?`))) return
    const snap = await studioApi.jobAction(job.id, 'remove')
    setJobs((snap.jobs || []).filter((j) => j.type === 'review'))
    if (preview?.jobId === job.id) setPreview(null)
  }

  async function removePart(job: QueueJob, index: number) {
    if (!window.confirm(t(`Xoá phần ${index}?`, `Delete part ${index}?`))) return
    const snap = await studioApi.deletePart(job.id, index)
    setJobs((snap.jobs || []).filter((j) => j.type === 'review'))
    if (preview?.jobId === job.id && preview.part === index) setPreview(null)
  }

  async function rerenderJob(job: QueueJob) {
    setError('')
    try {
      const snap = await studioApi.jobAction(job.id, 'retry')
      setJobs((snap.jobs || []).filter((row) => row.type === 'review'))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const previewModal = preview ? (
    <div className="rv-modal" onClick={() => setPreview(null)}>
      <div className="rv-dialog rv-preview-box" onClick={(e) => e.stopPropagation()}>
        <div className="rv-card-title">
          <h2>{t('Xem trước', 'Preview')}</h2>
          <button type="button" className="rv-ghost" onClick={() => setPreview(null)}>×</button>
        </div>
        <video className="rv-preview-video" src={studioApi.fileUrl(preview.jobId, { part: preview.part })} controls autoPlay />
        <div className="rv-dialog-actions">
          <a className="rv-ghost" href={downloadHref(preview.jobId, preview.part)}>{t('Tải xuống', 'Download')}</a>
          <button type="button" className="rv-run" onClick={() => setPreview(null)}>{t('Đóng', 'Close')}</button>
        </div>
      </div>
    </div>
  ) : null
  if (view === 'list') {
    return (
      <div className="rv-page">
        <div className="rv-top">
          <BackTitle onBack={onBack}>{t('Dự án của bạn', 'Your projects')}<span className="rv-count">({counts.all})</span></BackTitle>
          <button type="button" className="rv-new" onClick={() => openCreate()}>+ {t('Tạo mới', 'New')}</button>
        </div>
        <div className="rv-filters">
          <button type="button" className={`rv-chip${filter === 'all' ? ' on' : ''}`} onClick={() => setFilter('all')}><i className="all" />{t('Tất cả', 'All')} {counts.all}</button>
          <button type="button" className={`rv-chip${filter === 'running' ? ' on' : ''}`} onClick={() => setFilter('running')}><i className="run" />{t('Đang chạy', 'Running')} {counts.running}</button>
          <button type="button" className={`rv-chip${filter === 'done' ? ' on' : ''}`} onClick={() => setFilter('done')}><i className="ok" />{t('Hoàn thành', 'Done')} {counts.done}</button>
          <button type="button" className={`rv-chip${filter === 'failed' ? ' on' : ''}`} onClick={() => setFilter('failed')}><i className="bad" />{t('Dừng / Lỗi', 'Paused / Error')} {counts.failed}</button>
        </div>
        {counts.running ? (
          <div className="rv-banner">● {t('Hàng đợi', 'Queue')}: {Math.max(0, counts.running - 1)} {t('dự án đang chờ', 'projects waiting')}
            {active ? <> · {t('Đang chạy', 'Running')}: {jobTitle(active)} - {fmtDate(active, locale)}</> : null}
          </div>
        ) : null}
        {!visible.length ? (
          <div className="rv-empty">{t('Chưa có dự án review. Bấm Tạo mới để bắt đầu.', 'No review projects yet. Click New to start.')}</div>
        ) : visible.map((job, idx) => {
          const pct = Math.round((job.progress || 0) * 100)
          const snap = job.settings_snapshot || {}
          const running = job.status === 'running' || job.status === 'queued'
          const parts = job.parts && job.parts.length ? job.parts : []
          const doneParts = parts.filter((p) => p.status === 'done').length
          const open = partsOpen[job.id] ?? true
          const logShown = jobLogOpen[job.id] ?? false
          const lines = jobLog(job)
          const mode = resolveBuildMode(snap)
          const showParts = mode === 'accumulate' && parts.length > 1
          const canFile = job.status === 'done' || parts.some((p) => p.status === 'done')
          return (
            <article key={job.id} className="rv-job">
              <div className="rv-job-top">
                <div>
                  <span className="rv-num">#{idx + 1}</span>
                  <h3>{jobTitle(job)} - {fmtDate(job, locale)}</h3>
                  <div className="rv-meta">{relTime(job.updatedAt || job.createdAt, t)} · {fmtDate(job, locale)}</div>
                  <div className="rv-tags">
                    <span>📐 {modeLabel(mode, t)}</span>
                    <span>🎙 {voices.find((v) => v.id === snap.voice)?.name || t('Giọng hệ thống', 'System voice')}</span>
                    <span>📝 {String(snap.scriptStyle || snap.style || '')}</span>
                  </div>
                </div>
                <span className={job.status === 'done' ? 'rv-ok' : running ? 'rv-run-badge' : 'rv-warn'}>● {statusLabel(job.status, t)}</span>
              </div>
              {running ? (
                <>
                  <div className="rv-row" style={{ justifyContent: 'space-between', margin: '8px 0 4px' }}>
                    <span className="rv-meta">{job.stage}</span>
                    <span className="rv-meta">{pct}%</span>
                  </div>
                  <div className="rv-bar"><i style={{ width: `${pct}%` }} /></div>
                </>
              ) : null}
              {showParts ? (
                <div className="rv-parts-panel">
                  <button type="button" className="rv-parts-head" onClick={() => setPartsOpen((s) => ({ ...s, [job.id]: !open }))}>
                    <span>◉ {t('Tiến độ phân đoạn', 'Segment progress')} ({doneParts}/{parts.length}) {open ? '⌃' : '⌄'}</span>
                    <span className="rv-mode-tag">{modeLabel(mode, t)}</span>
                  </button>
                  {open ? <div className="rv-parts">
                  {(parts.length ? parts : [{ index: 1, label: job.stage, status: job.status }]).map((part, i, arr) => {
                    const ready = part.status === 'done'
                    return (
                      <div key={part.index} className="rv-part">
                        <span className={`rv-tree${i === arr.length - 1 ? ' last' : ''}`} />
                        <span className="rv-part-name">🎬 {t('Phần', 'Part')} {part.index}{part.label ? `: ${part.label}` : ''}</span>
                        {ready ? (
                          <span className="rv-part-actions">
                            <span className="rv-ok">✓ {t('Xong', 'Done')}</span>
                            <a className="rv-dl" href={downloadHref(job.id, part.index)}>{t('Tải phần này', 'Download this part')}</a>
                            <button type="button" className="rv-icon-btn" title={t('Xem trước', 'Preview')} onClick={() => setPreview({ jobId: job.id, part: part.index, title: `${t('Phần', 'Part')} ${part.index}` })}>▶</button>
                            <button type="button" className="rv-icon-btn danger" title={t('Xoá', 'Delete')} onClick={() => void removePart(job, part.index)}>✕</button>
                          </span>
                        ) : (
                          <span className="rv-warn">{partStatusLabel(part.status, t)}</span>
                        )}
                      </div>
                    )
                  })}
                  {job.error ? <div className="rv-part"><span /><span>{reviewErrorLabel(job.error, t)}</span><span /></div> : null}
                  </div> : null}
                </div>
              ) : null}
              <div className="rv-row" style={{ marginTop: 10 }}>
                {job.status === 'done' && job.projectId && onOpenEditor ? (
                  <button type="button" className="rv-run" onClick={() => onOpenEditor(job.projectId!)}>▶ {t('Mở Editor', 'Open Editor')}</button>
                ) : null}
                {canFile ? (
                  <>
                    <button type="button" className="rv-ghost" onClick={() => setPreview({ jobId: job.id, title: jobTitle(job) })}>▶ {t('Xem trước', 'Preview')}</button>
                    <a className="rv-ghost" href={downloadHref(job.id)}>{t('Tải xuống', 'Download')}</a>
                  </>
                ) : null}
                {running ? (
                  <>
                    <button type="button" className="rv-run" onClick={() => void studioApi.jobAction(job.id, 'pause')}>Ⅱ {t('Dừng', 'Pause')}</button>
                    <button type="button" className="rv-ghost" onClick={() => void studioApi.jobAction(job.id, 'cancel')}>{t('Hủy', 'Cancel')}</button>
                  </>
                ) : job.status === 'paused' || job.status === 'interrupted' ? (
                  <>
                    <button type="button" className="rv-run" onClick={() => { void studioApi.jobAction(job.id, 'resume').then(() => pullRef.current()) }}>▶ {t('Tiếp tục', 'Resume')}</button>
                    <button type="button" className="rv-ghost" onClick={() => void studioApi.jobAction(job.id, 'cancel')}>{t('Hủy', 'Cancel')}</button>
                  </>
                ) : job.status === 'failed' || job.status === 'cancelled' ? (
                  <button type="button" className="rv-run" onClick={() => void rerenderJob(job)}>▶ {t('Thử lại', 'Retry')}</button>
                ) : (
                  <button type="button" className="rv-run" onClick={() => void rerenderJob(job)}>▶ {t('Render lại', 'Render again')}</button>
                )}
                <button type="button" className="rv-ghost" onClick={() => openCreate(job, true)}>✎ {t('Cài đặt', 'Settings')}</button>
                <button type="button" className="rv-ghost danger" onClick={() => void removeJob(job)}>✕ {t('Xoá', 'Delete')}</button>
              </div>
              <div className="rv-log-h" style={{ marginTop: 8 }}>
                <button type="button" className="rv-ghost" onClick={() => setJobLogOpen((s) => ({ ...s, [job.id]: !logShown }))}>
                  📄 {t('Xem nhật ký tiến trình', 'View progress log')} ({lines.length} {t('dòng', 'lines')}) {logShown ? '▲' : '▼'}
                </button>
                <button type="button" className="rv-mini" onClick={() => navigator.clipboard?.writeText(lines.join('\n'))}>📋 {t('Chép log', 'Copy log')}</button>
                {!showParts ? <span className="rv-mode-tag">{modeLabel(mode, t)}</span> : null}
              </div>
              {logShown ? <AutoLog className="rv-job-log" text={lines.join('\n') || t('[Hệ thống] đang chạy…', '[System] running…')} /> : null}
            </article>
          )
        })}
        {previewModal}
      </div>
    )
  }

  return (
    <div className="rv-page">
      <div className="rv-top">
        <div className="rv-row">
          <button type="button" className="rv-back" onClick={() => setView('list')}>← {t('Quay lại', 'Back')}</button>
          <h1>{editingJobId ? t('Cài đặt dự án', 'Project settings') : t('Tạo dự án mới', 'Create new project')}</h1>
        </div>
        <div className="rv-top-actions">
          {!editingJobId && <button type="button" className="rv-draft" onClick={saveDraft}>🖫 {t('Lưu nháp', 'Save draft')}</button>}
          <button type="button" className="rv-draft" disabled={busy || clearingCache} onClick={askClearCache}>{t('Xóa cache', 'Clear cache')}</button>
          <button type="button" className="rv-run" disabled={busy} onClick={() => void (editingJobId ? saveJobSettings() : createAndRun())}>
            {editingJobId ? t('Lưu cài đặt', 'Save settings') : `+ ${t('Tạo & Chạy', 'Create & Run')}`}
          </button>
        </div>
      </div>
      <div className="rv-grid">
        <section className="rv-card">
          <div className="rv-card-title">
            <h2>{t('Cấu hình & Tối ưu', 'Setup & optimize')}</h2>
            <button type="button" className="rv-reset" onClick={() => setDraft(DEFAULT_DRAFT)}>↻ {t('Đặt lại', 'Reset')}</button>
          </div>
          <label className="rv-field">
            <span>{t('Video gốc', 'Source video')}</span>
            <div className="rv-combo rv-combo-peach">
              <span className="rv-combo-ico">📁</span>
              <input value={draft.source} onChange={(e) => set({ source: e.target.value })} placeholder={t('Dán link video (YouTube, TikTok, FB…) hoặc chọn video…', 'Paste a video link (YouTube, TikTok, FB…) or choose a file…')} />
              <button type="button" className="rv-inbtn" onClick={() => void pickVideo()}>{t('Chọn', 'Choose')}</button>
            </div>
          </label>
          <ReviewLangFields settings={draft} onChange={set} />
          <label className="rv-field">
            <span>{t('Thư mục lưu video', 'Output folder')}</span>
            <div className="rv-combo rv-combo-peach">
              <span className="rv-combo-ico">📂</span>
              <input value={draft.outputDir} onChange={(e) => set({ outputDir: e.target.value })} placeholder={t('Bắt buộc chọn nơi lưu video…', 'Choose where to save the video…')} />
              <button type="button" className="rv-inbtn" onClick={() => void pickOut()}>{t('Chọn', 'Choose')}</button>
            </div>
          </label>

          <ReviewLeftPanel settings={draft} onChange={set} />
          <AudioSlider value={draft.originalAudioPct} onChange={(v) => set({ originalAudioPct: v })} />
          <CaptionModePicker value={draft.captionMode} onChange={(v) => set({ captionMode: v })} />
        </section>

        <section className="rv-card">
          <ReviewRightPanel
            settings={draft}
            onChange={set}
            voices={voices}
            seriesSlot={(
              <div className={`rv-series-box${draft.seriesOn ? ' on' : ''}`}>
                <label className="rv-check">
                  <span>🎞 {t('Bộ nhớ phim bộ (Trí nhớ theo tập)', 'Series memory (per episode)')}</span>
                  <span className="rv-switch">
                    <input type="checkbox" checked={draft.seriesOn} onChange={(e) => set({ seriesOn: e.target.checked })} />
                    <i />
                  </span>
                </label>
                {draft.seriesOn ? (
                  <>
                    <p className="rv-hint">{t('Ghi nhớ nhân vật & diễn biến qua từng tập – giữ liên mạch.', 'Remembers characters and plot across episodes to keep continuity.')}</p>
                    <div className="rv-series-grid">
                      <label className="rv-field">
                        <span>{t('Tên phim / Bộ phim', 'Title / series')}</span>
                        <input value={draft.seriesTitle} onChange={(e) => set({ seriesTitle: e.target.value })} placeholder={t('Nhập tên phim bộ (VD: Hậu Duệ Mặt Trời)…', 'Enter the series title (e.g. Descendants of the Sun)…')} />
                      </label>
                      <label className="rv-field">
                        <span>{t('Tập số', 'Episode')}</span>
                        <Stepper value={draft.seriesEpisode} min={1} onChange={(v) => set({ seriesEpisode: v })} />
                      </label>
                    </div>
                    <p className="rv-hint">💡 {t('Mẹo: Để trống nếu là phim lẻ. Nếu là phim bộ nhiều tập, hãy nhập tên phim để AI nhớ nhân vật cho các tập sau.', 'Tip: leave blank for a standalone film. For a multi-episode series, enter the title so AI remembers characters for later episodes.')}</p>
                  </>
                ) : null}
              </div>
            )}
          />
        </section>
      </div>
      <section className="rv-card rv-log">
        <div className="rv-log-h">
          <button type="button" className="rv-ghost" onClick={() => setLogOpen((v) => !v)}>
            <strong>📃 {t('Nhật ký hoạt động (Tiến trình đang chạy)', 'Activity log (running progress)')} {logOpen ? '▲' : '▼'}</strong>
          </button>
          <div className="rv-log-tools">
            <span className="rv-mini" title={hw.label || hw.gpuName || hw.accel}>{deviceBadge(hw)}</span>
            <button type="button" className="rv-mini" onClick={copyLog}>{t('Sao chép', 'Copy')}</button>
            <button type="button" className="rv-mini danger" onClick={() => undefined}>{t('Xoá nhật ký', 'Clear log')}</button>
          </div>
        </div>
        {logOpen ? <AutoLog text={logLines.join('\n')} /> : null}
      </section>
      {cacheOpen ? (
        <div className="rv-modal" role="presentation" onClick={() => !clearingCache && setCacheOpen(false)}>
          <div
            className="rv-dialog rv-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rv-clear-cache-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="rv-clear-cache-title">{t('Xóa cache', 'Clear cache')}</h2>
            <p>{t('Xóa cache nhận dạng và kịch bản của video này. Video nguồn không bao giờ bị xóa.', 'Clear this video’s transcript and script cache. The source video is never deleted.')}</p>
            <p className="rv-confirm-note">{t('Lần chạy sau sẽ nhận dạng và viết kịch bản lại.', 'The next run will re-transcribe and rewrite the script.')}</p>
            <div className="rv-dialog-actions">
              <button type="button" className="rv-ghost" disabled={clearingCache} onClick={() => setCacheOpen(false)}>{t('Hủy', 'Cancel')}</button>
              <button type="button" className="rv-confirm-go" disabled={clearingCache} onClick={() => void confirmClearCache()}>
                {clearingCache ? t('Đang xóa…', 'Deleting…') : t('Xóa cache', 'Clear cache')}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
