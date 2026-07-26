/** Logic thuần cho SRT: nhận diện, preview text và style xuất CapCut. */

export type SrtStyle = 'hard' | 'v916' | 'h169' | 'clause' | 'sentence'

export const SRT_STYLE_OPTIONS: { id: SrtStyle; label: string }[] = [
  { id: 'hard', label: 'Cue ngắn (mặc định)' },
  { id: 'v916', label: 'Video dọc 9:16' },
  { id: 'h169', label: 'Video ngang 16:9' },
  { id: 'clause', label: 'Ngắt câu ngắn' },
  { id: 'sentence', label: 'Ngắt câu hợp lý' },
]

/** Heuristic: nội dung clipboard/file có phải SRT không */
export function looksLikeSrt(raw: string): boolean {
  const t = raw.trim()
  if (!t) return false
  if (/\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}/.test(t)) return true
  if (/^\d+\s*\r?\n\d{1,2}:\d{2}/m.test(t) && t.includes('-->')) return true
  return false
}

/** Chỉ giữ dòng thoại (bỏ index + timestamp) để hiện preview text. */
export function srtPreviewLines(raw: string): string {
  return raw
    .split(/\r?\n/)
    .filter((ln) => ln.trim() && !/^\d+$/.test(ln.trim()) && !/-->/.test(ln))
    .join('\n')
}
