import type {
  AppConfig,
  HardwareInfo,
  JobStatus,
  ProjectSettings,
  RenderedVideo,
  Segment,
  SystemChecks,
  TextOverlay,
} from '@/features/project/project.types'
import { fetchJson } from '@/shared/api/fetchJson'

const base = '/api'

export const api = {
  renders: () => fetchJson<{ items: RenderedVideo[] }>(`${base}/renders`, undefined, 30_000),

  revealRender: (renderId: string) =>
    fetchJson<{ ok: boolean; path: string }>(`${base}/renders/${renderId}/reveal`, { method: 'POST' }),

  renameRender: (renderId: string, name: string) =>
    fetchJson<{ renderId: string; name: string }>(`${base}/renders/${renderId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),

  hardware: () => fetchJson<HardwareInfo>(`${base}/hardware`, undefined, 8000),

  systemChecks: () =>
    fetchJson<SystemChecks>(`${base}/system/checks`, undefined, 20_000),

  installAiRuntime: () =>
    fetchJson<{ ok: boolean; message: string; detail: string }>(
      `${base}/system/install/ai_runtime`,
      { method: 'POST' },
      35 * 60_000,
    ),

  installOcrCuda: () =>
    fetchJson<{ ok: boolean; message: string; detail: string }>(
      `${base}/system/install/ocr_cuda`,
      { method: 'POST' },
      15 * 60_000,
    ),

  installDemucsCuda: () =>
    fetchJson<{ ok: boolean; message: string; detail: string }>(
      `${base}/system/install/demucs_cuda`,
      { method: 'POST' },
      40 * 60_000,
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
    fetchJson<{
      id: string
      name: string
      previewUrl?: string
      type?: string
      engine?: string
      description?: string
      gender?: string
      language?: string
      accent?: string
      age?: string
      style?: string
      category?: string
    }[]>(
      `${base}/voices?lang=${encodeURIComponent(lang)}`,
      undefined,
      15_000,
    ),

  ttsStatus: () =>
    fetchJson<Record<string, {
      id?: string
      name?: string
      local?: boolean
      installed?: boolean
      ready?: boolean
      loaded?: boolean
      loadState?: string
      device?: string
      model?: string
      version?: string
      message?: string
      presetCount?: number
      installHint?: string
      cloneRequiresPytorch?: boolean
    }>>(`${base}/tts/status`, undefined, 15_000),

  ttsStudioSynth: (body: {
    jobId?: string
    text?: string
    srtText?: string
    voice: string
    lang?: string
    speed?: number
    volume?: number
    pitch?: number
    style?: string
    matchDuration?: string
    keepTimeline?: boolean
    autoSplit?: boolean
    gapMs?: number
    title?: string
  }) =>
    fetchJson<{
      id: string
      duration: number
      audioUrl: string
      mp3Url?: string
      srtUrl?: string
      zipUrl?: string
      meta: Record<string, unknown>
      cached?: boolean
    }>(
      `${base}/tts/studio/synthesize`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      10 * 60_000,
    ),

  ttsStudioHistory: () =>
    fetchJson<Array<Record<string, unknown>>>(
      `${base}/tts/studio/history`,
      undefined,
      12_000,
    ),

  ttsStudioDelete: (jobId: string) =>
    fetchJson<{ ok: boolean }>(`${base}/tts/studio/jobs/${jobId}`, {
      method: 'DELETE',
    }),

  ttsStudioCancel: (jobId: string) =>
    fetchJson<{ ok: boolean; cancelled?: boolean }>(
      `${base}/tts/studio/jobs/${jobId}/cancel`,
      { method: 'POST' },
      5000,
    ),

  ttsStudioClone: async (name: string, file: File, transcript = '', tags: string[] = []) => {
    const fd = new FormData()
    fd.append('file', file)
    const q = new URLSearchParams({ name, transcript, tags: JSON.stringify(tags) })
    return fetchJson<{ id: string; name: string; tags: string[] }>(
      `${base}/tts/studio/clone?${q.toString()}`,
      { method: 'POST', body: fd },
      180_000,
    )
  },

  ttsStudioCloneRename: (voiceId: string, name: string) => {
    const id = voiceId.replace(/^vn:clone:/, '')
    return fetchJson<{ id: string; name: string }>(
      `${base}/tts/studio/clone/${encodeURIComponent(id)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      },
    )
  },

  ttsStudioCloneDelete: (voiceId: string) => {
    const id = voiceId.replace(/^vn:clone:/, '')
    return fetchJson<{ ok: boolean }>(
      `${base}/tts/studio/clone/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    )
  },

  /** Đổi metadata / chuyển engine (zmAI ↔ clone) */
  ttsStudioVoicePatch: (
    voiceId: string,
    body: {
      name?: string
      tags?: string[]
      language?: string
      favorite?: boolean
      engine?: 'zmai' | 'clone'
    },
  ) =>
    fetchJson<{
      id: string
      name: string
      tags: string[]
      language?: string
      favorite?: boolean
      engine?: string
      type?: string
    }>(
      `${base}/tts/studio/voices/${encodeURIComponent(voiceId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),

  ttsStudioVoiceReplaceAudio: (voiceId: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetchJson<{ ok: boolean; id: string; name: string }>(
      `${base}/tts/studio/voices/${encodeURIComponent(voiceId)}/audio`,
      { method: 'PUT', body: fd },
      180_000,
    )
  },

  ttsStudioVoicesBulkMove: (voiceIds: string[], target: 'zmai' | 'clone') =>
    fetchJson<{
      target: 'zmai' | 'clone'
      successes: Array<{
        voiceId: string
        voice: { id: string; name: string; engine?: string; type?: string }
      }>
      failures: Array<{ voiceId: string; error: string; errorType: string }>
    }>(
      `${base}/tts/studio/voices/bulk-move`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voiceIds, target }),
      },
    ),

  ttsStudioVoiceDelete: (voiceId: string) =>
    fetchJson<{ ok: boolean }>(
      `${base}/tts/studio/voices/${encodeURIComponent(voiceId)}`,
      { method: 'DELETE' },
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

  replaceSegments: (projectId: string, segments: Segment[]) =>
    fetchJson<Segment[]>(`${base}/projects/${projectId}/segments`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(segments),
    }),

  /** CapCut Alt+G — compound clip (giữ children + mix TTS). */
  createCompound: (projectId: string, segmentIds: string[]) =>
    fetchJson<{
      ok: boolean
      mode: string
      compoundId: string
      mergedId: string
      start: number
      end: number
      childCount: number
      audioFile?: string
      audioUrl?: string
      audioDuration?: number
      segments: Segment[]
    }>(`${base}/projects/${projectId}/segments/compound`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ segmentIds }),
    }, 120_000),

  /** Tháo compound (restore children). */
  uncompound: (projectId: string, segId: string) =>
    fetchJson<{ ok: boolean; segments: Segment[]; restored: number }>(
      `${base}/projects/${projectId}/segments/${encodeURIComponent(segId)}/uncompound`,
      { method: 'POST' },
      60_000,
    ),

  overlays: (projectId: string) =>
    fetchJson<TextOverlay[]>(`${base}/projects/${projectId}/overlays`, undefined, 10_000),

  createOverlay: (projectId: string, overlay: TextOverlay) =>
    fetchJson<TextOverlay>(`${base}/projects/${projectId}/overlays`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(overlay),
    }),

  replaceOverlays: (projectId: string, overlays: TextOverlay[]) =>
    fetchJson<TextOverlay[]>(`${base}/projects/${projectId}/overlays`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(overlays),
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

  uploadLogoAsset: async (projectId: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetchJson<{ url: string; width: number; height: number }>(
      `${base}/projects/${projectId}/logo-asset`,
      { method: 'POST', body: fd },
      30_000,
    )
  },

  run: (projectId: string, settings: ProjectSettings) =>
    fetchJson<{ ok: boolean }>(`${base}/projects/${projectId}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),

  dub: (projectId: string, settings: ProjectSettings & { forceTts?: boolean }) =>
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

  /** Đóng popup lỗi — clear meta.status.error (F5 không hiện lại). */
  dismissStatus: (projectId: string) =>
    fetchJson<{ ok: boolean; ignored?: boolean }>(
      `${base}/projects/${projectId}/status/dismiss`,
      { method: 'POST' },
      5000,
    ),

  export: (projectId: string, settings: ProjectSettings, segments: Segment[] | undefined, exportEndSec: number | undefined, exportStartSec: number | undefined, renderName: string) =>
    fetchJson<{ ok: boolean; url: string; path?: string; exports?: string }>(
      `${base}/projects/${projectId}/export`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...settings, renderName, ...(segments ? { segments } : {}), ...(exportEndSec && exportEndSec > 0 ? { exportEndSec } : {}), ...(exportStartSec && exportStartSec > 0 ? { exportStartSec } : {}) }),
      },
    ),

  revealOutput: (projectId: string) =>
    fetchJson<{ ok: boolean; path: string }>(
      `${base}/projects/${projectId}/reveal-output`,
      { method: 'POST' },
    ),

  /** Cache hit nhanh — không hiện 1% nếu đã có stem. */
  noVocalsStatus: (projectId: string) =>
    fetchJson<{
      ready: boolean
      cached: boolean
      running: boolean
      progress: number
      message: string
      audioUrl: string | null
      file: string | null
    }>(`${base}/projects/${projectId}/audio/no-vocals/status`, undefined, 4000),

  /** Demucs xóa lời — lần đầu (cài torch) có thể rất lâu; tái dùng cache. */
  prepareNoVocals: (projectId: string) =>
    fetchJson<{ audioUrl: string; file: string; cached?: boolean }>(
      `${base}/projects/${projectId}/audio/no-vocals`,
      { method: 'POST' },
      900_000,
    ),

  /** Tiến độ tách stem (poll khi đang prepareNoVocals). */
  noVocalsProgress: (projectId: string) =>
    fetchJson<{
      progress: number
      message: string
      running: boolean
      ready?: boolean
      audioUrl?: string | null
      file?: string | null
    }>(`${base}/projects/${projectId}/audio/no-vocals/progress`, undefined, 4000),

  /** URL tải WAV: original | no_vocals | vocals (theo chế độ đã xử lý). */
  projectAudioDownloadUrl: (
    projectId: string,
    kind: 'original' | 'no_vocals' | 'vocals' = 'original',
  ) => `${base}/projects/${projectId}/audio/download?kind=${encodeURIComponent(kind)}&t=${Date.now()}`,

  /** Bake tốc độ preview toàn bộ + remap timeline. */
  rebakeSpeed: (projectId: string, speed: number, opts?: { skipRemap?: boolean }) =>
    fetchJson<{
      ok?: boolean
      bakedSpeed: number
      bakedPreferVideo: boolean
      workClipSec: number
      duration: number
      /** t_new = t_old * timeScale — scale media clips Video/Âm gốc */
      timeScale?: number
      prevBakedSpeed?: number
      segments: Segment[]
      overlays?: TextOverlay[]
      videoUrl: string
    }>(
      `${base}/projects/${projectId}/rebake-speed`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          speed,
          skipRemap: Boolean(opts?.skipRemap),
        }),
      },
      600_000,
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
