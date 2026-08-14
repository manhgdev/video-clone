/** Build a static Vietnamese → English UI catalog. Run only during development. */
import { readFile, readdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const roots = [
  fileURLToPath(new URL('../frontend/src/', import.meta.url)),
  fileURLToPath(new URL('../backend/pipeline/core/system_check/', import.meta.url)),
]
const OVERRIDES = {
  'Thiết lập': 'Settings',
  'Cấu hình': 'Settings',
  'API dịch': 'Translation API',
  'Kiểm tra lại': 'Check again',
  'Bắt đầu': 'Start',
  '+ Thêm key': '+ Add key',
  'đoạn thoại': 'speech segments',
  'Sẵn sàng render.': 'Ready to render.',
  'Mẹo:': 'Tip:',
  'Tái mã hóa có thể làm thay đổi nhẹ dung lượng hoặc chất lượng. Hãy giữ file gốc cho đến khi kiểm tra xong kết quả.': 'Re-encoding may slightly change file size or quality. Keep the original file until you have checked the result.',
  'Bạn có thể dán link kênh, playlist hoặc nhiều link.': 'You can paste a channel link, playlist, or multiple links.',
  'Xóa lời': 'Remove vocals',
  'Xóa lời… đang tách': 'Removing vocals…',
  'Xóa lời — lỗi tách (bấm Âm thanh → Thử lại)': 'Vocal removal failed (Audio → Retry)',
  'Tách âm thanh → Xóa lời': 'Extract audio → Remove vocals',
  'Cài Demucs…': 'Installing Demucs…',
  'Cài Demucs.': 'Install Demucs.',
  'Cài Demucs CUDA': 'Install Demucs (CUDA)',
  'Cài Demucs (Apple Metal)': 'Install Demucs (Apple Metal)',
  'Cài Demucs (CPU)': 'Install Demucs (CPU)',
  'Đã cài thành công': 'Installed successfully',
  'Đã cài gói AI': 'AI packages installed',
  'Đã cài OCR GPU': 'GPU OCR installed',
  'Đã cài GPU tăng tốc': 'GPU acceleration installed',
  'Đã cài Demucs': 'Demucs installed',
}
const strings = new Set()
function isUiText(text) {
  return text.length <= 360
    && !/^(#|\/\/|\/\*|\*|"""|''')/.test(text)
    && !/(=>|\b(?:import|from|return|def|class|const|let|setError|setStatus)\b|[{};])/.test(text)
}
async function walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const file = join(dir, entry.name)
    if (entry.isDirectory()) await walk(file)
    else if (/\.(tsx?|jsx?|py)$/.test(entry.name)) {
      const source = await readFile(file, 'utf8')
      for (const pattern of [/'([^'\n]+)'/g, /"([^"\n]+)"/g, /`([^`\n]+)`/g]) {
        for (const match of source.matchAll(pattern)) {
          const text = match[1].trim()
          if (text.length >= 2 && /[^\x00-\x7F]/.test(text) && isUiText(text)) strings.add(text)
        }
      }
      for (const match of source.matchAll(/>([^<>]*[^\x00-\x7F][^<>]*)</g)) {
        const text = match[1].trim()
        if (text && isUiText(text)) strings.add(text)
      }
      if (!file.includes('/backend/')) for (const line of source.split('\n')) {
        const text = line.trim()
        if (text.length >= 2 && /[^\x00-\x7F]/.test(text) && !/[<>]/.test(text) && isUiText(text)) strings.add(text)
      }
    }
  }
}

for (const root of roots) await walk(root)
const source = [...strings].sort()
let existing = {}
try { existing = JSON.parse(await readFile(new URL('../frontend/src/app/ui.en.json', import.meta.url), 'utf8')) } catch { /* first build */ }
const catalog = Object.fromEntries(source.map((text) => [text, existing[text] || '']))
let cursor = 0
async function translate(text) {
  const params = new URLSearchParams({ client: 'gtx', sl: 'vi', tl: 'en', dt: 't', q: text })
  const response = await fetch(`https://translate.googleapis.com/translate_a/single?${params}`)
  if (!response.ok) throw new Error(`translation HTTP ${response.status}`)
  const data = await response.json()
  return data?.[0]?.map((part) => part?.[0] || '').join('').trim() || text
}
async function worker() {
  while (cursor < source.length) {
    const text = source[cursor++]
    if (catalog[text]) continue
    try { catalog[text] = await translate(text) } catch { catalog[text] = text }
  }
}
await Promise.all(Array.from({ length: 8 }, worker))
Object.assign(catalog, OVERRIDES)
await writeFile(new URL('../frontend/src/app/ui.en.json', import.meta.url), JSON.stringify(catalog, null, 2) + '\n')
