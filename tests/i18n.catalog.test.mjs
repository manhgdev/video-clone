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

test('English catalog covers Review Phim and Batch queue', () => {
  const expected = {
    'Review Phim': 'Movie Review',
    'Clone Video và Review Phim': 'Clone Video and Movie Review',
    'Dự án của bạn': 'Your projects',
    'Tạo dự án mới': 'Create new project',
    'Tạo & Chạy': 'Create & Run',
    'Lưu nháp': 'Save draft',
    'Xóa cache': 'Clear cache',
    'Đã xóa cache': 'Cache cleared',
    'Xóa cache nhận dạng và kịch bản của video này. Video nguồn không bao giờ bị xóa.': 'Clear this video’s transcript and script cache. The source video is never deleted.',
    'Lần chạy sau sẽ nhận dạng và viết kịch bản lại.': 'The next run will re-transcribe and rewrite the script.',
    'Hủy': 'Cancel',
    'Đang xóa…': 'Deleting…',
    'Phân đoạn tích lũy': 'Cumulative segments',
    'Độ dài video review (phút)': 'Review length (minutes)',
    'Ngôn ngữ gốc': 'Original language',
    'Ngôn ngữ thoại': 'Spoken language',
    'Ngôn ngữ gốc là tiếng phim (Whisper / phụ đề nhúng). Ngôn ngữ thoại là lời kể TTS và caption.': 'Original language is the film (Whisper / embedded subs). Spoken language is the TTS narration and captions.',
    'Một video duy nhất — không chia thành nhiều phần.': 'One video — not split into parts.',
    'Bộ phong cách lời kể': 'Narration style packs',
    'Tiến độ phân đoạn': 'Segment progress',
    'Tải phần này': 'Download this part',
    'Xem trước': 'Preview',
    'Xoá': 'Delete',
    'Render lại': 'Render again',
    'Hàng loạt': 'Batch',
    'Thêm vào hàng đợi': 'Add to queue',
    'Hàng đợi chung': 'Unified queue',
    'Mỗi tab hiển thị hàng đợi riêng.': 'Each tab shows its own queue.',
    'Hàng đợi Clone hàng loạt': 'Clone batch queue',
    'Hàng đợi Review hàng loạt': 'Review batch queue',
    'Mở Editor': 'Open Editor',
    'Xóa logo / watermark': 'Remove logo / watermark',
    'Tự nhận diện logo/watermark chữ': 'Automatic text-logo detection',
    'Quét mọi nhãn chữ ổn định ở góc video. Veo, Grok và Kling chỉ là ví dụ; logo thuần hình không có chữ cần xử lý thủ công.': 'Scans any stable text label at video edges. Veo, Grok, and Kling are examples; image-only logos need manual treatment.',
  }
  for (const [vietnamese, english] of Object.entries(expected)) {
    assert.equal(catalog[vietnamese], english, vietnamese)
  }
})

test('English catalog covers Live Preview empty page', () => {
  const expected = {
    'Chưa có video để xem trước': 'No video to preview yet',
    'Mở hoặc tải video ở Clone Video rồi quay lại đây để chỉnh sửa theo timeline.': 'Open or upload a video in Clone Video, then return here to edit it on the timeline.',
    'Đi tới Clone Video': 'Go to Clone Video',
  }
  for (const [vietnamese, english] of Object.entries(expected)) {
    assert.equal(catalog[vietnamese], english, vietnamese)
  }
})
