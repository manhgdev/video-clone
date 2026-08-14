import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const catalog = JSON.parse(await readFile(new URL('../frontend/src/app/ui.en.json', import.meta.url), 'utf8'))

test('English catalog covers Configuration labels', () => {
  const expected = {
    'Thiết lập': 'Settings',
    'API dịch': 'Translation API',
    'Sẵn sàng': 'Ready',
    'Kiểm tra lại': 'Check again',
    'Bắt đầu': 'Start',
    '+ Thêm key': '+ Add key',
    'đoạn thoại': 'speech segments',
    'Sẵn sàng render.': 'Ready to render.',
    'Mẹo:': 'Tip:',
    'Bạn có thể dán link kênh, playlist hoặc nhiều link.': 'You can paste a channel link, playlist, or multiple links.',
    'Xóa lời': 'Remove vocals',
    'Xóa lời… đang tách': 'Removing vocals…',
    'Cài Demucs…': 'Installing Demucs…',
    'Cài Demucs CUDA': 'Install Demucs (CUDA)',
    'Đã cài thành công': 'Installed successfully',
  }
  for (const [vietnamese, english] of Object.entries(expected)) {
    assert.equal(catalog[vietnamese], english, vietnamese)
  }
})

test('English catalog has no empty entries', () => {
  const missing = Object.entries(catalog).filter(([, english]) => !String(english).trim())
  assert.deepEqual(missing, [])
})

test('English catalog covers interrupted TTS and Log UI text', () => {
  const expected = [
    'Lỗi job (Dịch / Lồng tiếng / Xuất), warm-models, crash hook. Copy gửi AI để sửa.',
    'Tiêu đề / tên',
    'Chưa có lịch sử — tạo giọng nói để bắt đầu',
    'Hoàn thành',
  ]
  for (const vietnamese of expected) {
    assert.notEqual(catalog[vietnamese], undefined, vietnamese)
    assert.notEqual(catalog[vietnamese], vietnamese, vietnamese)
  }
})
