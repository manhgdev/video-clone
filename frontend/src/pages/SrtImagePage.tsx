import { useEffect, useRef, useState } from 'react'
import './SrtImagePage.css'

type Job = {
  id: string
  name: string
  status: 'queued' | 'processing' | 'paused' | 'done' | 'error' | 'cancelled'
  progress: number
  error?: string
  logs?: string[]
}

const SETTINGS_KEY = 'videoclone.srt-image.settings.v1'
const HELP = {
  media: ['Thư mục ảnh / video', 'Chọn một thư mục chứa toàn bộ ảnh hoặc clip dùng để dựng video. APP đọc trực tiếp trong thư mục và tự sắp xếp theo tên, không upload/copy từng video.', 'Dùng JPG, JPEG, JFIF, PNG, WEBP, BMP, MP4, MOV, MKV, WEBM, AVI hoặc M4V. Nên đặt tên 001, 002, 003… tương ứng từng dòng timeline.'],
  audio: ['File audio', 'Âm thanh narration chính của video. Audio có sẵn trong các clip đầu vào sẽ bị bỏ để tránh chồng tiếng.', 'Dùng MP3, WAV, M4A hoặc định dạng audio FFmpeg đọc được. Có thể bỏ qua nếu muốn video không có tiếng.'],
  timeline: ['File timeline', 'Quyết định file ảnh/clip nào xuất hiện và xuất hiện trong bao lâu. Mỗi dòng timecode tương ứng một file theo thứ tự tên.', 'Dùng file TXT prompt ảnh/video, ví dụ: 001_[00.00.00.00-00.00.08.00] …'],
  output: ['File xuất', 'Chọn thư mục và tên video MP4 sẽ được lưu sau khi render. Nếu không chọn, APP lưu trong thư mục xuất mặc định.', 'Bấm Chọn để mở hộp thoại Windows. Ví dụ: D:\\Video\\lich-su-loai-nguoi.mp4.'],
  subtitles: ['File phụ đề', 'Chèn chữ phụ đề trực tiếp vào hình ảnh video.', 'Dùng file .SRT có timecode hợp lệ. Đây là file bắt buộc ở chế độ Ghép ảnh/video SRT.'],
  subtitleSize: ['Cỡ chữ', 'Điều chỉnh kích thước chữ phụ đề khi chèn vào video.', 'Giá trị mặc định là 8. Tăng nếu chữ quá nhỏ, giảm nếu chữ chiếm nhiều khung hình.'],
  subtitleOffset: ['Lệch phụ đề', 'Dịch toàn bộ phụ đề sớm hoặc muộn hơn so với audio.', 'Số dương làm phụ đề xuất hiện muộn hơn; số âm làm phụ đề xuất hiện sớm hơn. Đơn vị là giây.'],
  subtitleMargin: ['Lề dưới', 'Điều chỉnh khoảng cách từ phụ đề đến mép dưới video.', 'Giá trị càng lớn thì phụ đề càng được đẩy lên cao. Mặc định là 18.'],
  subtitleBackground: ['Nền chữ', 'Bật nền đen mờ phía sau chữ để phụ đề dễ đọc trên cảnh sáng.', 'Chọn 1 để bật nền chữ, chọn 0 để chỉ hiển thị chữ và viền.'],
  effect: ['Hiệu ứng', 'Chọn cách chuyển từ cảnh hiện tại sang cảnh kế tiếp. Tắt sẽ giữ chuyển cảnh trực tiếp và render nhanh nhất.', 'Mặc định nên để Tắt. Chỉ bật khi muốn video có chuyển cảnh mềm hơn.'],
  transition: ['Thời lượng hiệu ứng', 'Số giây dành cho một lần chuyển cảnh. Giá trị lớn làm hai cảnh hòa vào nhau lâu hơn.', 'Khoảng 0,2–0,5 giây thường tự nhiên; mặc định 0,28 giây.'],
  resolution: ['Độ phân giải', 'Kích thước khung hình video xuất. Auto lấy theo file media đầu tiên.', 'Dùng Auto để giữ khung gốc; chọn 1920×1080 cho video ngang hoặc 1080×1920 cho video dọc.'],
  fps: ['FPS', 'Số khung hình mỗi giây. FPS cao mượt hơn nhưng render chậm và file lớn hơn.', '30 FPS phù hợp hầu hết video; 60 FPS chỉ dùng khi nguồn có chuyển động nhanh.'],
  zoom: ['Zoom', 'Tạo chuyển động phóng nhẹ cho ảnh tĩnh để cảnh bớt đứng yên.', 'Chỉ có ý nghĩa rõ với ảnh; video đầu vào đã có chuyển động nên thường để Tắt.'],
  speed: ['Speed', 'Thay đổi tốc độ cả hình và narration để chúng vẫn khớp nhau.', '100% là tốc độ gốc; 110% nhanh hơn 10%; 80% chậm hơn 20%.'],
  quality: ['Chất lượng', 'Điều khiển mức nén video. Chất lượng cao cho hình đẹp hơn nhưng render lâu và file lớn.', 'Cân bằng phù hợp mặc định; chọn Nhanh khi cần thử hoặc Preview.'],
  volume: ['Âm lượng', 'Điều chỉnh âm lượng file narration chính trong video xuất.', '100% giữ nguyên; 80% giảm nhẹ; trên 100% có thể gây vỡ tiếng.'],
  encoder: ['Encoder', 'Chọn phần cứng dùng để mã hóa video. Tự động ưu tiên GPU khi máy hỗ trợ và chuyển sang CPU khi cần.', 'Nên để Tự động. Chọn CPU khi driver GPU gặp lỗi; chọn GPU để tăng tốc trên máy tương thích.'],
  preview: ['Preview', 'Giới hạn số giây render khi bấm Preview để kiểm tra nhanh trước khi xuất toàn bộ video.', '15 giây thường đủ để kiểm tra tỷ lệ, phụ đề, logo và âm lượng.'],
  metadata: ['Xóa metadata', 'Loại bỏ thông tin phụ như tên encoder và metadata khỏi file MP4 đầu ra.', 'Không ảnh hưởng hình hoặc tiếng. Bật nếu muốn file xuất sạch thông tin kỹ thuật.'],
} as const

