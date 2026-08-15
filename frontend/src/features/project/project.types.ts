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
  /** true khi bbox được ước lượng từ 3 mốc OCR, không phải poly OCR trực tiếp. */
  bboxInherited?: boolean
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
  /** Bake speed lúc fit TTS — playback = ttsSpeed × (bake hiện tại / ttsBake) */
  ttsBake?: number
  /** Cỡ chữ phụ đề riêng đoạn (px); 0 = theo cài đặt dự án / tự động */
  fontSize?: number
  /** Phông chữ riêng đoạn; bỏ trống = theo cài đặt dự án. */
  fontFamily?: string
  /** Màu chữ riêng đoạn #RRGGBB; bỏ trống = theo cài đặt dự án. */
  textColor?: string
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
  /** OpenCut-style: id nhóm clip (kéo/chọn cùng nhau); undefined = không group. */
  groupId?: string
  /** CapCut Alt+G compound shell — children giữ caption+TTS gốc. */
  isCompound?: boolean
  compoundChildren?: Segment[]
}

/** `inpaint` is the high-quality export path used by automatic watermark removal.
 * The editor shows it as a soft blur because browser preview cannot run native inpainting. */
export type OverlayMaskStyle = 'blur' | 'solid' | 'mosaic' | 'inpaint'
export type LogoKeyframe = { at: number; x: number; y: number }

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
  fontFamily?: string
  color: string
  /** text = chữ tự do; effect = vùng hiệu ứng (làm mờ / màu / khối) */
  kind?: 'text' | 'effect' | 'logo'
  /** Kiểu mặt nạ khi kind=effect */
  maskStyle?: OverlayMaskStyle
  maskColor?: string
  /** 0–100 */
  maskOpacity?: number
  /** A detected static watermark promoted to an editable timeline clip. */
  watermarkSource?: string
  logoSource?: 'text' | 'image' | 'icon'
  assetUrl?: string
  iconId?: string
  scope?: 'full' | 'range'
  motion?: 'fixed' | 'random'
  opacity?: number
  visibleSec?: number
  hiddenSec?: number
  fadeSec?: number
  safeMargin?: number
  positionSeed?: number
  positionKeyframes?: LogoKeyframe[]
}

