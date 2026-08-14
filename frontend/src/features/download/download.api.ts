import type { DownloadJob, DownloadOpts, DownloadQuality } from './download.types'
import { fetchJson } from '@/shared/api/fetchJson'

const base = '/api/download'

export type StartBody = {
  urls?: string[]
  url?: string
  quality?: DownloadQuality
} & Partial<DownloadOpts>

export type DownloadRootInfo = {
  path: string
  display: string
  relative: string
  defaultPath?: string
  isDefault?: boolean
}

export const downloadApi = {
  async root(): Promise<DownloadRootInfo> {
    return fetchJson(`${base}/root`, undefined, 8000)
  },

  async setRoot(path: string): Promise<DownloadRootInfo> {
    return fetchJson(
      `${base}/root`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      },
      15_000,
    )
  },

  async resetRoot(): Promise<DownloadRootInfo> {
    return fetchJson(`${base}/root/reset`, { method: 'POST' }, 8000)
  },

  async revealRoot(): Promise<{ ok: boolean; path: string }> {
    return fetchJson(`${base}/root/reveal`, { method: 'POST' }, 8000)
  },

  async pickFolder(): Promise<{ ok: boolean; path: string }> {
    return fetchJson('/api/system/pick-folder', { method: 'POST' }, 300_000)
  },

  async list(): Promise<DownloadJob[]> {
    return fetchJson<DownloadJob[]>(`${base}/jobs`, undefined, 8000)
  },

  async get(id: string): Promise<DownloadJob> {
    return fetchJson<DownloadJob>(`${base}/jobs/${id}`, undefined, 8000)
  },

  async start(body: StartBody): Promise<DownloadJob[]> {
    const res = await fetchJson<DownloadJob | { jobs: DownloadJob[]; count: number }>(
      `${base}/jobs`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      30_000,
    )
    if (res && typeof res === 'object' && 'jobs' in res && Array.isArray(res.jobs)) {
      return res.jobs
    }
    return [res as DownloadJob]
  },

  async cancel(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`${base}/jobs/${id}/cancel`, { method: 'POST' }, 8000)
  },

  async remove(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`${base}/jobs/${id}`, { method: 'DELETE' }, 8000)
  },

  async clearDone(): Promise<{ ok: boolean; removed: number }> {
    return fetchJson(`${base}/jobs/clear-done`, { method: 'POST' }, 8000)
  },

  fileUrl(id: string): string {
    return `${base}/jobs/${id}/file`
  },

  async openFile(id: string): Promise<{ ok: boolean; path: string }> {
    return fetchJson(`${base}/jobs/${id}/open`, { method: 'POST' }, 8000)
  },

  async toProject(id: string): Promise<{
    projectId: string
    videoUrl: string
    duration: number
    cached?: boolean
    segments?: unknown[]
    settings?: Record<string, unknown>
    fromDownload?: string
  }> {
    return fetchJson(`${base}/jobs/${id}/to-project`, { method: 'POST' }, 120_000)
  },
}
