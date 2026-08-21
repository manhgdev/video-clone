import { useCallback, useRef, useState, useEffect, type DragEvent } from 'react'
import { localize, useLocale } from '@/app/i18n'
import { BackTitle } from '@/shared/components/BackTitle'
import './VideoCleanerPage.css'

// Types
export type CleanMethod = 'metadata' | 'reencode' | 'optimize' | 'logo'
export type JobStatus = 'queued' | 'processing' | 'done' | 'error' | 'cancelled'
export type ResultTab = 'all' | 'processing' | 'done' | 'error'

interface FileInfo {
  file: File
  name: string
  size: number
  resolution?: string
  fps?: number
  duration?: number
  videoCodec?: string
  audioCodec?: string
}

export interface CleanJob {
  id: string
  filename: string
  method: CleanMethod
  status: JobStatus
  progress: number
  outputSize?: number
  startedAt?: number
  finishedAt?: number
  error?: string
}

export interface AdvancedOptions {
  videoCodec: 'copy' | 'libx264' | 'libx265'
  container: 'mp4' | 'mkv' | 'mov'
  crf: number
  preset: string
  audioMode: string
  removeContainerMeta: boolean
  removeVideoMeta: boolean
  removeAudioMeta: boolean
  removeChapters: boolean
  keepResolution: boolean
  keepFps: boolean
  pixelFormat: boolean
  faststart: boolean
  overwrite: boolean
}

