import type {
  AppConfig,
  HardwareInfo,
  JobStatus,
  ProjectSettings,
  Segment,
  SystemChecks,
  TextOverlay,
} from '../types'

const base = '/api'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.text()
    throw new Error(err || res.statusText)
  }
  return res.json() as Promise<T>
}

/** fetch + timeout — tránh treo khi backend reload */
async function fetchJson<T>(
  url: string,
  init?: RequestInit,
  timeoutMs = 12_000,
): Promise<T> {
  const ac = new AbortController()
  const t = window.setTimeout(() => ac.abort(), timeoutMs)
  try {
    const res = await fetch(url, { ...init, signal: ac.signal })
    return await json<T>(res)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error('API timeout — backend đang reload?')
    }
    throw e
  } finally {
    window.clearTimeout(t)
  }
}

export const api = {
  hardware: () => fetchJson<HardwareInfo>(`${base}/hardware`, undefined, 8000),

  systemChecks: () =>
    fetchJson<SystemChecks>(`${base}/system/checks`, undefined, 20_000),

  installOcrCuda: () =>
    fetchJson<{ ok: boolean; message: string; detail: string }>(
      `${base}/system/install/ocr_cuda`,
      { method: 'POST' },
      15 * 60_000,
    ),

  getConfig: () => fetchJson<AppConfig>(`${base}/config`, undefined, 8000),

  saveConfig: (body: {
    cloud?: Record<
      string,
      { apiKey?: string; baseUrl?: string; model?: string }
    >
    tts?: {
      elevenlabs?: { apiKeys?: string }
    }
  }) =>
    fetchJson<AppConfig>(`${base}/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  voices: (lang = 'vi') =>
    fetchJson<{ id: string; name: string }[]>(
      `${base}/voices?lang=${encodeURIComponent(lang)}`,
      undefined,
      15_000,
    ),

  upload: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetchJson<{
      projectId: string
      videoUrl: string
      duration: number
      cached?: boolean
      segments?: Segment[]
      settings?: Partial<ProjectSettings>
    }>(`${base}/upload`, { method: 'POST', body: fd }, 120_000)
  },

  saveSettings: (projectId: string, settings: ProjectSettings) =>
    fetchJson<{ ok: boolean }>(`${base}/projects/${projectId}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),

  status: (projectId: string) =>
    fetchJson<JobStatus>(`${base}/projects/${projectId}/status`, undefined, 6000),

  segments: (projectId: string) =>
    fetchJson<Segment[]>(`${base}/projects/${projectId}/segments`, undefined, 10_000),

  updateSegment: (projectId: string, seg: Segment) =>
    fetchJson<Segment>(`${base}/projects/${projectId}/segments/${seg.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(seg),
    }),

  overlays: (projectId: string) =>
    fetchJson<TextOverlay[]>(`${base}/projects/${projectId}/overlays`, undefined, 10_000),

  createOverlay: (projectId: string, overlay: TextOverlay) =>
    fetchJson<TextOverlay>(`${base}/projects/${projectId}/overlays`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(overlay),
    }),

  updateOverlay: (projectId: string, overlay: TextOverlay) =>
    fetchJson<TextOverlay>(`${base}/projects/${projectId}/overlays/${overlay.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(overlay),
    }),

  deleteOverlay: (projectId: string, overlayId: string) =>
    fetchJson<{ ok: boolean }>(`${base}/projects/${projectId}/overlays/${overlayId}`, {
      method: 'DELETE',
    }),

  run: (projectId: string, settings: ProjectSettings) =>
    fetchJson<{ ok: boolean }>(`${base}/projects/${projectId}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),

  dub: (projectId: string, settings: ProjectSettings) =>
    fetchJson<{ ok: boolean }>(`${base}/projects/${projectId}/dub`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),

  cancel: (projectId: string) =>
    fetchJson<{ ok: boolean; ignored?: boolean }>(
      `${base}/projects/${projectId}/cancel`,
      { method: 'POST' },
      5000,
    ),

  export: (projectId: string, settings: ProjectSettings, segments?: Segment[]) =>
    fetchJson<{ ok: boolean; url: string; path?: string; exports?: string }>(
      `${base}/projects/${projectId}/export`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(segments ? { ...settings, segments } : settings),
      },
    ),

  revealOutput: (projectId: string) =>
    fetchJson<{ ok: boolean; path: string }>(
      `${base}/projects/${projectId}/reveal-output`,
      { method: 'POST' },
    ),

  previewTts: (
    projectId: string,
    segId: string,
    body: { text: string; voice: string; lang: string },
  ) =>
    fetchJson<{ audioUrl: string; duration: number }>(
      `${base}/projects/${projectId}/segments/${segId}/preview-tts`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      60_000,
    ),

  retranslate: (
    projectId: string,
    segId: string,
    body?: {
      text?: string
      sourceLang?: string
      targetLang?: string
      translator?: string
    },
  ) =>
    fetchJson<{ translation: string; segment: Segment }>(
      `${base}/projects/${projectId}/segments/${segId}/retranslate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      },
      60_000,
    ),
}
