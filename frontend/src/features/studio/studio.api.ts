import { fetchJson } from '@/shared/api/fetchJson'

export type QueueJob = {
  id: string
  type: 'clone' | 'review' | string
  mode?: string
  source: string
  status: string
  stage: string
  progress: number
  output?: string
  runId?: string
  projectId?: string | null
  error?: string | null
  errorType?: string | null
  log?: string[]
  outputName?: string
  settings_snapshot?: Record<string, unknown>
  parts?: Array<{
    index: number
    start?: number
    end?: number
    sourceStart?: number
    sourceEnd?: number
    outputDuration?: number | null
    label?: string
    status?: string
    output?: string
  }>
  createdAt?: number
  updatedAt?: number
}

export type QueueSnapshot = {
  jobs: QueueJob[]
  pauseAll: boolean
  diagnostics?: Record<string, unknown>
}

export type ReviewDiagnostics = {
  ollamaModels?: string[]
  llm?: string | null
}

const json = { 'Content-Type': 'application/json' }

export const studioApi = {
  queue: () => fetchJson<QueueSnapshot>('/api/queue', undefined, 15_000),
  enqueue: (type: 'clone' | 'review', sources: string[], settings: Record<string, unknown>, recursive = true) =>
    fetchJson<{ ok: boolean; jobs: QueueJob[] }>('/api/queue', {
      method: 'POST',
      headers: json,
      body: JSON.stringify({ type, sources, settings, recursive }),
    }, 30_000),
  jobAction: (jobId: string, op: string) =>
    fetchJson<QueueSnapshot>(`/api/queue/${jobId}/action`, {
      method: 'POST',
      headers: json,
      body: JSON.stringify({ op }),
    }),
  updateJobSettings: (jobId: string, settings: Record<string, unknown>) =>
    fetchJson<QueueSnapshot>(`/api/queue/${jobId}/settings`, {
      method: 'PATCH',
      headers: json,
      body: JSON.stringify({ settings }),
    }),
  globalAction: (op: string) =>
    fetchJson<QueueSnapshot>('/api/queue/action', {
      method: 'POST',
      headers: json,
      body: JSON.stringify({ op }),
    }),
  generateReview: (body: Record<string, unknown>) =>
    fetchJson<{ ok: boolean; job: QueueJob | null }>('/api/review/generate', {
      method: 'POST',
      headers: json,
      body: JSON.stringify(body),
    }, 30_000),
  reviewStatus: (jobId: string) => fetchJson<QueueJob>(`/api/review/status?jobId=${encodeURIComponent(jobId)}`, undefined, 10_000),
  diagnostics: () => fetchJson<ReviewDiagnostics>('/api/review/diagnostics', undefined, 15_000),
  clearReviewCache: (source: string) =>
    fetchJson<{ ok: boolean; cleared: boolean }>(
      '/api/review/clear-cache',
      { method: 'POST', headers: json, body: JSON.stringify({ source }) },
      30_000,
    ),
  pickVideos: () => fetchJson<{ ok: boolean; paths: string[] }>('/api/system/pick-videos', { method: 'POST' }, 300_000),
  pickFolder: () => fetchJson<{ ok: boolean; path: string }>('/api/system/pick-folder', { method: 'POST' }, 300_000),
  fileUrl: (jobId: string, opts?: { part?: number; download?: boolean }) => {
    const q = new URLSearchParams()
    if (opts?.part != null) q.set('part', String(opts.part))
    if (opts?.download) q.set('download', '1')
    const qs = q.toString()
    return `/api/queue/${jobId}/file${qs ? `?${qs}` : ''}`
  },
  deletePart: (jobId: string, index: number) =>
    fetchJson<QueueSnapshot>(`/api/queue/${jobId}/parts/${index}`, { method: 'DELETE' }),
}
