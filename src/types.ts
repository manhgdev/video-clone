export type Step = 'video' | 'asr' | 'translate' | 'dub' | 'export'

export type Segment = {
  id: string
  index: number
  start: number
  end: number
  /** Cửa sổ che chữ gốc (có thể rộng hơn start/end dịch). OCR/gán tay. */
  coverStart?: number
  coverEnd?: number
  /** Vùng che chữ (pixel video nguồn). Mode over: lưu đúng khung cover trên preview. */
  bbox?: { x: number; y: number; w: number; h: number } | null
  /** Layout caption từ preview — export dùng y nguyên, không tính lại. */
  captionLayout?: {
    x: number
    y: number
    w: number
    h: number
    lines: string[]
    fontSize: number
  } | null
  /** Tốc độ hình của đoạn này khi xuất; 1 = giữ nguyên. */
  videoSpeed?: number
  ttsVolume?: number
  ttsSpeed?: number
  /** Cỡ chữ phụ đề riêng đoạn (px); 0 = theo cài đặt dự án / tự động */
  fontSize?: number
  source: string
  translation: string
  voice: string
  audioUrl?: string
  audioFile?: string
  audioDuration?: number
  /** horizontal = hardsub; vertical = title dọc; label = nhãn; mid = flash giữa */
  layout?: 'horizontal' | 'vertical' | 'label' | 'mid'
  /**
   * Lồng tiếng đoạn này. Title dọc / nhãn: mặc định false (chỉ burn chữ).
   * Hardsub: mặc định true (undefined = bật).
   */
  dub?: boolean
}

export type TextOverlay = {
  id: string
  start: number
  end: number
  text: string
  x: number
  y: number
  w: number
  h: number
  fontSize: number
  color: string
}

export type ProjectSettings = {
  /** whisper = giọng nói; paddleocr = chữ trên khung */
  engine: 'whisper' | 'paddleocr'
  sourceLang: string
  targetLang: string
  /** google | mymemory | tiktok | ollama | openai | gemini | deepseek | openrouter | grok */
  translator:
    | 'google'
    | 'mymemory'
    | 'tiktok'
    | 'ollama'
    | 'openai'
    | 'gemini'
    | 'deepseek'
    | 'openrouter'
    | 'grok'
  matchDuration: 'natural' | 'stretch' | 'none' | 'preferVideo'
  defaultVoice: string
  /** Che hardsub cũ (blur). Tắt = giữ chữ OCR trên khung */
  coverHardsubs: boolean
  /** Kiểu mặt nạ che chữ gốc khi cover: blur | solid | mosaic */
  coverMaskStyle: 'blur' | 'solid' | 'mosaic'
  /** Màu phủ (blur tint hoặc nền solid), hex #RRGGBB */
  coverMaskColor: string
  /** Độ mờ/đậm mặt nạ 0–100 */
  coverMaskOpacity: number
  /** Chèn / đè bản dịch lên video khi xuất. Tắt = không vẽ caption */
  burnSubs: boolean
  /** Vị trí caption khi không che: below | above (cover thì căn giữa dải OCR) */
  captionPlacement: 'below' | 'above'
  /** Cỡ chữ bản dịch theo pixel; 0 = tự động theo bbox/độ phân giải */
  subtitleFontSize: number
  /** Bật bộ lọc track âm thanh có sẵn trong video */
  processOriginalAudio: boolean
  /** Chế độ xử lý track âm thanh gốc */
  originalAudioMode: 'original' | 'vocals' | 'no_vocals' | 'mute'
  /** Âm lượng track gốc / nền 0–100 (sau lọc) */
  originalAudioVolume: number
  /** Số giây đầu khi bấm Preview (Dịch toàn bộ vẫn = full) */
  previewSec: number
  /** 1–16 luồng định vị OCR + xuất khung + TTS; 0 = tự động theo tài nguyên rảnh */
  workers: number
  /** Tỷ lệ khung preview / xuất: original | 16:9 | 9:16 | … */
  previewAspectRatio: string
}

export type CloudProviderId = 'openai' | 'gemini' | 'deepseek' | 'openrouter' | 'grok'

export type CloudProviderConfig = {
  apiKey: string
  apiKeySet: boolean
  baseUrl: string
  model: string
  label: string
  env: string
}

export type ElevenLabsConfig = {
  apiKeys: string
  apiKeySet: boolean
  keyCount: number
  label: string
  env: string
}

export type AppConfig = {
  cloud: Record<CloudProviderId, CloudProviderConfig>
  tts?: {
    elevenlabs: ElevenLabsConfig
  }
}

export type HardwareInfo = {
  label: string
  accel: string
  os?: string
  gpuKind?: string
  gpuName?: string
}

export type DeviceInfo = {
  os: 'windows' | 'macos' | 'linux' | 'unknown' | string
  osLabel: string
  arch: string
  appleSilicon: boolean
  gpuKind: 'nvidia' | 'apple' | 'none' | string
  gpuName: string
  vramMb: number | null
  driver: string
  accel: 'cuda' | 'metal' | 'cpu' | string
  label: string
  hasGpu: boolean
  install: {
    ocr: string
    ocrLabel?: string
    demucs: string
    demucsLabel: string
    demucsBackend: string
    summary: string
    hint: string
    actions?: { id: string; label: string }[]
    items?: Record<
      string,
      {
        kind: string
        value: string
        label: string
        hint?: string
        relevant?: boolean
        backend?: string
        name?: string
      }
    >
  }
}

export type SystemCheckItem = {
  id: string
  name: string
  ok: boolean
  required: boolean
  detail: string
  hint: string
  install: string
  installLabel?: string
}

export type SystemChecks = {
  ok: boolean
  platform: string
  python: string
  device?: DeviceInfo
  items: SystemCheckItem[]
  requiredMissing: string[]
  optionalMissing: string[]
  summary: string
}

export type JobStatus = {
  step: Step
  progress: number
  message: string
  running: boolean
  error?: string
  outputRel?: string
  outputPath?: string
  /** Clip lần dịch gần nhất (giây); 0 = full video */
  workClipSec?: number
  duration?: number
  /** preferVideo đã bake chậm 0.80× vào workVideo — preview rate = 1 */
  bakedPreferVideo?: boolean
  /** Tốc độ đã bake vào file preview (1 = chưa bake) */
  bakedSpeed?: number
}
