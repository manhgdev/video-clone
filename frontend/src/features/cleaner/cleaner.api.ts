import { fetchJson } from '@/shared/api/fetchJson'
import type { CleanJob, CleanMethod, AdvancedOptions } from '@/pages/VideoCleanerPage'

const base = '/api/cleaner/jobs'

export const cleanerApi = {
  async list(): Promise<CleanJob[]> {
    return fetchJson<CleanJob[]>(base, undefined, 5000)
  },

  async start(files: File[], method: CleanMethod, options: AdvancedOptions): Promise<CleanJob[]> {
    const fd = new FormData()
    files.forEach(f => fd.append('files', f))
    fd.append('method', method)
    fd.append('options', JSON.stringify(options))
    
    // timeout larger since uploading files might take time
    return fetchJson<CleanJob[]>(base, {
      method: 'POST',
      body: fd,
    }, 60000)
  },

  async cancel(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`${base}/${id}/cancel`, { method: 'POST' }, 5000)
  },

  async remove(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`${base}/${id}`, { method: 'DELETE' }, 5000)
  },

  async reveal(id: string): Promise<{ ok: boolean }> {
    return fetchJson(`${base}/${id}/reveal`, { method: 'POST' }, 5000)
  },
}