type HelpKey = keyof typeof HELP

function cachedSettings(): Record<string, unknown> {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
  } catch {
    return {}
  }
}

export default function SrtImagePage() {
  const cached = useRef(cachedSettings()).current
  const [tab, setTab] = useState<'project' | 'settings'>('project')
  const [helpKey, setHelpKey] = useState<HelpKey | null>(null)
  const [mediaFolder, setMediaFolder] = useState(String(cached.mediaFolder ?? ''))
  const [audioPath, setAudioPath] = useState(String(cached.audioPath ?? ''))
  const [timelinePath, setTimelinePath] = useState(String(cached.timelinePath ?? ''))
  const [srtPath, setSrtPath] = useState(String(cached.srtPath ?? ''))
  const [subtitleSize, setSubtitleSize] = useState(Number(cached.subtitleSize ?? 8))
  const [subtitleOffset, setSubtitleOffset] = useState(Number(cached.subtitleOffset ?? 0))
  const [subtitleMargin, setSubtitleMargin] = useState(Number(cached.subtitleMargin ?? 18))
  const [subtitleBackground, setSubtitleBackground] = useState(Number(cached.subtitleBackground ?? 1))
  const [resolution, setResolution] = useState(String(cached.resolution ?? 'auto'))
  const [fps, setFps] = useState(Number(cached.fps ?? 30))
  const [crf, setCrf] = useState(Number(cached.crf ?? 20))
  const [effect, setEffect] = useState(String(cached.effect ?? 'none'))
  const [transitionDuration, setTransitionDuration] = useState(Number(cached.transitionDuration ?? 0.28))
  const [zoom, setZoom] = useState(String(cached.zoom ?? 'off'))
  const [speed, setSpeed] = useState(Number(cached.speed ?? 100))
  const [volume, setVolume] = useState(Number(cached.volume ?? 100))
  const [previewSeconds, setPreviewSeconds] = useState(Number(cached.previewSeconds ?? 15))
  const [encoder, setEncoder] = useState(String(cached.encoder ?? 'auto'))
  const [removeMetadata, setRemoveMetadata] = useState(Boolean(cached.removeMetadata ?? false))
  const [watermarkPath, setWatermarkPath] = useState(String(cached.watermarkPath ?? ''))
  const [logoEnabled, setLogoEnabled] = useState(Boolean(cached.logoEnabled ?? false))
  const [logoSource, setLogoSource] = useState<'text' | 'image' | 'icon'>(
    cached.logoSource === 'image' || cached.logoSource === 'icon' ? cached.logoSource : 'text',
  )
  const [logoText, setLogoText] = useState(String(
    !cached.logoText || cached.logoText === 'VideoClone' ? 'ZMTOOL' : cached.logoText,
  ))
  const [logoFontSize, setLogoFontSize] = useState(Number(
    cached.logoFontSize == null || cached.logoFontSize === 42 ? 10 : cached.logoFontSize,
  ))
  const [logoColor, setLogoColor] = useState(String(cached.logoColor ?? '#ffffff'))
  const [logoIcon, setLogoIcon] = useState(String(cached.logoIcon ?? '★'))
  const [logoSize, setLogoSize] = useState(Number(cached.logoSize ?? 8))
  const [logoOpacity, setLogoOpacity] = useState(Number(cached.logoOpacity ?? 85))
  const [logoX, setLogoX] = useState(Number(cached.logoX ?? 88))
  const [logoY, setLogoY] = useState(Number(cached.logoY ?? 88))
  const [logoMotion, setLogoMotion] = useState(String(cached.logoMotion ?? 'fixed'))
  const [logoScope, setLogoScope] = useState(String(cached.logoScope ?? 'full'))
  const [logoStart, setLogoStart] = useState(Number(cached.logoStart ?? 0))
  const [logoEnd, setLogoEnd] = useState(Number(cached.logoEnd ?? 10))
  const [logoVisibleSec, setLogoVisibleSec] = useState(Number(cached.logoVisibleSec ?? 4))
  const [logoHiddenSec, setLogoHiddenSec] = useState(Number(cached.logoHiddenSec ?? 2))
  const [logoFadeSec, setLogoFadeSec] = useState(Number(cached.logoFadeSec ?? 0.5))
  const [logoSafeMargin, setLogoSafeMargin] = useState(Number(cached.logoSafeMargin ?? 4))
  const [outputName, setOutputName] = useState(String(cached.outputName ?? 'output.mp4'))
  const [outputPath, setOutputPath] = useState(String(cached.outputPath ?? ''))
  const [job, setJob] = useState<Job | null>(null)
  const [sending, setSending] = useState(false)
  const [logStart, setLogStart] = useState(0)
  const settingsSnapshot = useRef('')

  useEffect(() => {
    if (!job || !['queued', 'processing', 'paused'].includes(job.status)) return
    const timer = window.setInterval(async () => {
      const response = await fetch(`/api/srt-image/jobs/${job.id}`)
      if (response.ok) setJob(await response.json())
    }, 1000)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status])

  useEffect(() => {
    if (!helpKey) return
    const close = (event: KeyboardEvent) => event.key === 'Escape' && setHelpKey(null)
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [helpKey])

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({
      mediaFolder, audioPath, timelinePath, srtPath, watermarkPath, outputName, outputPath,
      resolution, fps, crf, effect, transitionDuration, zoom, speed, volume,
      previewSeconds, encoder, removeMetadata, subtitleSize, subtitleOffset,
      subtitleMargin, subtitleBackground, logoEnabled, logoSource, logoText,
      logoIcon, logoSize, logoFontSize, logoColor, logoOpacity, logoX, logoY,
      logoMotion, logoScope, logoStart, logoEnd, logoVisibleSec, logoHiddenSec,
      logoFadeSec, logoSafeMargin,
    }))
  }, [
    mediaFolder, audioPath, timelinePath, srtPath, watermarkPath, outputName, outputPath,
    resolution, fps, crf, effect, transitionDuration, zoom, speed, volume,
    previewSeconds, encoder, removeMetadata, subtitleSize, subtitleOffset,
    subtitleMargin, subtitleBackground, logoEnabled, logoSource, logoText,
    logoIcon, logoSize, logoFontSize, logoColor, logoOpacity, logoX, logoY,
    logoMotion, logoScope, logoStart, logoEnd, logoVisibleSec, logoHiddenSec,
    logoFadeSec, logoSafeMargin,
  ])

  async function start(preview = false) {
    if (!mediaFolder || !timelinePath || !srtPath) return
    setSending(true)
    try {
      const form = new FormData()
      form.append('media_folder', mediaFolder)
      form.append('timeline_path', timelinePath)
      form.append('srt_path', srtPath)
      if (audioPath) form.append('audio_path', audioPath)
      if (watermarkPath) form.append('watermark_path', watermarkPath)
      form.append('output_name', preview ? `${outputName.replace(/\.mp4$/i, '')}-preview.mp4` : outputName)
      if (outputPath && !preview) form.append('output_path', outputPath)
      form.append('options', JSON.stringify({
        resolution, fps, crf, effect, transitionDuration, zoom, speed, volume,
        encoder, removeMetadata, subtitleSize, subtitleOffset, subtitleMargin,
        subtitleBackground, previewSeconds: preview ? previewSeconds : 0,
        logo: {
          enabled: logoEnabled, source: logoSource, text: logoText, icon: logoIcon,
          size: logoSize, fontSize: logoFontSize, color: logoColor, opacity: logoOpacity,
          x: logoX, y: logoY, motion: logoMotion, scope: logoScope,
          start: logoStart, end: logoEnd, visibleSec: logoVisibleSec,
          hiddenSec: logoHiddenSec, fadeSec: logoFadeSec, safeMargin: logoSafeMargin,
        },
      }))
      const response = await fetch('/api/srt-image/jobs', { method: 'POST', body: form })
      if (!response.ok) throw new Error(await response.text())
      setLogStart(0)
      setJob(await response.json())
    } catch (error) {
      setJob({ id: '', name: outputName, status: 'error', progress: 0, error: String(error) })
    } finally {
      setSending(false)
    }
  }

  async function cancel() {
    if (!job?.id) return
    await fetch(`/api/srt-image/jobs/${job.id}/cancel`, { method: 'POST' })
    setJob({ ...job, status: 'cancelled' })
  }

  async function togglePause() {
    if (!job?.id) return
    const paused = job.status !== 'paused'
    const response = await fetch(`/api/srt-image/jobs/${job.id}/pause?paused=${paused}`, { method: 'POST' })
    if (response.ok) setJob({ ...job, status: paused ? 'paused' : 'processing' })
  }

  async function openFolder() {
    const params = new URLSearchParams()
    if (outputPath) params.set('selected_output', outputPath)
    else if (job?.id) params.set('job_id', job.id)
    const query = params.size ? `?${params}` : ''
    const response = await fetch(`/api/srt-image/open-folder${query}`, { method: 'POST' })
    if (!response.ok) setJob(job ? { ...job, error: await response.text() } : job)
  }

  async function openVideo() {
    if (!job?.id) return
    const response = await fetch(`/api/srt-image/jobs/${job.id}/open`, { method: 'POST' })
    if (!response.ok) setJob({ ...job, error: await response.text() })
  }

  async function chooseMediaFolder() {
    try {
      const response = await fetch('/api/system/pick-media-folder', { method: 'POST' })
      if (!response.ok) throw new Error(await response.text())
      const result = await response.json()
      if (result.ok && result.path) setMediaFolder(String(result.path))
    } catch (error) {
      setJob({ id: '', name: outputName, status: 'error', progress: 0, error: `Không chọn được thư mục: ${String(error)}` })
    }
  }

  async function chooseInputFile(kind: 'audio' | 'timeline' | 'srt' | 'watermark') {
    try {
      const response = await fetch(`/api/system/pick-srt-image-file?kind=${kind}`, { method: 'POST' })
      if (!response.ok) throw new Error(await response.text())
      const result = await response.json()
      if (!result.ok || !result.path) return
      const path = String(result.path)
      if (kind === 'audio') setAudioPath(path)
      else if (kind === 'timeline') setTimelinePath(path)
      else if (kind === 'srt') setSrtPath(path)
      else setWatermarkPath(path)
    } catch (error) {
      setJob({ id: '', name: outputName, status: 'error', progress: 0, error: `Không chọn được file: ${String(error)}` })
    }
  }

  async function chooseOutput() {
    try {
      const response = await fetch(`/api/system/pick-save-video?filename=${encodeURIComponent(outputName)}`, { method: 'POST' })
      if (!response.ok) throw new Error(await response.text())
      const result = await response.json()
      if (!result.ok || !result.path) return
      const path = String(result.path)
      setOutputPath(path)
      setOutputName(path.split(/[\\/]/).pop() || 'output.mp4')
    } catch (error) {
      setJob({ id: '', name: outputName, status: 'error', progress: 0, error: `Không chọn được file xuất: ${String(error)}` })
    }
  }

  function renameOutput(value: string) {
    const name = `${value.replace(/\.mp4$/i, '')}.mp4`
    setOutputName(name)
    if (outputPath) {
      const slash = Math.max(outputPath.lastIndexOf('\\'), outputPath.lastIndexOf('/'))
      setOutputPath(`${outputPath.slice(0, slash + 1)}${name}`)
    }
  }

  const busy = sending || job?.status === 'queued' || job?.status === 'processing' || job?.status === 'paused'
  const statusText = job?.status === 'done'
    ? 'Hoàn thành'
    : job?.status === 'error'
      ? 'Render thất bại'
      : job?.status === 'paused'
        ? 'Đang tạm dừng'
      : job?.status === 'cancelled'
        ? 'Đã hủy'
        : busy ? 'Đang render…' : 'Sẵn sàng render'
  const outputSlash = Math.max(outputPath.lastIndexOf('\\'), outputPath.lastIndexOf('/'))
  const outputDirectory = outputPath ? outputPath.slice(0, outputSlash + 1) : 'Thư mục mặc định\\'
  const visibleLogs = (job?.logs || []).slice(logStart)
  const logText = visibleLogs.length
    ? visibleLogs.join('\n')
    : `[${new Date().toLocaleTimeString('vi-VN')}] ${job?.error || statusText}`
  const logRef = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const node = logRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [logText])

  function openSettings() {
    if (tab === 'settings') return
    settingsSnapshot.current = JSON.stringify({
      resolution, fps, crf, effect, transitionDuration, zoom, speed, volume,
      previewSeconds, encoder, removeMetadata, watermarkPath, logoEnabled,
      logoSource, logoText, logoIcon, logoSize, logoFontSize, logoColor,
      logoOpacity, logoX, logoY, logoMotion, logoScope, logoStart, logoEnd,
      logoVisibleSec, logoHiddenSec, logoFadeSec, logoSafeMargin,
    })
    setTab('settings')
  }

  function cancelSettings() {
    const value = JSON.parse(settingsSnapshot.current || '{}')
    setResolution(value.resolution ?? resolution)
    setFps(value.fps ?? fps)
    setCrf(value.crf ?? crf)
    setEffect(value.effect ?? effect)
    setTransitionDuration(value.transitionDuration ?? transitionDuration)
    setZoom(value.zoom ?? zoom)
    setSpeed(value.speed ?? speed)
    setVolume(value.volume ?? volume)
    setPreviewSeconds(value.previewSeconds ?? previewSeconds)
    setEncoder(value.encoder ?? encoder)
    setRemoveMetadata(value.removeMetadata ?? removeMetadata)
    setWatermarkPath(value.watermarkPath ?? watermarkPath)
    setLogoEnabled(value.logoEnabled ?? logoEnabled)
    setLogoSource(value.logoSource ?? logoSource)
    setLogoText(value.logoText ?? logoText)
    setLogoIcon(value.logoIcon ?? logoIcon)
    setLogoSize(value.logoSize ?? logoSize)
    setLogoFontSize(value.logoFontSize ?? logoFontSize)
    setLogoColor(value.logoColor ?? logoColor)
    setLogoOpacity(value.logoOpacity ?? logoOpacity)
    setLogoX(value.logoX ?? logoX)
    setLogoY(value.logoY ?? logoY)
    setLogoMotion(value.logoMotion ?? logoMotion)
    setLogoScope(value.logoScope ?? logoScope)
    setLogoStart(value.logoStart ?? logoStart)
    setLogoEnd(value.logoEnd ?? logoEnd)
    setLogoVisibleSec(value.logoVisibleSec ?? logoVisibleSec)
    setLogoHiddenSec(value.logoHiddenSec ?? logoHiddenSec)
    setLogoFadeSec(value.logoFadeSec ?? logoFadeSec)
    setLogoSafeMargin(value.logoSafeMargin ?? logoSafeMargin)
    setTab('project')
  }

  return (
    <main className="siv-page">
      <header>
        <div>
          <h1>Ghép ảnh/video SRT</h1>
          <p>Ghép ảnh hoặc clip theo timeline, kèm audio narration và phụ đề SRT.</p>
        </div>
      </header>

      <section className="siv-workspace">
        <nav className="siv-tabs" aria-label="Thiết lập Ghép ảnh SRT">
          <button className={tab === 'project' ? 'active' : ''} onClick={() => setTab('project')}>Dự án</button>
          <button className={tab === 'settings' ? 'active' : ''} onClick={openSettings}>Cài đặt</button>
        </nav>

        <div className="siv-panel">
          {tab === 'project' ? (
            <div className="siv-form">
              <div className="siv-row">
                <label>Thư mục media <button type="button" className="siv-info" onClick={() => setHelpKey('media')}>i</button></label>
                <div className="siv-input"><span>{mediaFolder || 'Chưa chọn thư mục ảnh/video'}</span></div>
                <button onClick={chooseMediaFolder}>Chọn</button>
                <button onClick={() => setMediaFolder('')} disabled={!mediaFolder}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File audio <button type="button" className="siv-info" onClick={() => setHelpKey('audio')}>i</button></label>
                <div className="siv-input"><span title={audioPath}>{audioPath || 'Không dùng audio'}</span></div>
                <button onClick={() => chooseInputFile('audio')}>Chọn</button>
                <button onClick={() => setAudioPath('')} disabled={!audioPath}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File timeline <button type="button" className="siv-info" onClick={() => setHelpKey('timeline')}>i</button></label>
                <div className="siv-input"><span title={timelinePath}>{timelinePath || 'Chưa chọn file timeline (.txt)'}</span></div>
                <button onClick={() => chooseInputFile('timeline')}>Chọn</button>
                <button onClick={() => setTimelinePath('')} disabled={!timelinePath}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File xuất <button type="button" className="siv-info" onClick={() => setHelpKey('output')}>i</button></label>
                <div className="siv-input siv-output-path"><span title={outputDirectory}>{outputDirectory}</span><input value={outputName.replace(/\.mp4$/i, '')} onChange={(e) => renameOutput(e.target.value)} /><b>.mp4</b></div>
                <button onClick={chooseOutput}>Chọn</button>
                <button onClick={() => { setOutputPath(''); setOutputName('output.mp4') }}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File phụ đề <button type="button" className="siv-info" onClick={() => setHelpKey('subtitles')}>i</button></label>
                <div className="siv-input"><span title={srtPath}>{srtPath || 'Chưa chọn phụ đề SRT'}</span></div>
                <button onClick={() => chooseInputFile('srt')}>Chọn</button>
                <button onClick={() => setSrtPath('')} disabled={!srtPath}>Xóa</button>
              </div>
              {srtPath && (
                <div className="siv-subtitle-options">
                  <label>
                    <span className="siv-subtitle-title">Cỡ chữ <button type="button" className="siv-info" onClick={() => setHelpKey('subtitleSize')}>i</button></span>
                    <input type="number" min="6" max="120" value={subtitleSize} onChange={(e) => setSubtitleSize(Number(e.target.value))} />
                  </label>
                  <label>
                    <span className="siv-subtitle-title">Lệch (s) <button type="button" className="siv-info" onClick={() => setHelpKey('subtitleOffset')}>i</button></span>
                    <input type="number" min="-3600" max="3600" step=".1" value={subtitleOffset} onChange={(e) => setSubtitleOffset(Number(e.target.value))} />
                  </label>
                  <label>
                    <span className="siv-subtitle-title">Lề dưới <button type="button" className="siv-info" onClick={() => setHelpKey('subtitleMargin')}>i</button></span>
                    <input type="number" min="0" max="1000" value={subtitleMargin} onChange={(e) => setSubtitleMargin(Number(e.target.value))} />
                  </label>
                  <label>
                    <span className="siv-subtitle-title">Nền chữ <button type="button" className="siv-info" onClick={() => setHelpKey('subtitleBackground')}>i</button></span>
                    <select value={subtitleBackground} onChange={(e) => setSubtitleBackground(Number(e.target.value))}>
                      <option value="1">1 — Bật</option>
                      <option value="0">0 — Tắt</option>
                    </select>
                  </label>
                </div>
              )}
              <p className="siv-hint">Timeline quyết định thời lượng từng ảnh/clip; SRT chỉ dùng để chèn phụ đề.</p>
            </div>
          ) : (
            <div className="siv-settings">
              <label><span className="siv-setting-title">Hiệu ứng <button type="button" className="siv-info" onClick={() => setHelpKey('effect')}>i</button></span>
                <select value={effect} onChange={(e) => setEffect(e.target.value)}>
                  <option value="random">Ngẫu nhiên</option><option value="fade">Fade</option>
                  <option value="dissolve">Dissolve</option><option value="none">Tắt</option>
                </select>
              </label>
              <label><span className="siv-setting-title">Thời lượng (s) <button type="button" className="siv-info" onClick={() => setHelpKey('transition')}>i</button></span>
                <input type="number" min=".1" max="2" step=".01" value={transitionDuration} onChange={(e) => setTransitionDuration(Number(e.target.value))} />
              </label>
              <label><span className="siv-setting-title">Độ phân giải <button type="button" className="siv-info" onClick={() => setHelpKey('resolution')}>i</button></span>
                <select value={resolution} onChange={(e) => setResolution(e.target.value)}>
                  <option value="auto">Auto (theo ảnh)</option>
                  <option value="1920x1080">1920 × 1080 (16:9)</option>
                  <option value="1080x1920">1080 × 1920 (9:16)</option>
                  <option value="1080x1080">1080 × 1080 (1:1)</option>
                  <option value="1280x720">1280 × 720</option>
                </select>
              </label>
              <label><span className="siv-setting-title">FPS <button type="button" className="siv-info" onClick={() => setHelpKey('fps')}>i</button></span>
                <select value={fps} onChange={(e) => setFps(Number(e.target.value))}>
                  <option>24</option><option>25</option><option>30</option><option>60</option>
                </select>
              </label>
              <label><span className="siv-setting-title">Zoom <button type="button" className="siv-info" onClick={() => setHelpKey('zoom')}>i</button></span>
                <select value={zoom} onChange={(e) => setZoom(e.target.value)}>
                    <option value="off">Tắt</option>
                    <option value="random">Ngẫu nhiên</option>
                    <option value="zoomIn">Zoom in</option>
                    <option value="zoomOut">Zoom out</option>
                    <option value="left">Trái → phải</option>
                    <option value="right">Phải → trái</option>
                    <option value="up">Dưới → trên</option>
                    <option value="down">Trên → dưới</option>
                </select>
              </label>
              <label><span className="siv-setting-title">Speed (%) <button type="button" className="siv-info" onClick={() => setHelpKey('speed')}>i</button></span>
                <input type="number" min="25" max="400" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
              </label>
              <label><span className="siv-setting-title">Chất lượng <button type="button" className="siv-info" onClick={() => setHelpKey('quality')}>i</button></span>
                <select value={crf} onChange={(e) => setCrf(Number(e.target.value))}>
                  <option value="18">Cao</option><option value="20">Cân bằng</option><option value="24">Nhanh</option>
                </select>
              </label>
              <label><span className="siv-setting-title">Âm lượng (%) <button type="button" className="siv-info" onClick={() => setHelpKey('volume')}>i</button></span>
                <input type="number" min="0" max="300" value={volume} onChange={(e) => setVolume(Number(e.target.value))} />
              </label>
              <label><span className="siv-setting-title">Encoder <button type="button" className="siv-info" onClick={() => setHelpKey('encoder')}>i</button></span>
                <select value={encoder} onChange={(e) => setEncoder(e.target.value)}>
                  <option value="auto">Tự động</option><option value="gpu">GPU</option><option value="cpu">CPU</option>
                </select>
              </label>
              <label><span className="siv-setting-title">Preview (s) <button type="button" className="siv-info" onClick={() => setHelpKey('preview')}>i</button></span>
                <input type="number" min="1" max="120" value={previewSeconds} onChange={(e) => setPreviewSeconds(Number(e.target.value))} />
              </label>
              <label><span className="siv-setting-title">Xóa metadata <button type="button" className="siv-info" onClick={() => setHelpKey('metadata')}>i</button></span>
                <select value={removeMetadata ? 'on' : 'off'} onChange={(e) => setRemoveMetadata(e.target.value === 'on')}>
                  <option value="off">Tắt</option><option value="on">Bật</option>
                </select>
              </label>
              <div className="siv-logo">
                <div className="siv-logo-head"><strong>Logo / Watermark VideoClone</strong><label><input type="checkbox" checked={logoEnabled} onChange={(e) => setLogoEnabled(e.target.checked)} /> Áp dụng</label></div>
                <div className="siv-logo-sources">
                  {(['text', 'image', 'icon'] as const).map((source) => <button key={source} className={logoSource === source ? 'active' : ''} onClick={() => setLogoSource(source)}>{source === 'text' ? 'T  Chữ' : source === 'image' ? '▧  Ảnh' : '★  Icon'}</button>)}
                </div>
                {logoSource === 'text' && <><label>Nội dung<input value={logoText} onChange={(e) => setLogoText(e.target.value)} /></label><label>Màu chữ<input type="color" value={logoColor} onChange={(e) => setLogoColor(e.target.value)} /></label></>}
                {logoSource === 'image' && <div className="siv-logo-file"><span title={watermarkPath}>{watermarkPath || 'Chưa chọn ảnh logo'}</span><button className="siv-choose" onClick={() => chooseInputFile('watermark')}>Chọn ảnh</button></div>}
                {logoSource === 'icon' && <label>Icon<select value={logoIcon} onChange={(e) => setLogoIcon(e.target.value)}><option>★</option><option>▶</option><option>●</option><option>◆</option></select></label>}
                {logoSource === 'text'
                  ? <label>Cỡ chữ: {logoFontSize}px<input type="range" min="6" max="160" value={logoFontSize} onChange={(e) => setLogoFontSize(Number(e.target.value))} /></label>
                  : <label>Kích thước: {logoSize}%<input type="range" min="2" max="30" value={logoSize} onChange={(e) => setLogoSize(Number(e.target.value))} /></label>}
                <label>Độ mờ: {logoOpacity}%<input type="range" min="5" max="100" value={logoOpacity} onChange={(e) => setLogoOpacity(Number(e.target.value))} /></label>
                <div className="siv-logo-range"><label>X (%)<input type="number" min="0" max="100" value={logoX} onChange={(e) => setLogoX(Number(e.target.value))} /></label><label>Y (%)<input type="number" min="0" max="100" value={logoY} onChange={(e) => setLogoY(Number(e.target.value))} /></label></div>
                <label>Chuyển động<select value={logoMotion} onChange={(e) => setLogoMotion(e.target.value)}><option value="fixed">Cố định</option><option value="random">Ngẫu nhiên</option></select></label>
                <label>Phạm vi<select value={logoScope} onChange={(e) => setLogoScope(e.target.value)}><option value="full">Toàn video</option><option value="range">Theo đoạn</option></select></label>
                {logoMotion === 'random' && <div className="siv-logo-motion"><label>Hiện (s)<input type="number" min="0.5" step="0.1" value={logoVisibleSec} onChange={(e) => setLogoVisibleSec(Number(e.target.value))} /></label><label>Ẩn (s)<input type="number" min="0" step="0.1" value={logoHiddenSec} onChange={(e) => setLogoHiddenSec(Number(e.target.value))} /></label><label>Fade (s)<input type="number" min="0" step="0.1" value={logoFadeSec} onChange={(e) => setLogoFadeSec(Number(e.target.value))} /></label><label>Lề (%)<input type="number" min="0" max="20" value={logoSafeMargin} onChange={(e) => setLogoSafeMargin(Number(e.target.value))} /></label></div>}
                {logoScope === 'range' && <div className="siv-logo-range"><label>Hiện từ<input type="number" min="0" value={logoStart} onChange={(e) => setLogoStart(Number(e.target.value))} /></label><label>Đến<input type="number" min="0" value={logoEnd} onChange={(e) => setLogoEnd(Number(e.target.value))} /></label></div>}
              </div>
            </div>
          )}
        </div>

        {tab === 'project' ? (
          <>
            <div className="siv-progress">
              <span>Tiến độ</span>
              <progress max="100" value={job?.progress || 0} />
              <b>{Math.round(job?.progress || 0)}%</b>
            </div>
            <div className="siv-log">
              <header>
                <strong>Log chi tiết</strong>
                <div>
                  <button type="button" onClick={() => navigator.clipboard.writeText(logText)}>Copy</button>
                  <button type="button" onClick={() => setLogStart(job?.logs?.length || 0)}>Xóa</button>
                </div>
              </header>
              <pre ref={logRef}>{logText}</pre>
            </div>
            <footer className="siv-actions">
              <button className="primary" disabled={busy || !mediaFolder || !timelinePath || !srtPath} onClick={() => start(false)}>
                {sending ? 'ĐANG TẢI…' : 'RENDER'}
              </button>
              <button disabled={busy || !mediaFolder || !timelinePath || !srtPath} onClick={() => start(true)}>Preview</button>
              <button disabled={!job || !['processing', 'paused'].includes(job.status)} onClick={togglePause}>
                {job?.status === 'paused' ? 'Tiếp tục' : 'Tạm dừng'}
              </button>
              <button disabled={!busy} onClick={cancel}>Hủy</button>
              <button disabled={job?.status !== 'done'} onClick={openVideo}>Mở video</button>
              <button onClick={openFolder}>Thư mục</button>
              <span>{statusText}</span>
            </footer>
          </>
        ) : (
          <footer className="siv-actions">
            <button className="primary" onClick={() => setTab('project')}>Lưu</button>
            <button onClick={cancelSettings}>Hủy</button>
          </footer>
        )}
      </section>
      {helpKey && (
        <div className="siv-help-backdrop" role="presentation" onMouseDown={() => setHelpKey(null)}>
          <section className="siv-help-dialog" role="dialog" aria-modal="true" aria-labelledby="siv-help-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <small>Hướng dẫn sử dụng</small>
                <h2 id="siv-help-title">{HELP[helpKey][0]}</h2>
              </div>
              <button type="button" aria-label="Đóng hướng dẫn" onClick={() => setHelpKey(null)}>×</button>
            </header>
            <p>{HELP[helpKey][1]}</p>
            <div><strong>File hoặc thiết lập cần dùng</strong><p>{HELP[helpKey][2]}</p></div>
            <button type="button" className="siv-help-close" onClick={() => setHelpKey(null)}>Đã hiểu</button>
          </section>
        </div>
      )}
    </main>
  )
}
