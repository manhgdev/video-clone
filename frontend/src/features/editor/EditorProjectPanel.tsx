import React from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import { resolvedSpeakerProfiles, speakerRoleOptions } from '@/features/project/speakerProfiles'
import { localize, useLocale } from '@/app/i18n'
import { ScrollArea } from '@/shared/ui/scroll-area'
import { formatTimecode, PropLabel } from '@/features/editor/lib'

type Props = {
  tab: 'workflow' | 'speakers'
  segments: Segment[]
  settings: ProjectSettings
  voices: { id: string; name: string }[]
  busy: boolean
  jobStep: string
  jobProgress: number
  onSettings: (settings: ProjectSettings) => void
  onRunPipeline?: (previewSec: number, settingsOverride?: ProjectSettings) => void | Promise<void>
  onCancel?: () => void
  onDub?: () => void
  onExport: () => void
  onUpdateSpeakerProfile: (id: string, patch: { name?: string; color?: string; voice?: string }) => void
}

export function EditorProjectPanel({ tab, segments, settings, voices, busy, jobStep, jobProgress, onSettings, onRunPipeline, onCancel, onDub, onExport, onUpdateSpeakerProfile }: Props) {
  const { locale } = useLocale()
  const t = (vi: string, en: string) => localize(locale, vi, en)
  const [previewSec, setPreviewSec] = React.useState(() => Math.max(5, Number(settings.previewSec) || 30))
  React.useEffect(() => setPreviewSec(Math.max(5, Number(settings.previewSec) || 30)), [settings.previewSec])
  const profiles = React.useMemo(() => resolvedSpeakerProfiles(segments, settings, locale), [segments, settings, locale])

  return <ScrollArea className="h-full scrollbar-hidden"><div className="space-y-3 p-3">
    <div className="border-b border-border pb-1 text-sm text-muted-foreground">{tab === 'workflow' ? t('Quy trình dự án', 'Project workflow') : t('Người nói', 'Speakers')}</div>
    {tab === 'workflow' ? <>
      <p className="text-[11px] leading-snug text-muted-foreground">{t('Thiết lập và chạy toàn bộ pipeline cho dự án này.', 'Configure and run this project’s complete pipeline.')}</p>
      <div className="grid grid-cols-2 gap-2">
        <PropLabel label={t('Nhận dạng', 'Recognition')}><select className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs" value={settings.engine} disabled={busy} onChange={(e) => onSettings({ ...settings, engine: e.target.value as ProjectSettings['engine'] })}><option value="whisper">Whisper</option><option value="paddleocr">OCR</option><option value="subtitle">SRT</option></select></PropLabel>
        <PropLabel label={t('Công cụ dịch', 'Translator')}><select className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs" value={settings.translator} disabled={busy} onChange={(e) => onSettings({ ...settings, translator: e.target.value as ProjectSettings['translator'] })}>{(['google', 'mymemory', 'tiktok', 'ollama', 'openai', 'gemini', 'deepseek', 'openrouter', 'grok', 'nvidia'] as const).map((id) => <option key={id} value={id}>{id}</option>)}</select></PropLabel>
        <PropLabel label={t('Ngôn ngữ gốc', 'Source language')}><select className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs" value={settings.sourceLang} disabled={busy} onChange={(e) => onSettings({ ...settings, sourceLang: e.target.value })}><option value="auto">{t('Tự động', 'Auto')}</option><option value="zh">中文</option><option value="en">English</option><option value="ja">日本語</option><option value="ko">한국어</option><option value="vi">Tiếng Việt</option></select></PropLabel>
        <PropLabel label={t('Dịch sang', 'Translate to')}><select className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs" value={settings.targetLang} disabled={busy} onChange={(e) => onSettings({ ...settings, targetLang: e.target.value })}><option value="vi">Tiếng Việt</option><option value="en">English</option><option value="zh">中文</option><option value="ja">日本語</option><option value="ko">한국어</option><option value="none">{t('Không dịch', 'No translation')}</option></select></PropLabel>
      </div>
      <label className="flex cursor-pointer items-center justify-between gap-2 rounded-md border border-border px-2 py-2 text-xs"><span><b className="block text-foreground">{t('Tách người nói', 'Separate speakers')}</b><span className="text-[10px] text-muted-foreground">{t('Phân vai và dùng giọng riêng.', 'Assign roles and individual voices.')}</span></span><input type="checkbox" className="size-4 accent-primary" checked={Boolean(settings.speakerDiarization)} disabled={busy || settings.engine !== 'whisper'} onChange={(e) => onSettings({ ...settings, speakerDiarization: e.target.checked })} /></label>
      <div className="rounded-md border border-border p-2"><div className="flex items-center justify-between gap-2"><b className="text-xs">{t('Preview', 'Preview')}</b><input type="number" min={5} max={3600} value={previewSec} disabled={busy} className="h-7 w-16 rounded border border-border bg-background px-1.5 text-right text-xs" aria-label={t('Số giây preview', 'Preview seconds')} onChange={(e) => setPreviewSec(Math.max(5, Math.min(3600, Number(e.target.value) || 5)))} /></div><div className="mt-2 grid grid-cols-2 gap-1.5"><button type="button" disabled={busy || !onRunPipeline} className="rounded-md border border-border px-2 py-2 text-xs font-medium hover:bg-accent disabled:opacity-50" onClick={() => { const next = { ...settings, previewSec }; onSettings(next); void onRunPipeline?.(previewSec, next) }}>{t('Chạy preview', 'Run preview')}</button><button type="button" disabled={busy || !onRunPipeline} className="rounded-md bg-primary px-2 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50" onClick={() => void onRunPipeline?.(0, settings)}>{t('Chạy toàn video', 'Run full video')}</button></div>{busy && <div className="mt-2 flex justify-between gap-2 text-[11px] text-muted-foreground"><span className="truncate">{jobStep || t('Đang xử lý', 'Processing')} · {Math.round(jobProgress || 0)}%</span>{onCancel && <button type="button" className="font-medium text-destructive hover:underline" onClick={onCancel}>{t('Hủy', 'Cancel')}</button>}</div>}</div>
      <div className="grid grid-cols-2 gap-1.5"><button type="button" disabled={busy || !segments.length || !onDub} className="rounded-md border border-primary/40 bg-primary/10 px-2 py-2 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50" onClick={() => void onDub?.()}>{settings.speakerDiarization ? t('Tạo TTS theo vai', 'Generate TTS by role') : t('Tạo TTS', 'Generate TTS')}</button><button type="button" disabled={busy} className="rounded-md border border-border px-2 py-2 text-xs font-medium hover:bg-accent disabled:opacity-50" onClick={onExport}>{t('Xuất video', 'Export video')}</button></div>
    </> : profiles.length === 0 ? <p className="py-4 text-center text-[11px] leading-snug text-muted-foreground">{t('Bật Tách người nói trong Quy trình rồi chạy nhận dạng để có dữ liệu vai.', 'Enable speaker separation in Workflow, then run recognition to get roles.')}</p> : <>
      <label className="flex items-center justify-between rounded-md border border-border bg-accent/30 px-2 py-2 text-[11px]"><span><b className="block text-foreground">{t('Màu phụ đề theo vai', 'Caption color by role')}</b><span className="text-muted-foreground">{t('Preview và video xuất', 'Preview and exported video')}</span></span><input type="checkbox" className="size-4 accent-primary" checked={Boolean(settings.speakerCaptionColors)} disabled={busy} onChange={(e) => onSettings({ ...settings, speakerCaptionColors: e.target.checked })} /></label>
      {profiles.map((profile) => { const owned = segments.filter((segment) => segment.speaker === profile.id); const seconds = owned.reduce((sum, segment) => sum + Math.max(0, segment.end - segment.start), 0); return <div key={profile.id} className="space-y-1.5 rounded-md border border-border bg-background p-2" style={{ borderLeft: `4px solid ${profile.color}` }}><div className="flex gap-1.5"><input type="color" className="size-8 shrink-0 rounded border border-border bg-transparent p-0.5" value={profile.color} disabled={busy} aria-label={`${t('Màu', 'Color')} ${profile.name}`} onChange={(e) => onUpdateSpeakerProfile(profile.id, { color: e.target.value })} /><input className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-xs font-medium" value={profile.name} list={`speaker-role-${profile.id}`} disabled={busy} aria-label={`${t('Tên', 'Name')} ${profile.id}`} onChange={(e) => onUpdateSpeakerProfile(profile.id, { name: e.target.value })} /><datalist id={`speaker-role-${profile.id}`}>{speakerRoleOptions(locale).map((role) => <option key={role} value={role} />)}</datalist></div><select className="h-8 w-full rounded-md border border-border bg-background px-2 text-[11px]" value={profile.voice || ''} disabled={busy} onChange={(e) => onUpdateSpeakerProfile(profile.id, { voice: e.target.value })}><option value="">{t('Giọng mặc định', 'Default voice')}</option>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}</option>)}</select><div className="flex justify-between text-[10px] text-muted-foreground"><span>{owned.length} {t('đoạn', 'segments')}</span><span>{formatTimecode(seconds)}</span></div></div> })}
    </>}
  </div></ScrollArea>
}
