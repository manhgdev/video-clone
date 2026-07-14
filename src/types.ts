export type Step = 'video' | 'asr' | 'translate' | 'dub' | 'export'

export type Segment = {
  id: string
  index: number
  start: number
  end: number
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
}

export type SystemCheckItem = {
  id: string
  name: string
  ok: boolean
  required: boolean
  detail: string
  hint: string
  install: string
}

export type SystemChecks = {
  ok: boolean
  platform: string
  python: string
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
}