const DEFAULT_OPTIONS: AdvancedOptions = {
  videoCodec: 'copy',
  container: 'mp4',
  crf: 19,
  preset: 'slow',
  audioMode: 'copy',
  removeContainerMeta: true,
  removeVideoMeta: true,
  removeAudioMeta: true,
  removeChapters: true,
  keepResolution: true,
  keepFps: true,
  pixelFormat: true,
  faststart: true,
  overwrite: false,
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

function methodLabel(method: CleanMethod, t: (vi: string, en: string) => string) {
  if (method === 'metadata') return t('Xóa metadata', 'Remove metadata')
  if (method === 'reencode') return t('Tái mã hóa', 'Re-encode')
  if (method === 'optimize') return t('Tối ưu', 'Optimize')
  return t('Xóa logo / watermark', 'Remove logo / watermark')
}
const STATUS_LABELS: Record<JobStatus, string> = {
  queued: 'Chờ xử lý',
  processing: 'Đang xử lý',
  done: 'Hoàn thành',
  error: 'Lỗi',
  cancelled: 'Đã hủy',
}

const ACTIVE_STATES = new Set<JobStatus>(['queued', 'processing'])

// ponytail: inline SVGs — no icon library
function SvgCloud() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />
      <path d="M12 15V9M9 12l3-3 3 3" />
    </svg>
  )
}
function SvgCheck() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}
function SvgVideo() {
  return (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect width="14" height="14" x="2" y="5" rx="2" />
      <path d="m16 10 6-3v10l-6-3z" />
    </svg>
  )
}
function SvgChevron({ open }: { open: boolean }) {
  return (
    <svg style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

const LS_VC_JOBS = 'videoclone.vc.jobs'
const LS_VC_METHOD = 'videoclone.vc.method'
const LS_VC_OPTS = 'videoclone.vc.opts'

function loadJobs(): CleanJob[] {
  try {
    const raw = localStorage.getItem(LS_VC_JOBS)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function loadMethod(): CleanMethod {
  const m = localStorage.getItem(LS_VC_METHOD) as CleanMethod
  return ['metadata', 'reencode', 'optimize', 'logo'].includes(m) ? m : 'metadata'
}

function loadOpts(): AdvancedOptions {
  try {
    const raw = localStorage.getItem(LS_VC_OPTS)
    if (!raw) return DEFAULT_OPTIONS
    return { ...DEFAULT_OPTIONS, ...JSON.parse(raw) }
  } catch {
    return DEFAULT_OPTIONS
  }
}

import { cleanerApi } from '@/features/cleaner/cleaner.api'

export default function VideoCleanerPage({ onBack }: { onBack: () => void }) {
  const { locale } = useLocale()
  const t = (vi: string, en: string) => localize(locale, vi, en)
  const [selectedFiles, setSelectedFiles] = useState<FileInfo[]>([])
  const [method, setMethod] = useState<CleanMethod>(loadMethod)
  const [options, setOptions] = useState<AdvancedOptions>(loadOpts)
  const [jobs, setJobs] = useState<CleanJob[]>(loadJobs)
  const [activeTab, setActiveTab] = useState<ResultTab>('all')
  const [isDragging, setIsDragging] = useState(false)
  const [logExpanded, setLogExpanded] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const [optionsExpanded, setOptionsExpanded] = useState(true)
  const [previewJobId, setPreviewJobId] = useState<string | null>(null)

  useEffect(() => { localStorage.setItem(LS_VC_JOBS, JSON.stringify(jobs)) }, [jobs])
  useEffect(() => { localStorage.setItem(LS_VC_METHOD, method) }, [method])
  useEffect(() => { localStorage.setItem(LS_VC_OPTS, JSON.stringify(options)) }, [options])

  // Polling real backend jobs
  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const backendJobs = await cleanerApi.list()
        if (active) setJobs(backendJobs)
      } catch (e) {
        // ignore polling errors
      }
    }
    
    // Initial fetch
    poll()
    
    // Poll every 1.5s
    const interval = setInterval(poll, 1500)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [])

  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    if (!isDragging) setIsDragging(true)
  }, [isDragging])

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }, [])

  const addFiles = useCallback((files: FileList | File[]) => {
    setSelectedFiles(prev => {
      const existingNames = new Set(prev.map(p => p.name))
      const valid: FileInfo[] = Array.from(files)
        .filter(f => {
          if (existingNames.has(f.name)) return false
          if (f.type.startsWith('video/')) return true
          return /\.(mp4|mkv|mov|avi|webm|flv|wmv|m4v|ts)$/i.test(f.name)
        })
        .map(file => ({ file, name: file.name, size: file.size }))
      return valid.length ? [...prev, ...valid] : prev
    })
  }, [])

  const handleDrop = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files)
  }, [addFiles])

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      const filesArray = Array.from(e.target.files)
      addFiles(filesArray)
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [addFiles])

  const startProcessing = async () => {
    if (!selectedFiles.length) return
    
    const files = selectedFiles.map(f => f.file)
    try {
      const newJobs = await cleanerApi.start(files, method, options)
      
      setJobs(prev => {
        const map = new Map(prev.map(j => [j.id, j]))
        newJobs.forEach(nj => map.set(nj.id, nj))
        return Array.from(map.values())
      })
      
      setLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${t('Đã gửi', 'Submitted')} ${files.length} ${t('file xử lý', 'files for processing')} · ${t('phương pháp', 'method')}: ${methodLabel(method, t)}`])
      setActiveTab('all')
      setSelectedFiles([]) // Clear input after submission
    } catch (e: any) {
      alert(`Lỗi khi tạo job: ${e.message}`)
    }
  }

  const filteredJobs = jobs.filter(job => {
    if (activeTab === 'all') return true
    if (activeTab === 'processing') return job.status === 'queued' || job.status === 'processing'
    return job.status === activeTab
  })

  const counts = {
    all: jobs.length,
    processing: jobs.filter(j => j.status === 'queued' || j.status === 'processing').length,
    done: jobs.filter(j => j.status === 'done').length,
    error: jobs.filter(j => j.status === 'error').length,
  }

  const opt = (key: keyof AdvancedOptions, val: unknown) => setOptions(o => ({ ...o, [key]: val }))

  return (
    <div className="vc-page">
      <div className="vc-head">
          <BackTitle onBack={onBack}>Làm sạch video</BackTitle>
          <p>Xóa metadata không cần thiết và tái mã hóa video để bảo vệ quyền riêng tư.</p>
        </div>

        <div className="vc-grid">
          {/* ── Left Column ── */}
          <div className="vc-col">

            {/* Card 1 — Tải video */}
            <div className="vc-card">
              <div className="vc-card-title">
                <h2><span className="vc-num">1</span>Tải video</h2>
              </div>
              <input type="file" ref={fileInputRef} style={{ display: 'none' }} multiple onChange={handleFileChange} />
              {selectedFiles.length === 0 ? (
                <div
                  className={`vc-dropzone${isDragging ? ' is-drag' : ''}`}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <SvgCloud />
                  <div className="vc-dropzone-text">{t('Kéo & thả file vào đây', 'Drag & drop files here')}</div>
                  <div className="vc-dropzone-or">{t('hoặc', 'or')}</div>
                  <button className="vc-btn" type="button" onClick={e => { e.stopPropagation(); fileInputRef.current?.click() }}>{t('Chọn file', 'Choose files')}</button>
                  <div className="vc-dropzone-note">{t('Hỗ trợ: MP4, MOV, MKV, WebM — tối đa 10GB', 'Supports MP4, MOV, MKV, WebM — up to 10 GB')}</div>
                </div>
              ) : (
                <div className="vc-file-list">
                  <div className="vc-file-bar" style={{ marginBottom: 6 }}>
                    <span style={{ fontSize: '.78rem', fontWeight: 550 }}>{selectedFiles.length} file đã chọn</span>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="vc-btn" type="button" onClick={() => fileInputRef.current?.click()}>+ Thêm</button>
                      <button className="vc-btn vc-btn-danger" type="button" onClick={() => setSelectedFiles([])}>Xóa hết</button>
                    </div>
                  </div>
                  <div className="vc-file-items-scroll">
                    {selectedFiles.map((f, i) => (
                      <div key={i} className="vc-file-item">
                        <span>{f.name}</span>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                          <span className="vc-file-size">{formatBytes(f.size)}</span>
                          <button className="vc-file-remove" type="button" onClick={() => setSelectedFiles(prev => prev.filter((_, idx) => idx !== i))} title="Xóa file này">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Card 2 — Phương pháp */}
            <div className="vc-card">
              <div className="vc-card-title">
                <h2><span className="vc-num">2</span>{t('Phương pháp xử lý', 'Processing method')}</h2>
              </div>
              <div className="vc-methods">
                {([
                  { key: 'metadata' as const, title: t('Xóa metadata nhanh', 'Quick metadata cleanup'), badge: t('Khuyên dùng', 'Recommended'), badgeCls: 'green',
                    desc: t('Stream copy · nhanh · không cần GPU', 'Stream copy · fast · no GPU') },
                  { key: 'reencode' as const, title: t('Tái mã hóa', 'Re-encode'), badge: t('Chất lượng cao', 'High quality'), badgeCls: 'blue',
                    desc: t('H.264/H.265 · chuẩn hóa pixel format', 'H.264/H.265 · normalize pixel format') },
                  { key: 'optimize' as const, title: t('Tối ưu dung lượng', 'Optimize file size'), badge: t('Tiết kiệm', 'Smaller file'), badgeCls: 'amber',
                    desc: t('CRF nén · giảm kích thước file', 'CRF compression · smaller file') },
                  { key: 'logo' as const, title: t('Xóa logo / watermark', 'Remove logo / watermark'), badge: t('OCR tự nhận diện', 'OCR detection'), badgeCls: 'blue',
                    desc: t('Quét watermark chữ ở góc: Veo, Grok, Kling, TikTok, UID…', 'Scans text corner marks: Veo, Grok, Kling, TikTok, UID…') },
                ]).map(m => (
                  <button type="button" key={m.key} className={`vc-method${method === m.key ? ' is-active' : ''}`} onClick={() => setMethod(m.key)}>
                    <div className="vc-check"><SvgCheck /></div>
                    <div className={`vc-badge ${m.badgeCls}`}>{m.badge}</div>
                    <div className="vc-method-title">{m.title}</div>
                    <div className="vc-method-desc">{m.desc}</div>
                  </button>
                ))}
              </div>
              {method === 'logo' ? (
                <div className="vc-logo-targets">
                  <strong>{t('Tự nhận diện logo/watermark chữ', 'Automatic text-logo detection')}</strong>
                  <span>{t('Quét mọi nhãn chữ ổn định ở góc video. Veo, Grok và Kling chỉ là ví dụ; logo thuần hình không có chữ cần xử lý thủ công.', 'Scans any stable text label at video edges. Veo, Grok, and Kling are examples; image-only logos need manual treatment.')}</span>
                </div>
              ) : null}
            </div>

            {/* Card 3 — Tùy chọn nâng cao */}
            <div className="vc-card">
              <div className="vc-card-title" style={{ cursor: 'pointer', marginBottom: optionsExpanded ? undefined : 0 }} onClick={() => setOptionsExpanded(!optionsExpanded)}>
                <h2><span className="vc-num">3</span>Tùy chọn nâng cao (đề xuất giữ mặc định)</h2>
                <SvgChevron open={optionsExpanded} />
              </div>
              {optionsExpanded && (
                <>
                  <div className="vc-opts-row">
                    <div className="vc-opts-group">
                      <label>Codec video</label>
                      <select className="vc-select" value={options.videoCodec} onChange={e => opt('videoCodec', e.target.value)}>
                        <option value="copy">Giữ nguyên</option>
                        <option value="libx264">H.264</option>
                        <option value="libx265">H.265</option>
                      </select>
                    </div>
                    <div className="vc-opts-group">
                      <label>Container đầu ra</label>
                      <select className="vc-select" value={options.container} onChange={e => opt('container', e.target.value)}>
                        <option value="mp4">MP4</option>
                        <option value="mkv">MKV</option>
                        <option value="mov">MOV</option>
                      </select>
                    </div>
                    <div className="vc-opts-group">
                      <label>CRF (Chất lượng)</label>
                      <select className="vc-select" value={options.crf} onChange={e => opt('crf', +e.target.value)}>
                        {[18, 19, 20, 21, 22, 23, 24, 25].map(v => <option key={v} value={v}>{v}{v === 19 ? ' (Mặc định)' : v <= 18 ? ' (Cao)' : v >= 24 ? ' (Thấp)' : ''}</option>)}
                      </select>
                    </div>
                    <div className="vc-opts-group">
                      <label>Preset FFmpeg</label>
                      <select className="vc-select" value={options.preset} onChange={e => opt('preset', e.target.value)}>
                        {['ultrafast', 'veryfast', 'fast', 'medium', 'slow', 'slower'].map(p => <option key={p} value={p}>{p}{p === 'slow' ? ' (Mặc định)' : ''}</option>)}
                      </select>
                    </div>
                    <div className="vc-opts-group">
                      <label>Audio</label>
                      <select className="vc-select" value={options.audioMode} onChange={e => opt('audioMode', e.target.value)}>
                        <option value="copy">Giữ nguyên</option>
                        <option value="aac128">AAC 128k</option>
                        <option value="aac160">AAC 160k</option>
                        <option value="aac192">AAC 192k</option>
                        <option value="none">Không có audio</option>
                      </select>
                    </div>
                  </div>

                  <div className="vc-checks">
                    {([
                      ['removeContainerMeta', 'Xóa metadata container'],
                      ['removeVideoMeta', 'Xóa metadata video stream'],
                      ['removeAudioMeta', 'Xóa metadata audio stream'],
                      ['removeChapters', 'Xóa chapter'],
                      ['keepResolution', 'Giữ nguyên độ phân giải'],
                      ['keepFps', 'Giữ nguyên FPS'],
                      ['pixelFormat', 'Chuẩn hóa yuv420p'],
                      ['faststart', 'Tối ưu phát web (faststart)'],
                      ['overwrite', 'Ghi đè file nếu đã tồn tại'],
                    ] as const).map(([key, label]) => (
                      <label key={key} className="vc-checkbox">
                        <input type="checkbox" checked={options[key] as boolean} onChange={e => opt(key, e.target.checked)} />
                        {label}
                      </label>
                    ))}
                  </div>

                  <div className="vc-actions">
                    <button className="vc-btn-link" type="button">Xem hướng dẫn chi tiết</button>
                    <button className="vc-btn vc-btn-primary" type="button" disabled={!selectedFiles.length} onClick={startProcessing}>
                      Bắt đầu xử lý
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* ── Right Column ── */}
          <div className="vc-col">

            {/* Card 4 — Kết quả */}
            <div className="vc-card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <div className="vc-card-title">
                <h2>
                  <span className="vc-num">4</span>Kết quả
                  {jobs.length > 0 && <span className="vc-job-count" style={{ marginLeft: 6 }}>{jobs.length} job</span>}
                </h2>
                {jobs.length > 0 && (
                  <button className="vc-btn vc-btn-danger" type="button" onClick={async () => {
                    for (const job of jobs) {
                      await cleanerApi.remove(job.id).catch(() => {})
                    }
                    setJobs([])
                    setLogs([])
                  }}>Xóa tất cả</button>
                )}
              </div>

              <div className="vc-tabs">
                {(['all', 'processing', 'done', 'error'] as const).map(tab => (
                  <button key={tab} type="button" className={`vc-tab${activeTab === tab ? ' is-active' : ''}`} onClick={() => setActiveTab(tab)}>
                    {tab === 'all' ? 'Tất cả' : tab === 'processing' ? 'Đang xử lý' : tab === 'done' ? 'Hoàn thành' : 'Lỗi'}
                    {' '}({counts[tab]})
                  </button>
                ))}
              </div>

              {filteredJobs.length === 0 ? (
                <div className="vc-empty">
                  <SvgVideo />
                  <strong>Chưa có job nào</strong>
                  <span>Tải video lên và chọn phương pháp để bắt đầu xử lý.</span>
                </div>
              ) : (
                <div className="vc-table-wrap">
                  <table className="vc-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>File</th>
                        <th>Phương pháp</th>
                        <th>Trạng thái</th>
                        <th>Kích thước</th>
                        <th>Thao tác</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredJobs.map((job, idx) => (
                        <tr key={job.id}>
                          <td>{idx + 1}</td>
                          <td title={job.filename} style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{job.filename}</td>
                          <td>{methodLabel(job.method, t)}</td>
                          <td>
                            <span className={`vc-status ${job.status}`}>{STATUS_LABELS[job.status]}</span>
                            {job.status === 'processing' && (
                              <div className="vc-progress" style={{ marginTop: 4 }}>
                                <div className="vc-progress-bar" style={{ width: `${job.progress}%` }} />
                              </div>
                            )}
                          </td>
                          <td>{job.outputSize ? formatBytes(job.outputSize) : '—'}</td>
                          <td>
                            {job.status === 'done' ? (
                              <div style={{ display: 'flex', gap: '10px' }}>
                                <button className="vc-btn-link" type="button" title="Xem trước file kết quả" onClick={() => setPreviewJobId(job.id)}>Xem</button>
                                <button className="vc-btn-link" type="button" title="Mở thư mục chứa file trên máy tính" onClick={async () => {
                                  try {
                                    await cleanerApi.reveal(job.id)
                                  } catch (e: any) {
                                    alert(`Lỗi mở file: ${e.message}`)
                                  }
                                }}>Mở thư mục</button>
                              </div>
                            ) : ACTIVE_STATES.has(job.status) ? (
                              <div style={{ display: 'flex', gap: '10px' }}>
                                <button className="vc-btn-link" type="button" style={{ color: '#ef4444' }} onClick={async () => {
                                  try {
                                    await cleanerApi.cancel(job.id)
                                  } catch (e: any) {
                                    alert(`Lỗi hủy job: ${e.message}`)
                                  }
                                }}>Hủy</button>
                              </div>
                            ) : (
                              '—'
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Log chi tiết */}
              <div className="vc-log">
                <div className="vc-log-header" onClick={() => setLogExpanded(!logExpanded)}>
                  <span>Log chi tiết</span>
                  <SvgChevron open={logExpanded} />
                </div>
                {logExpanded && (
                  <div className="vc-log-content">
                    {logs.length === 0 ? 'Chưa có log...' : logs.join('\n')}
                  </div>
                )}
              </div>
            </div>

            {/* Card 5 — Hướng dẫn nhanh */}
            <div className="vc-card">
              <div className="vc-card-title">
                <h2><span className="vc-num">5</span>Hướng dẫn nhanh</h2>
              </div>
              <div className="vc-guide">
                <div className="vc-guide-col">
                  <h3>Xóa metadata</h3>
                  <ul><li>Không thay đổi chất lượng</li><li>Xử lý cực nhanh (vài giây)</li></ul>
                </div>
                <div className="vc-guide-col">
                  <h3>Tái mã hóa</h3>
                  <ul><li>Tạo file mới hoàn toàn</li><li>Chuẩn hóa container & codec</li></ul>
                </div>
                <div className="vc-guide-col">
                  <h3>Tối ưu dung lượng</h3>
                  <ul><li>Nén bằng CRF</li><li>Cân bằng kích thước & chất lượng</li></ul>
                </div>
              </div>
            </div>
          </div>
        </div>

      <div className="vc-tip">
        <strong>Mẹo:</strong> Tái mã hóa có thể làm thay đổi nhẹ dung lượng hoặc chất lượng. Hãy giữ file gốc cho đến khi kiểm tra xong kết quả.
      </div>
      
      {/* Video Preview Modal */}
      {previewJobId && (
        <div className="vc-modal-overlay" style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.8)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ position: 'relative', width: '80%', maxWidth: '1000px', backgroundColor: '#000', borderRadius: 8, overflow: 'hidden' }} onClick={e => e.stopPropagation()}>
            <video 
              src={`/api/cleaner/jobs/${previewJobId}/file`} 
              controls 
              autoPlay 
              style={{ width: '100%', maxHeight: '80vh', display: 'block' }} 
            />
            <button 
              onClick={() => setPreviewJobId(null)}
              style={{ position: 'absolute', top: 10, right: 10, background: 'rgba(0,0,0,0.5)', color: '#fff', border: 'none', borderRadius: '50%', width: 32, height: 32, cursor: 'pointer', fontSize: 18, lineHeight: '32px', textAlign: 'center' }}
            >
              &times;
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
