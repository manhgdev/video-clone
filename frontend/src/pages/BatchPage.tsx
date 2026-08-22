import { useEffect, useState } from 'react'
import { applyEngineProfile, defaultSettings } from '@/app/appSettings'
import { localize, useLocale } from '@/app/i18n'
import type { ProjectSettings } from '@/features/project/project.types'
import { CloneBatchSettingsPanel } from '@/features/studio/CloneBatchSettingsPanel'
import { studioApi, type QueueJob } from '@/features/studio/studio.api'
import { AudioSlider, CaptionModePicker, ReviewLangFields, ReviewLeftPanel, ReviewRightPanel, useVoices } from '@/features/studio/ReviewSettingsPanel'
import { BackTitle } from '@/shared/components/BackTitle'
import { IconArrowRight, IconGear } from '@/shared/components/Icons'
import { DEFAULT_REVIEW_SETTINGS, STYLE_TO_PIPE, type ReviewSettings } from '@/features/studio/reviewSettings'
import './StudioPages.css'
import './FilmPage.css'

type Props = {
  onBack: () => void
  onOpenEditor?: (projectId: string) => void
  onOpenReviewProjects: () => void
}

type BatchTab = 'clone' | 'review'
const BATCH_TAB_LS = 'videoclone.batchTab'
const BATCH_CLONE_SETTINGS_LS = 'videoclone.batchCloneSettings'
const BATCH_CLONE_SETTINGS_VERSION_LS = 'videoclone.batchCloneSettingsVersion'
const BATCH_CLONE_SETTINGS_VERSION = '3'
const BATCH_REVIEW_SETTINGS_LS = 'videoclone.batchReviewSettings'

function loadBatchTab(): BatchTab {
  try {
    return localStorage.getItem(BATCH_TAB_LS) === 'review' ? 'review' : 'clone'
  } catch {
    return 'clone'
  }
}

function loadBatchReviewSettings(): ReviewSettings {
  try {
    const raw = localStorage.getItem(BATCH_REVIEW_SETTINGS_LS)
    const parsed = raw ? JSON.parse(raw) as Partial<ReviewSettings> : null
    return { ...DEFAULT_REVIEW_SETTINGS, ...parsed }
  } catch {
    return DEFAULT_REVIEW_SETTINGS
  }
}

/**
 * Batch jobs need a snapshot which is independent from the settings used by
 * the regular Clone Video page. The first separate Batch version intentionally
 * starts from clean Clone defaults: audio filtering is off and all pipeline
 * controls are usable before a user chooses an option.
 */
function loadBatchCloneSettings(): ProjectSettings {
  const fallback: ProjectSettings = {
    ...defaultSettings,
    // Batch should preserve the source video by default. Captions are an
    // explicit output choice, rather than an implicit burn-in on every job.
    burnSubs: false,
    engineProfiles: { ...defaultSettings.engineProfiles },
  }
  try {
    // Version 1 was copied from the regular Clone settings, which could carry
    // a "no translation" state and make unrelated Batch fields appear locked.
    if (localStorage.getItem(BATCH_CLONE_SETTINGS_VERSION_LS) !== BATCH_CLONE_SETTINGS_VERSION) {
      return fallback
    }
    const raw = localStorage.getItem(BATCH_CLONE_SETTINGS_LS)
    if (!raw) return fallback.engine === 'subtitle' ? applyEngineProfile(fallback, 'whisper') : fallback
    const saved = JSON.parse(raw) as Partial<ProjectSettings>
    const merged = {
      ...fallback,
      ...saved,
      engineProfiles: { ...fallback.engineProfiles, ...saved.engineProfiles },
    }
    return merged.engine === 'subtitle' ? applyEngineProfile(merged, 'whisper') : merged
  } catch {
    return fallback.engine === 'subtitle' ? applyEngineProfile(fallback, 'whisper') : fallback
  }
}

