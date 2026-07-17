import type { DownloadJob, DownloadQuality } from './download.types'
import { fetchJson } from '@/shared/api/fetchJson'

const base = '/api'

/** Stub API — backend download chưa có; UI sẵn sàng. */
export const downloadApi = {
  async list(): Promise<DownloadJob[]> {
    try {
      return await fetchJson<DownloadJob[]>(`${base}/download/jobs`, undefined, 8000)
    } catch {
      return []
    }
  },

  async start(url: string, quality: DownloadQuality = 'best'): Promise<DownloadJob> {
    return fetchJson<DownloadJob>(
      `${base}/download/jobs`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, quality }),
      },
      30_000,
    )
  },

  async cancel(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`${base}/download/jobs/${id}/cancel`, { method: 'POST' }, 8000)
  },
}