export type ProjectSettings = {
  /** whisper = giọng nói; paddleocr = chữ trên khung; subtitle = file SRT */
  engine: 'whisper' | 'paddleocr' | 'subtitle'
  subtitleSource?: string
  sourceLang: string
  targetLang: string
  /** google | mymemory | tiktok | ollama | openai | gemini | deepseek | openrouter | grok | nvidia */
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
    | 'nvidia'
  /** Ollama local dùng model đã tải; cloud dùng hạn mức tài khoản Ollama. */
  ollamaMode: 'local' | 'cloud'
  ollamaModel: string
  ollamaLocalTier: 'fast' | 'balanced' | 'quality'
  matchDuration: 'natural' | 'stretch' | 'none' | 'preferVideo'
  defaultVoice: string
  /** true khi bấm Lồng tiếng — server xóa cache TTS và gen lại */
  forceTts?: boolean
  /**
   * Bật khung giới hạn vùng định vị (analysisRegion bên dưới).
   * Tên field giữ nguyên để không vỡ cấu hình đã lưu; chế độ OCR
   * «đầu•giữa•cuối» đã bỏ — mọi lần định vị đều dùng 1 frame/mốc.
   */
  stableCaptionLocate: boolean
  /**
   * Vùng phân tích OCR (0–1, theo khung video).
   * Chỉ dùng khi bật khung giới hạn — thu hẹp ROI, nhanh + ít nhiễu.
   */
  analysisRegion?: { x: number; y: number; w: number; h: number } | null
  /** Tự động tìm một logo cố định và xóa bằng native inpainting khi xuất. */
  coverLogo: boolean
  /** Các watermark người dùng chọn không che khi xuất. */
  hiddenLogoTexts?: string[]
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
  /** Phông chữ phụ đề (CSS / tên font hệ thống) */
  subtitleFontFamily?: string
  /** Màu chữ phụ đề #RRGGBB */
  captionTextColor?: string
  /** Nền sau chữ: none | solid | blur | box */
  captionBgStyle?: 'none' | 'solid' | 'blur' | 'box'
  /** Màu nền chữ #RRGGBB */
  captionBgColor?: string
  /** Độ đậm nền 0–100 */
  captionBgOpacity?: number
  /** Viền chữ (outline) */
  captionStroke?: boolean
  /** Bật bộ lọc track âm thanh có sẵn trong video */
  processOriginalAudio: boolean
  /** Chế độ xử lý track âm thanh gốc */
  originalAudioMode: 'original' | 'vocals' | 'no_vocals' | 'mute'
  /** Âm lượng track gốc / nền 0–100 (sau lọc) */
  originalAudioVolume: number
  /** Ô Preview trên sidebar (s) — chỉ dùng khi bấm ▶ Preview, không đổi khi Dịch cả video */
  previewSec: number
  /**
   * Cửa sổ lần chạy pipeline (gửi API): 0 = full, N = preview Ns.
   * Không lưu lâu dài — chỉ payload run.
   */
  runPreviewSec?: number
  /** 1–16 luồng định vị OCR + xuất khung + TTS; 0 = tự động theo tài nguyên rảnh */
  workers: number
  /** Tỷ lệ khung preview / xuất: original | 16:9 | 9:16 | … */
  previewAspectRatio: string
  /** Vùng cắt tự do, tọa độ chuẩn hóa 0–1 theo video gốc. */
  previewCrop?: { x: number; y: number; w: number; h: number } | null
  /** Thu phóng ngang/dọc lớp video trong khung xuất, 1–500%. */
  videoScaleX: number
  videoScaleY: number
  /** Legacy: dữ liệu project tạo trước khi tách hai chiều. */
  videoScale?: number
  /** Độ phân giải cạnh chuẩn khi xuất; original = giữ kích thước sau crop. */
  exportResolution: '144' | '240' | '360' | '480' | '720' | '1080' | '1440' | '2160' | 'original'
  exportVideo?: boolean
  exportVideoFormat?: string
  exportAudio?: boolean
  exportAudioFormat?: string
  exportSrt?: boolean
  exportSrtFormat?: string
  exportGif?: boolean
  exportGifRes?: string
  exportOutputDir?: string
  /**
   * Setting riêng theo engine (Whisper / OCR).
   * matchDuration + lọc âm không dùng chung — đổi nhận dạng nhớ từng bộ.
   */
  engineProfiles?: Partial<
    Record<
      'whisper' | 'paddleocr' | 'subtitle',
      {
        matchDuration?: ProjectSettings['matchDuration']
        processOriginalAudio?: boolean
        originalAudioMode?: ProjectSettings['originalAudioMode']
        originalAudioVolume?: number
      }
    >
  >
}

export type CloudProviderId = 'openai' | 'gemini' | 'deepseek' | 'openrouter' | 'grok' | 'nvidia'

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
  /** Bản desktop đóng gói — file đã local, ẩn «Tải xuống» */
  desktop?: boolean
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
  gpuCount?: number
  hybridGpu?: boolean
  gpus?: Array<{
    index: number
    name: string
    kind: string
    vramMb?: number | null
    driver?: string
    accel: string
    source?: string
  }>
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
  loading?: boolean
  ok: boolean
  platform: string
  python: string
  device?: DeviceInfo
  items: SystemCheckItem[]
  requiredMissing: string[]
  optionalMissing: string[]
  summary: string
  fast?: boolean
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
  /** Watermark detected independently from subtitle / TTS segments. */
  logoDetection?: {
    text?: string
    /** Tọa độ chuẩn hoá theo khung hình nguồn — dùng cho preview cùng mask xuất. */
    tracks?: Array<{
      text?: string
      start?: number
      end?: number
      bbox?: { x: number; y: number; w: number; h: number }
    }>
  }
}

export type RenderedVideo = {
  renderId: string
  projectId: string
  name: string
  createdAt: string
  sizeBytes: number
  duration: number
  width: number
  height: number
  videoUrl: string
  downloadUrl: string
  thumbnailUrl: string
}