export default function BatchPage({ onBack, onOpenEditor, onOpenReviewProjects }: Props) {
  const { locale } = useLocale()
  const t = (vi: string, en: string) => localize(locale, vi, en)
  const [tab, setTab] = useState<BatchTab>(loadBatchTab)
  const [sources, setSources] = useState<string[]>([])
  const [outputDir, setOutputDir] = useState('')
  const [recursive, setRecursive] = useState(true)
  const [overwrite, setOverwrite] = useState('rename')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [cloneSettings, setCloneSettings] = useState<ProjectSettings>(loadBatchCloneSettings)
  const [reviewSettings, setReviewSettings] = useState<ReviewSettings>(loadBatchReviewSettings)
  const [jobs, setJobs] = useState<QueueJob[]>([])
  const [pauseAll, setPauseAll] = useState(false)
  const [error, setError] = useState('')
  const cloneVoices = useVoices(cloneSettings.targetLang === 'none' ? 'vi' : cloneSettings.targetLang)
  const voices = useVoices(reviewSettings.language)
  const tabJobs = jobs.filter((job) => job.type === tab)
  const hasActiveJobs = jobs.some((job) => job.status === 'running' || job.status === 'queued')

  const setReview = (patch: Partial<ReviewSettings>) => setReviewSettings((cur) => ({ ...cur, ...patch }))
  const setClone = (next: ProjectSettings) => setCloneSettings(next)
  const selectTab = (next: BatchTab) => {
    setTab(next)
    setSettingsOpen(false)
  }

  useEffect(() => {
    try { localStorage.setItem(BATCH_TAB_LS, tab) } catch { /* private mode */ }
  }, [tab])

  useEffect(() => {
    try { localStorage.setItem(BATCH_REVIEW_SETTINGS_LS, JSON.stringify(reviewSettings)) } catch {}
  }, [reviewSettings])

  useEffect(() => {
    try {
      localStorage.setItem(BATCH_CLONE_SETTINGS_LS, JSON.stringify(cloneSettings))
      localStorage.setItem(BATCH_CLONE_SETTINGS_VERSION_LS, BATCH_CLONE_SETTINGS_VERSION)
    } catch {}
  }, [cloneSettings])

  async function refresh() {
    const snap = await studioApi.queue()
    setJobs(snap.jobs || [])
    setPauseAll(Boolean(snap.pauseAll))
  }

  useEffect(() => {
    void refresh()
    if (!hasActiveJobs) return
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 3000)
    return () => window.clearInterval(timer)
  }, [hasActiveJobs])

  useEffect(() => {
    if (!cloneVoices.length || cloneVoices.some((voice) => voice.id === cloneSettings.defaultVoice)) return
    setClone({ ...cloneSettings, defaultVoice: cloneVoices[0].id })
  }, [cloneVoices])

  async function addFiles() {
    const res = await studioApi.pickVideos()
    setSources((cur) => [...new Set([...cur, ...(res.paths || [])])])
  }

  async function addFolder() {
    const res = await studioApi.pickFolder()
    if (res.path) setSources((cur) => [...new Set([...cur, res.path])])
  }

  async function addToQueue() {
    setError('')
    try {
      const settings = tab === 'review'
        ? {
          outputDir, overwrite, naming: '{name}_review',
          style: STYLE_TO_PIPE[reviewSettings.scriptStyle],
          durationSec: reviewSettings.chunkMinutes * 60,
          buildMode: reviewSettings.buildMode,
          chunkMinutes: reviewSettings.chunkMinutes,
          keepSec: reviewSettings.keepSec,
          skipSec: reviewSettings.skipSec,
          originalAudioPct: reviewSettings.originalAudioPct,
          voice: reviewSettings.voice,
          genre: reviewSettings.genre,
          notes: reviewSettings.notes,
          reviewMode: reviewSettings.reviewMode,
          reviewModel: reviewSettings.reviewModel,
          narration: reviewSettings.narration,
          pausePace: reviewSettings.pausePace,
          captionMode: reviewSettings.captionMode,
          ratio: '16:9', language: reviewSettings.language, sourceLang: reviewSettings.sourceLang, recognitionEngine: reviewSettings.recognitionEngine, spoiler: 'none',
          subtitle: true, headless: true,
        }
        : {
          ...cloneSettings,
          engine: cloneSettings.engine === 'subtitle' ? 'whisper' : cloneSettings.engine,
          previewSec: 0,
          runPreviewSec: 0,
          subtitleSource: undefined,
          exportOutputDir: undefined,
          lutAssetId: '',
          hiddenLogoTexts: [],
          outputDir,
          overwrite,
          naming: '{name}_{type}',
        }
      await studioApi.enqueue(tab, sources, settings, recursive)
      setSources([])
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="studio-page">
      <header>
        <div>
          <BackTitle onBack={onBack}>{t('Hàng loạt', 'Batch')}</BackTitle>
          <p>{t('Mỗi tab hiển thị hàng đợi riêng.', 'Each tab shows its own queue.')}</p>
        </div>
        <div className="studio-actions">
          <button type="button" onClick={() => void studioApi.globalAction(pauseAll ? 'resume_all' : 'pause_all').then(refresh)}>
            {pauseAll ? t('Tiếp tục tất cả', 'Resume all') : t('Tạm dừng tất cả', 'Pause all')}
          </button>
          <button type="button" onClick={() => void studioApi.globalAction('retry_failed').then(refresh)}>{t('Thử lại lỗi', 'Retry failed')}</button>
          <button type="button" onClick={() => void studioApi.globalAction('clear_completed').then(refresh)}>{t('Xóa đã xong', 'Clear completed')}</button>
        </div>
      </header>
      <div className="studio-tabs">
        <button type="button" className={tab === 'clone' ? 'active' : undefined} onClick={() => selectTab('clone')}>{t('Clone hàng loạt', 'Clone batch')}</button>
        <button type="button" className={tab === 'review' ? 'active' : undefined} onClick={() => selectTab('review')}>{t('Review hàng loạt', 'Review batch')}</button>
      </div>
      <section className="studio-card">
        <div className="studio-actions">
          <button type="button" onClick={() => void addFiles()}>{t('Thêm file', 'Add files')}</button>
          <button type="button" onClick={() => void addFolder()}>{t('Thêm thư mục', 'Add folder')}</button>
          <button
            type="button"
            className={`studio-settings-toggle${settingsOpen ? ' open' : ''}`}
            aria-expanded={settingsOpen}
            aria-controls="batch-settings-panel"
            onClick={() => setSettingsOpen((open) => !open)}
          >
            <IconGear size={15} />
            {tab === 'review'
              ? t('Cài đặt Review hàng loạt', 'Review batch settings')
              : t('Cài đặt Clone hàng loạt', 'Clone batch settings')}
            <IconArrowRight size={15} className="studio-settings-chevron" />
          </button>
          <button type="button" className="primary" disabled={!sources.length} onClick={() => void addToQueue()}>{t('Thêm vào hàng đợi', 'Add to queue')}</button>
        </div>
        <p className="muted">{outputDir || t('Xuất mặc định vào project', 'Default output is the project folder')} · {sources.length} {t('nguồn', 'sources')}</p>
        {sources.length ? <ul className="studio-files">{sources.map((s) => <li key={s}>{s}</li>)}</ul> : null}
        {error ? <p className="studio-error">{error}</p> : null}
      </section>
      {settingsOpen ? (
        <div id="batch-settings-panel" className="studio-settings-panel">
          <section className="studio-card studio-settings-common">
            <h2>{t('Cài đặt đầu ra', 'Output settings')}</h2>
            <div className="studio-actions">
              <button type="button" onClick={() => void studioApi.pickFolder().then((r) => r.path && setOutputDir(r.path))}>{t('Thư mục xuất', 'Output folder')}</button>
              <label className="check"><input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} /> {t('Quét đệ quy', 'Recursive scan')}</label>
              <select aria-label={t('Xử lý file trùng', 'Existing file handling')} value={overwrite} onChange={(e) => setOverwrite(e.target.value)}>
                <option value="rename">{t('Đổi tên nếu trùng', 'Auto rename')}</option>
                <option value="skip">{t('Bỏ qua file có sẵn', 'Skip existing')}</option>
                <option value="overwrite">{t('Ghi đè', 'Overwrite')}</option>
              </select>
            </div>
            <p className="muted">{outputDir || t('Xuất mặc định vào project', 'Default output is the project folder')}</p>
          </section>
          {tab === 'review' ? (
            <div className="rv-page rv-embed">
              <div className="rv-grid">
                <section className="rv-card">
                  <div className="rv-card-title">
                    <h2>{t('Cấu hình Review hàng loạt', 'Batch review setup')}</h2>
                    <button type="button" className="rv-reset" onClick={() => setReviewSettings(DEFAULT_REVIEW_SETTINGS)}>↻ {t('Đặt lại', 'Reset')}</button>
                  </div>
                  <p className="rv-hint">{t('Cài đặt bên dưới sẽ áp dụng cho tất cả video được thêm vào hàng đợi review hàng loạt.', 'The settings below apply to every video added to the batch review queue.')}</p>
                  <ReviewLangFields settings={reviewSettings} onChange={setReview} />
                  <ReviewLeftPanel settings={reviewSettings} onChange={setReview} />
                  <AudioSlider value={reviewSettings.originalAudioPct} onChange={(v) => setReview({ originalAudioPct: v })} />
                  <CaptionModePicker value={reviewSettings.captionMode} onChange={(v) => setReview({ captionMode: v })} />
                </section>
                <section className="rv-card">
                  <ReviewRightPanel settings={reviewSettings} onChange={setReview} voices={voices} />
                </section>
              </div>
            </div>
          ) : (
            <CloneBatchSettingsPanel settings={cloneSettings} voices={cloneVoices} onChange={setClone} />
          )}
        </div>
      ) : null}
      <section className="studio-card">
        <div className="studio-card-heading">
          <h2>{tab === 'clone' ? t('Hàng đợi Clone hàng loạt', 'Clone batch queue') : t('Hàng đợi Review hàng loạt', 'Review batch queue')}</h2>
          {tab === 'review' ? (
            <button type="button" className="studio-projects-link" onClick={onOpenReviewProjects}>
              {t('Dự án của bạn', 'Your projects')} →
            </button>
          ) : null}
        </div>
        <table className="studio-table">
          <thead>
            <tr>
              <th>{t('Nguồn', 'Source')}</th>
              <th>{t('Trạng thái', 'Status')}</th>
              <th>{t('Tiến độ', 'Progress')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {tabJobs.map((job) => (
              <tr key={job.id}>
                <td title={job.source}>{job.source.split(/[/\\]/).pop()}</td>
                <td>{job.status} · {job.stage}</td>
                <td>{Math.round((job.progress || 0) * 100)}%</td>
                <td className="studio-job-actions">
                  {job.status === 'running' || job.status === 'queued' ? (
                    <>
                      <button type="button" onClick={() => void studioApi.jobAction(job.id, 'pause').then(refresh)}>{t('Dừng', 'Pause')}</button>
                      <button type="button" onClick={() => void studioApi.jobAction(job.id, 'cancel').then(refresh)}>{t('Hủy', 'Cancel')}</button>
                    </>
                  ) : null}
                  {job.status === 'paused' || job.status === 'interrupted' ? (
                    <button type="button" onClick={() => void studioApi.jobAction(job.id, 'resume').then(refresh)}>{t('Tiếp tục', 'Resume')}</button>
                  ) : null}
                  {job.status === 'failed' || job.status === 'cancelled' ? (
                    <button type="button" onClick={() => void studioApi.jobAction(job.id, 'retry').then(refresh)}>{t('Thử lại', 'Retry')}</button>
                  ) : null}
                  {job.status === 'done' && job.projectId && onOpenEditor ? (
                    <button type="button" onClick={() => onOpenEditor(job.projectId!)}>{t('Mở Editor', 'Open Editor')}</button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!tabJobs.length ? <p className="muted">{t('Chưa có job.', 'No jobs yet.')}</p> : null}
      </section>
    </div>
  )
}
