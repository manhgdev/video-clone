/** Format + hằng số hiển thị thuần của TTS Studio. */

export function fmtDur(sec = 0) {
  const s = Math.max(0, Math.floor(sec))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

/** Placeholder / fallback «Nghe thử» theo ngôn ngữ — ô nhập mặc định trống. */
export const PREVIEW_SAMPLES: Record<string, string> = {
  vi: 'Xin chào, đây là giọng thử của Text to Speech Studio.',
  en: 'Hello, this is a sample voice preview from Text to Speech Studio.',
  zh: '你好，这是语音试听示例。',
  ja: 'こんにちは。これは音声プレビューのサンプルです。',
  ko: '안녕하세요. 이것은 음성 미리듣기 샘플입니다.',
  th: 'สวัสดี นี่คือตัวอย่างเสียงทดลองฟัง',
  id: 'Halo, ini adalah contoh pratinjau suara.',
  es: 'Hola, esta es una muestra de vista previa de voz.',
  fr: 'Bonjour, ceci est un aperçu vocal d’exemple.',
  de: 'Hallo, dies ist eine Beispiel-Stimmvorschau.',
  pt: 'Olá, esta é uma amostra de prévia de voz.',
}

export function previewSampleFor(lang: string): string {
  return PREVIEW_SAMPLES[lang] || PREVIEW_SAMPLES.vi
}

export const HISTORY_PAGE_SIZE = 10
export const HISTORY_MAX = 50
