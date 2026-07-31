import { useEffect, useRef, useState } from 'react'
import './SrtImagePage.css'
import { CAPTION_FONT_PRESETS, captionChromeStyle, captionFontCss } from '../features/editor/lib/previewStyles'

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
  subtitleFontFamily: ['Phông chữ', 'Kiểu chữ (font) dùng cho phụ đề.', 'Nên chọn các font không chân (sans-serif) nét rõ như Noto Sans, Inter, Roboto để dễ đọc trên mọi thiết bị.'],
  subtitleSize: ['Cỡ chữ', 'Điều chỉnh kích thước chữ phụ đề khi chèn vào video.', 'Giá trị mặc định là 8. Tăng nếu chữ quá nhỏ, giảm nếu chữ chiếm nhiều khung hình.'],
  subtitleOffset: ['Lệch phụ đề', 'Dịch toàn bộ phụ đề sớm hoặc muộn hơn so với audio.', 'Số dương làm phụ đề xuất hiện muộn hơn; số âm làm phụ đề xuất hiện sớm hơn. Đơn vị là giây.'],
  subtitleMargin: ['Lề dưới', 'Điều chỉnh khoảng cách từ phụ đề đến mép dưới video.', 'Giá trị càng lớn thì phụ đề càng được đẩy lên cao. Mặc định là 18.'],
  subtitleBackground: ['Nền chữ', 'Kiểu nền phía sau chữ phụ đề, giống tùy chọn nền bản dịch bên Clone Video.', 'Tắt: chỉ viền · Đặc: nền đen mờ · Hộp: nền bo góc viền trắng.'],
  subtitleColor: ['Màu chữ', 'Màu sắc chính của chữ phụ đề.', 'Thường nên để màu trắng (#FFFFFF) hoặc vàng nhạt để tương phản tốt với các nền tối.'],
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
  delogo: ['Xóa logo gốc', 'Xóa watermark AI (Veo 3, Grok…) bằng bộ lọc delogo của FFmpeg trên frame gốc trước khi scale.', 'Vùng xóa mặc định ở góc dưới phải. Tắt Tự định vị để kéo tay chọn vùng trên Preview.'],
  delogoAuto: ['Tự định vị', 'Tự động đặt vùng xóa logo ở góc dưới phải — vị trí mặc định của Veo 3, Grok.', 'Tắt để kéo chuột vẽ vùng xóa tùy ý trên Preview trực tiếp.'],
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
  const [subtitleFontFamily, setSubtitleFontFamily] = useState(String(cached.subtitleFontFamily ?? 'system'))
  const [subtitleMargin, setSubtitleMargin] = useState(Number(cached.subtitleMargin ?? 18))
  const [subtitleBackground, setSubtitleBackground] = useState(String(cached.subtitleBackground ?? 'solid'))
  const [subtitleColor, setSubtitleColor] = useState(String(cached.subtitleColor ?? '#ffffff'))
  const [subtitleBgColor, setSubtitleBgColor] = useState(String(cached.subtitleBgColor ?? '#000000'))
  const [subtitleOpacity, setSubtitleOpacity] = useState(Number(cached.subtitleOpacity ?? 55))
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
  const [delogoEnabled, setDelogoEnabled] = useState(Boolean(cached.delogoEnabled ?? false))
  const [delogoAuto, setDelogoAuto] = useState(Boolean(cached.delogoAuto ?? true))
  // ponytail: vùng xóa logo = % frame (0–100), mặc định góc dưới phải cho Veo3/Grok
  const [delogoRect, setDelogoRect] = useState(
    (cached.delogoRect as { x: number; y: number; w: number; h: number } | undefined) ?? { x: 82, y: 94, w: 16, h: 4 }
  )
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
      previewSeconds, encoder, removeMetadata, delogoEnabled, delogoAuto, delogoRect,
      subtitleSize, subtitleOffset, subtitleFontFamily,
      subtitleMargin, subtitleBackground, subtitleColor, subtitleBgColor, subtitleOpacity, logoEnabled, logoSource, logoText,
      logoIcon, logoSize, logoFontSize, logoColor, logoOpacity, logoX, logoY,
      logoMotion, logoScope, logoStart, logoEnd, logoVisibleSec, logoHiddenSec,
      logoFadeSec, logoSafeMargin,
    }))
  }, [
    mediaFolder, audioPath, timelinePath, srtPath, watermarkPath, outputName, outputPath,
    resolution, fps, crf, effect, transitionDuration, zoom, speed, volume,
    previewSeconds, encoder, removeMetadata, delogoEnabled, delogoAuto, delogoRect,
    subtitleSize, subtitleOffset, subtitleFontFamily,
    subtitleMargin, subtitleBackground, subtitleColor, subtitleBgColor, subtitleOpacity, logoEnabled, logoSource, logoText,
    logoIcon, logoSize, logoFontSize, logoColor, logoOpacity, logoX, logoY,
    logoMotion, logoScope, logoStart, logoEnd, logoVisibleSec, logoHiddenSec,
    logoFadeSec, logoSafeMargin,
  ])

  async function start(preview = false) {
    if (!mediaFolder || !timelinePath) return
    setSending(true)
    try {
      const form = new FormData()
      form.append('media_folder', mediaFolder)
      form.append('timeline_path', timelinePath)
      if (srtPath) form.append('srt_path', srtPath)
      if (audioPath) form.append('audio_path', audioPath)
      if (watermarkPath) form.append('watermark_path', watermarkPath)
      form.append('output_name', preview ? `${outputName.replace(/\.mp4$/i, '')}-preview.mp4` : outputName)
      if (outputPath && !preview) form.append('output_path', outputPath)
      form.append('options', JSON.stringify({
        resolution, fps, crf, effect, transitionDuration, zoom, speed, volume,
        encoder, removeMetadata, subtitleSize, subtitleOffset, subtitleFontFamily, subtitleMargin,
        subtitleBackground, subtitleColor, subtitleBgColor, subtitleOpacity, previewSeconds: preview ? previewSeconds : 0,
        delogo: { enabled: delogoEnabled, ...delogoRect },
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

      <div className="siv-layout">
        <div className="siv-main-pane">
          <section className="siv-workspace">
        <nav className="siv-tabs" aria-label="Thiết lập Ghép ảnh SRT">
          <button className={tab === 'project' ? 'active' : ''} onClick={() => setTab('project')}>Dự án</button>
          <button className={tab === 'settings' ? 'active' : ''} onClick={openSettings}>Cài đặt</button>
        </nav>

        <div className="siv-panel">
          {tab === 'project' ? (
            <div className="siv-form">
              <div className="siv-row">
                <label>Thư mục media <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('media') }}>i</span></label>
                <div className="siv-input"><span>{mediaFolder || 'Chưa chọn thư mục ảnh/video'}</span></div>
                <button onClick={chooseMediaFolder}>Chọn</button>
                <button onClick={() => setMediaFolder('')} disabled={!mediaFolder}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File audio <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('audio') }}>i</span></label>
                <div className="siv-input"><span title={audioPath}>{audioPath || 'Không dùng audio'}</span></div>
                <button onClick={() => chooseInputFile('audio')}>Chọn</button>
                <button onClick={() => setAudioPath('')} disabled={!audioPath}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File timeline <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('timeline') }}>i</span></label>
                <div className="siv-input"><span title={timelinePath}>{timelinePath || 'Chưa chọn file timeline (.txt)'}</span></div>
                <button onClick={() => chooseInputFile('timeline')}>Chọn</button>
                <button onClick={() => setTimelinePath('')} disabled={!timelinePath}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File xuất <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('output') }}>i</span></label>
                <div className="siv-input siv-output-path"><span title={outputDirectory}>{outputDirectory}</span><input value={outputName.replace(/\.mp4$/i, '')} onChange={(e) => renameOutput(e.target.value)} /><b>.mp4</b></div>
                <button onClick={chooseOutput}>Chọn</button>
                <button onClick={() => { setOutputPath(''); setOutputName('output.mp4') }}>Xóa</button>
              </div>
              <div className="siv-row">
                <label>File phụ đề <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('subtitles') }}>i</span></label>
                <div className="siv-input"><span title={srtPath}>{srtPath || 'Chưa chọn phụ đề SRT'}</span></div>
                <button onClick={() => chooseInputFile('srt')}>Chọn</button>
                <button onClick={() => setSrtPath('')} disabled={!srtPath}>Xóa</button>
              </div>
              {srtPath && (
                <div className="siv-subtitle-options">
                  <label>
                    <span className="siv-subtitle-title">Phông chữ <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('subtitleFontFamily') }}>i</span></span>
                    <select value={subtitleFontFamily} onChange={(e) => setSubtitleFontFamily(e.target.value)}>
                      {CAPTION_FONT_PRESETS.map((font) => <option key={font.id} value={font.id} style={{ fontFamily: font.css }}>{font.label}</option>)}
                    </select>
                  </label>
                  <label>
                    <span className="siv-subtitle-title">Màu chữ <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('subtitleColor') }}>i</span></span>
                    <input type="color" className="siv-text-color" value={subtitleColor} title="Màu chữ" onChange={e => setSubtitleColor(e.target.value)} />
                  </label>
                  <label>
                    <span className="siv-subtitle-title">Cỡ chữ <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('subtitleSize') }}>i</span></span>
                    <input type="number" min="6" max="120" value={subtitleSize} onChange={(e) => setSubtitleSize(Number(e.target.value))} />
                  </label>
                  <label>
                    <span className="siv-subtitle-title">Lệch (s) <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('subtitleOffset') }}>i</span></span>
                    <input type="number" min="-3600" max="3600" step=".1" value={subtitleOffset} onChange={(e) => setSubtitleOffset(Number(e.target.value))} />
                  </label>
                  <label>
                    <span className="siv-subtitle-title">Lề dưới <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('subtitleMargin') }}>i</span></span>
                    <input type="number" min="0" max="1000" value={subtitleMargin} onChange={(e) => setSubtitleMargin(Number(e.target.value))} />
                  </label>
                  <div className="siv-bg-label">
                    <span className="siv-subtitle-title">Nền chữ <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('subtitleBackground') }}>i</span></span>
                    <span className="siv-bg-row">
                      <div className="siv-bg-tabs">
                        {(['solid', 'box', 'none'] as const).map(id => (
                          <button key={id} type="button" className={`siv-bg-tab${subtitleBackground === id ? ' on' : ''}`}
                            onClick={() => setSubtitleBackground(id)}>{id === 'solid' ? 'Đặc' : id === 'box' ? 'Hộp' : 'Tắt'}</button>
                        ))}
                      </div>
                      {subtitleBackground !== 'none' && <>
                        <input type="color" className="siv-bg-color" value={subtitleBgColor} title="Màu nền" onChange={e => setSubtitleBgColor(e.target.value)} />
                        <input type="range" min={0} max={100} className="siv-bg-slider" value={subtitleOpacity} onChange={e => setSubtitleOpacity(Number(e.target.value))} />
                        <span className="siv-bg-pct">{subtitleOpacity}%</span>
                      </>}
                    </span>
                  </div>
                </div>
              )}
              <p className="siv-hint">Timeline quyết định thời lượng từng ảnh/clip; SRT chỉ dùng để chèn phụ đề.</p>
            </div>
          ) : (
            <div className="siv-settings">
              <div className="siv-set-row">
                <label><span className="siv-setting-title">Độ phân giải <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('resolution') }}>i</span></span>
                  <select value={resolution} onChange={(e) => setResolution(e.target.value)}>
                    <option value="auto">Auto (theo ảnh)</option>
                    <option value="1920x1080">1920 × 1080 (16:9)</option>
                    <option value="1080x1920">1080 × 1920 (9:16)</option>
                    <option value="1080x1080">1080 × 1080 (1:1)</option>
                    <option value="1280x720">1280 × 720</option>
                  </select>
                </label>
                <label><span className="siv-setting-title">FPS <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('fps') }}>i</span></span>
                  <select value={fps} onChange={(e) => setFps(Number(e.target.value))}>
                    <option>24</option><option>25</option><option>30</option><option>60</option>
                  </select>
                </label>
                <label><span className="siv-setting-title">Chất lượng <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('quality') }}>i</span></span>
                  <select value={crf} onChange={(e) => setCrf(Number(e.target.value))}>
                    <option value="18">Cao</option><option value="20">Cân bằng</option><option value="24">Nhanh</option>
                  </select>
                </label>
                <label><span className="siv-setting-title">Encoder <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('encoder') }}>i</span></span>
                  <select value={encoder} onChange={(e) => setEncoder(e.target.value)}>
                    <option value="auto">Tự động</option><option value="gpu">GPU</option><option value="cpu">CPU</option>
                  </select>
                </label>
              </div>
              <div className="siv-set-row">
                <label><span className="siv-setting-title">Hiệu ứng <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('effect') }}>i</span></span>
                  <select value={effect} onChange={(e) => setEffect(e.target.value)}>
                    <option value="random">Ngẫu nhiên</option><option value="fade">Fade</option>
                    <option value="dissolve">Dissolve</option><option value="none">Tắt</option>
                  </select>
                </label>
                <label><span className="siv-setting-title">Zoom <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('zoom') }}>i</span></span>
                  <select value={zoom} onChange={(e) => setZoom(e.target.value)}>
                      <option value="off">Tắt</option><option value="random">Ngẫu nhiên</option>
                      <option value="zoomIn">Zoom in</option><option value="zoomOut">Zoom out</option>
                      <option value="left">Trái → phải</option><option value="right">Phải → trái</option>
                      <option value="up">Dưới → trên</option><option value="down">Trên → dưới</option>
                  </select>
                </label>
              </div>
              <div className="siv-set-row">
                <label><span className="siv-setting-title">Speed (%) <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('speed') }}>i</span></span>
                  <input type="number" min="25" max="400" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
                </label>
                <label><span className="siv-setting-title">Âm lượng (%) <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('volume') }}>i</span></span>
                  <input type="number" min="0" max="300" value={volume} onChange={(e) => setVolume(Number(e.target.value))} />
                </label>
                <label><span className="siv-setting-title">Preview (s) <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('preview') }}>i</span></span>
                  <input type="number" min="1" max="120" value={previewSeconds} onChange={(e) => setPreviewSeconds(Number(e.target.value))} />
                </label>
              </div>
              <div className="siv-set-row">
                <label><span className="siv-setting-title">Xóa metadata <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('metadata') }}>i</span></span>
                  <select value={removeMetadata ? 'on' : 'off'} onChange={(e) => setRemoveMetadata(e.target.value === 'on')}>
                    <option value="off">Tắt</option><option value="on">Bật</option>
                  </select>
                </label>
                <label><span className="siv-setting-title">Xóa logo gốc <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('delogo') }}>i</span></span>
                  <select value={delogoEnabled ? 'on' : 'off'} onChange={(e) => setDelogoEnabled(e.target.value === 'on')}>
                    <option value="off">Tắt</option><option value="on">Bật</option>
                  </select>
                </label>
                {delogoEnabled && <label><span className="siv-setting-title">Tự định vị <span role="button" tabIndex={0} className="siv-info" onClick={(e) => { e.stopPropagation(); setHelpKey('delogoAuto') }}>i</span></span>
                  <select value={delogoAuto ? 'on' : 'off'} onChange={(e) => setDelogoAuto(e.target.value === 'on')}>
                    <option value="on">Bật</option><option value="off">Tắt (kéo tay)</option>
                  </select>
                </label>}
              </div>
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
              <button className="primary" disabled={busy || !mediaFolder || !timelinePath} onClick={() => start(false)}>
                {sending ? 'ĐANG TẢI…' : 'RENDER'}
              </button>
              <button disabled={busy || !mediaFolder || !timelinePath} onClick={() => start(true)}>Preview</button>
              <button disabled={!job || !['processing', 'paused'].includes(job.status)} onClick={togglePause}>
                {job?.status === 'paused' ? 'Tiếp tục' : 'Tạm dừng'}
              </button>
              <button disabled={!busy} onClick={cancel}>Hủy</button>
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
        </div>
        
        <div className="siv-preview-pane">
          <div className="siv-preview-header">
            <span>{job?.status === 'done' ? 'Video Output' : 'Preview trực tiếp'}</span>
            {job?.status === 'done' && job.id && (<>
              <button
                className="siv-btn-sm"
                onClick={() => fetch(`/api/srt-image/jobs/${job.id}/open`, { method: 'POST' })}
                title="Mở bằng app mặc định"
                style={{ marginLeft: 'auto', fontSize: '0.72rem', padding: '2px 8px' }}
              >▶ Mở video</button>
              <button
                className="siv-btn-sm"
                onClick={() => setJob(null)}
                title="Quay lại xem trước"
                style={{ fontSize: '0.72rem', padding: '2px 8px' }}
              >✕ Xem trước</button>
            </>)}
            {job?.status === 'done' && job.name && (
              <span style={{ color: 'var(--muted-foreground)', fontSize: '0.75rem' }}>{job.name}</span>
            )}
          </div>
          {job?.status === 'done' && job.id ? (
            <SubtitleLivePreview
              fontFamily={subtitleFontFamily}
              textColor={subtitleColor}
              bgStyle={subtitleBackground}
              bgColor={subtitleBgColor}
              bgOpacity={subtitleOpacity}
              fontSize={subtitleSize}
              marginBottom={subtitleMargin}
              resolution={resolution}
              onResolutionChange={setResolution}
              mediaFolder={mediaFolder}
              videoSrc={`/api/srt-image/jobs/${job.id}/file`}
            />
          ) : (
            <SubtitleLivePreview
              fontFamily={subtitleFontFamily}
              textColor={subtitleColor}
              bgStyle={subtitleBackground}
              bgColor={subtitleBgColor}
              bgOpacity={subtitleOpacity}
              fontSize={subtitleSize}
              marginBottom={subtitleMargin}
              resolution={resolution}
              onResolutionChange={setResolution}
              mediaFolder={mediaFolder}
              delogoEnabled={delogoEnabled}
              delogoAuto={delogoAuto}
              delogoRect={delogoRect}
              onDelogoRectChange={setDelogoRect}
            />
          )}
        </div>
      </div>

      {helpKey && (
        <div className="siv-help-backdrop" role="presentation" onMouseDown={() => setHelpKey(null)}>
          <section className="siv-help-dialog" role="dialog" aria-modal="true" aria-labelledby="siv-help-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <small>Hướng dẫn sử dụng</small>
                <h2 id="siv-help-title">{HELP[helpKey][0]}</h2>
              </div>
              <button type="button" aria-label="Đóng hướng dẫn" onClick={(e) => { e.stopPropagation(); setHelpKey(null) }}>×</button>
            </header>
            <p>{HELP[helpKey][1]}</p>
            <div><strong>File hoặc thiết lập cần dùng</strong><p>{HELP[helpKey][2]}</p></div>
            <button type="button" className="siv-help-close" onClick={(e) => { e.stopPropagation(); setHelpKey(null) }}>Đã hiểu</button>
          </section>
        </div>
      )}
    </main>
  )
}

// ─── Live Preview ────────────────────────────────────────────────────────────
type PreviewProps = {
  fontFamily: string
  textColor: string
  bgStyle: string
  bgColor: string
  bgOpacity: number
  fontSize: number
  marginBottom: number
  resolution: string
  onResolutionChange: (r: string) => void
  mediaFolder: string
  videoSrc?: string
  delogoEnabled?: boolean
  delogoAuto?: boolean
  delogoRect?: { x: number; y: number; w: number; h: number }
  onDelogoRectChange?: (r: { x: number; y: number; w: number; h: number }) => void
}

const SAMPLE_LINES = ['Đây là dòng phụ đề mẫu,', 'hiển thị trực tiếp theo cài đặt.']

// Platform → resolution mapping (exact specs)
const PLATFORM_OPTIONS = [
  { id: 'auto',      label: 'Theo media', res: 'auto',      w: 16, h: 9,
    icon: <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><rect x="2" y="4" width="20" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="2"/><path d="M8 10l4 3 4-3" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg> },
  { id: 'tiktok',    label: 'TikTok',     res: '1080x1920', w: 9,  h: 16,
    icon: <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1V9.01a6.29 6.29 0 00-.79-.05 6.34 6.34 0 00-6.34 6.34 6.34 6.34 0 006.34 6.34 6.34 6.34 0 006.33-6.34V8.82a8.18 8.18 0 004.79 1.53V6.88a4.85 4.85 0 01-1.02-.19z"/></svg> },
  { id: 'yt-shorts', label: 'Shorts',      res: '1080x1920', w: 9,  h: 16,
    icon: <svg viewBox="0 0 24 24" fill="#FF0000" width="16" height="16"><path d="M10 15l5.19-3L10 9v6m11.56-7.83c.13.47.22 1.1.28 1.9.07.8.1 1.49.1 2.09L22 12c0 2.19-.16 3.8-.44 4.83-.25.9-.83 1.48-1.73 1.73-.47.13-1.33.22-2.65.28-1.3.07-2.49.1-3.59.1L12 19c-4.19 0-6.8-.16-7.83-.44-.9-.25-1.48-.83-1.73-1.73-.13-.47-.22-1.1-.28-1.9-.07-.8-.1-1.49-.1-2.09L2 12c0-2.19.16-3.8.44-4.83.25-.9.83-1.48 1.73-1.73.47-.13 1.33-.22 2.65-.28 1.3-.07 2.49-.1 3.59-.1L12 5c4.19 0 6.8.16 7.83.44.9.25 1.48.83 1.73 1.73z"/></svg> },
  { id: 'youtube',   label: 'YouTube',     res: '1920x1080', w: 16, h: 9,
    icon: <svg viewBox="0 0 24 24" fill="#FF0000" width="16" height="16"><path d="M10 15l5.19-3L10 9v6m11.56-7.83c.13.47.22 1.1.28 1.9.07.8.1 1.49.1 2.09L22 12c0 2.19-.16 3.8-.44 4.83-.25.9-.83 1.48-1.73 1.73-.47.13-1.33.22-2.65.28-1.3.07-2.49.1-3.59.1L12 19c-4.19 0-6.8-.16-7.83-.44-.9-.25-1.48-.83-1.73-1.73-.13-.47-.22-1.1-.28-1.9-.07-.8-.1-1.49-.1-2.09L2 12c0-2.19.16-3.8.44-4.83.25-.9.83-1.48 1.73-1.73.47-.13 1.33-.22 2.65-.28 1.3-.07 2.49-.1 3.59-.1L12 5c4.19 0 6.8.16 7.83.44.9.25 1.48.83 1.73 1.73z"/></svg> },
  { id: 'facebook',  label: 'Facebook',    res: '1080x1920', w: 9,  h: 16,
    icon: <svg viewBox="0 0 24 24" fill="#1877F2" width="16" height="16"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg> },
]

function SubtitleLivePreview({ fontFamily, textColor, bgStyle, bgColor, bgOpacity, fontSize, marginBottom, resolution, onResolutionChange, mediaFolder, videoSrc, delogoEnabled, delogoAuto, delogoRect, onDelogoRectChange }: PreviewProps) {
  // Fetch kích thước thực tế của media khi resolution=auto
  const [mediaAp, setMediaAp] = useState<{ w: number; h: number } | null>(null)
  useEffect(() => {
    if (resolution !== 'auto' || !mediaFolder) { setMediaAp(null); return }
    fetch(`/api/srt-image/media-size?folder=${encodeURIComponent(mediaFolder)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setMediaAp(d))
      .catch(() => setMediaAp(null))
  }, [resolution, mediaFolder])

  // Derive aspect ratio from resolution setting — this is the single source of truth
  const resMap: Record<string, { w: number; h: number }> = {
    'auto':      { w: 16, h: 9 }, // fallback nếu chưa chọn thư mục
    '1920x1080': { w: 16, h: 9 },
    '1280x720':  { w: 16, h: 9 },
    '1080x1920': { w: 9,  h: 16 },
    '1080x1080': { w: 1,  h: 1 },
  }
  // Ưu tiên kích thước thực tế từ media khi đang ở chế độ auto
  const ap = (resolution === 'auto' && mediaAp) ? mediaAp : (resMap[resolution] ?? { w: 16, h: 9 })
  const activePlatform = PLATFORM_OPTIONS.find(p => p.res === resolution)?.id ?? 'auto'
  const frameRef = useRef<HTMLDivElement>(null)
  const [frameH, setFrameH] = useState(300)
  const [thumbIdx, setThumbIdx] = useState(0)

  // Đo chiều cao frame thực tế để tính scale đúng với ASS (PlayResY=288)
  useEffect(() => {
    if (!frameRef.current) return
    const ro = new ResizeObserver(e => setFrameH(e[0].contentRect.height))
    ro.observe(frameRef.current)
    return () => ro.disconnect()
  }, [])

  const fakeSettings = {
    captionTextColor: textColor,
    captionBgStyle: bgStyle as 'none' | 'solid' | 'box' | 'blur',
    captionBgColor: bgColor,
    captionBgOpacity: bgOpacity,
    subtitleFontFamily: fontFamily,
    captionStroke: true,
  // ponytail: cast minimal fake settings; chỉ dùng các field mà captionChromeStyle đọc
  } as Parameters<typeof captionChromeStyle>[0]

  const captionStyle = captionChromeStyle(fakeSettings)
  const fontCss = captionFontCss(fontFamily)

  // Scale khớp ASS: FontSize/MarginV đều dùng PlayResY=288 làm reference
  // MarginV đo từ đáy frame — chrome UI chỉ là overlay visual, không ảnh hưởng vị trí ASS
  const scale = frameH / 288
  // ponytail: ASS FontSize = em-square, CSS fontSize = bounding box (ascender+descender).
  // Hệ số ~0.75 để CSS khớp kết quả libass render thực tế.
  const scaledFontSize = Math.round(fontSize * scale * 0.75)
  const scaledMarginBottom = Math.round(marginBottom * scale)
  // ponytail: ASS Outline (padding hộp nền) là giá trị cố định PlayRes, không phải em-based
  // solid: Outline=1.2, box: Outline=2 — scale giống fontSize/margin
  const outlinePx = bgStyle === 'box' ? Math.round(2 * scale) : bgStyle === 'solid' ? Math.round(1.2 * scale) : 0


  return (
    <div className="siv-live-preview-wrap">
      <div className="siv-live-aspect-toggle">
        {PLATFORM_OPTIONS.map(p => (
          <button
            key={p.id}
            type="button"
            className={p.id === activePlatform ? 'active' : ''}
            onClick={() => onResolutionChange(p.res)}
            title={`${p.label} · ${p.res}`}
          >
            {p.icon}
            <span>{p.label}</span>
          </button>
        ))}
      </div>
      <div className="siv-live-preview-stage">
        <div
          ref={frameRef}
          className="siv-live-preview-frame"
          style={{
            aspectRatio: `${ap.w}/${ap.h}`,
            ...(ap.h > ap.w
              ? { height: '100%', width: 'auto' }
              : { width: '100%' }),
          }}
        >
          {videoSrc ? (
            /* Video output: hiện video thật trong cùng khung, controls bên dưới */
            <video
              src={videoSrc}
              controls
              preload="metadata"
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', background: '#000', zIndex: 30 }}
            />
          ) : (
            <>
              {/* Nền: ảnh thật khi delogo bật, gradient khi không */}
              {delogoEnabled && mediaFolder ? (
                <>
                  <img
                    src={`/api/srt-image/media-thumb?folder=${encodeURIComponent(mediaFolder)}&index=${thumbIdx}`}
                    alt=""
                    style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: 0.85 }}
                    onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                  />
                  <div style={{
                    position: 'absolute', top: 6, right: 6, zIndex: 22,
                    display: 'flex', gap: 3, alignItems: 'center',
                  }}>
                    <button onClick={() => setThumbIdx(i => Math.max(0, i - 1))} style={{
                      width: 22, height: 22, border: 'none', borderRadius: 4,
                      background: 'rgba(0,0,0,0.55)', color: '#fff', fontSize: 13, cursor: 'pointer', lineHeight: 1,
                    }}>◀</button>
                    <span style={{ fontSize: 10, color: '#fff', textShadow: '0 1px 3px #000', fontWeight: 600 }}>{thumbIdx + 1}</span>
                    <button onClick={() => setThumbIdx(i => i + 1)} style={{
                      width: 22, height: 22, border: 'none', borderRadius: 4,
                      background: 'rgba(0,0,0,0.55)', color: '#fff', fontSize: 13, cursor: 'pointer', lineHeight: 1,
                    }}>▶</button>
                  </div>
                </>
              ) : (
                <div className="siv-live-preview-bg" />
              )}
              {/* Platform UI chrome overlay */}
              <PlatformChrome platform={activePlatform} />
              {/* Phụ đề mẫu */}
              <div
                className="siv-live-caption-wrap"
                style={{ paddingBottom: `${scaledMarginBottom}px` }}
              >
                {SAMPLE_LINES.map((line, i) => (
                  <div
                    key={i}
                    className="siv-live-caption-line"
                    style={{
                      ...captionStyle,
                      fontFamily: fontCss,
                      fontSize: `${scaledFontSize}px`,
                      lineHeight: 1.15,
                      ...(outlinePx > 0 && { padding: `${outlinePx}px` }),
                    }}
                  >
                    {line}
                  </div>
                ))}
              </div>
              {/* Delogo: vùng xóa logo */}
              {delogoEnabled && delogoRect && (
                <div
                  style={{
                    position: 'absolute', zIndex: 20,
                    left: `${delogoRect.x}%`, top: `${delogoRect.y}%`,
                    width: `${delogoRect.w}%`, height: `${delogoRect.h}%`,
                    border: '1.5px solid rgba(0,180,255,0.7)',
                    borderRadius: 3,
                    background: 'linear-gradient(135deg, rgba(0,180,255,0.12), rgba(0,120,255,0.08))',
                    boxShadow: '0 0 0 1px rgba(0,0,0,0.15), inset 0 0 8px rgba(0,180,255,0.08)',
                    pointerEvents: 'none',
                  }}
                >
                  <span style={{
                    position: 'absolute', top: -18, left: 0,
                    fontSize: 9, fontWeight: 600, lineHeight: '16px',
                    padding: '0 5px', borderRadius: '3px 3px 0 0',
                    background: 'rgba(0,180,255,0.85)', color: '#fff',
                    whiteSpace: 'nowrap',
                  }}>✕ Logo</span>
                </div>
              )}
              {/* Drag overlay — chỉ khi delogo bật VÀ tự định vị tắt */}
              {delogoEnabled && !delogoAuto && (
                <div
                  style={{ position: 'absolute', inset: 0, zIndex: 21, cursor: 'crosshair' }}
                  onMouseDown={(e) => {
                    const el = e.currentTarget
                    const rect = el.getBoundingClientRect()
                    const startX = ((e.clientX - rect.left) / rect.width) * 100
                    const startY = ((e.clientY - rect.top) / rect.height) * 100
                    const onMove = (ev: MouseEvent) => {
                      const curX = Math.max(0, Math.min(100, ((ev.clientX - rect.left) / rect.width) * 100))
                      const curY = Math.max(0, Math.min(100, ((ev.clientY - rect.top) / rect.height) * 100))
                      const nx = Math.min(startX, curX), ny = Math.min(startY, curY)
                      const nw = Math.abs(curX - startX), nh = Math.abs(curY - startY)
                      if (nw > 1 && nh > 0.5) onDelogoRectChange?.({ x: +nx.toFixed(1), y: +ny.toFixed(1), w: +nw.toFixed(1), h: +nh.toFixed(1) })
                    }
                    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
                    window.addEventListener('mousemove', onMove)
                    window.addEventListener('mouseup', onUp)
                  }}
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Platform Chrome Overlay (accurate per-platform layouts) ─────────────────
// ponytail: shared icon helpers avoid repetition across 4 platform branches
const IcoHeart   = () => <svg viewBox="0 0 24 24" width="22" height="22" fill="white"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
const IcoComment = () => <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="white" strokeWidth="2" strokeLinejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
const IcoShare   = () => <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round"><path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>
const IcoBookmark= () => <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg>
const IcoThumbUp = () => <svg viewBox="0 0 24 24" width="22" height="22" fill="white"><path d="M2 20h2c.55 0 1-.45 1-1v-9H2v10zm19.83-7.12c.11-.25.17-.52.17-.88v-2c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L13.17 1 6.59 7.59C6.22 7.95 6 8.45 6 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05-.03.15z"/></svg>
const IcoThumbDn = () => <svg viewBox="0 0 24 24" width="22" height="22" fill="white"><path d="M22 4h-2c-.55 0-1 .45-1 1v9h2V4zM2.17 11.12c-.11.25-.17.52-.17.88v2c0 1.1.9 2 2 2h6.31l-.95 4.57c0 .41.17.79.44 1.06L10.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2H7c-.83 0-1.54.5-1.84 1.22L2.17 11.12z"/></svg>
const IcoMore3Vert=()=> <svg viewBox="0 0 24 24" width="20" height="20" fill="white"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg>
const IcoMore3Horiz=()=><svg viewBox="0 0 24 24" width="20" height="20" fill="white"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
const IcoMusic   = () => <svg viewBox="0 0 24 24" width="10" height="10" fill="white"><path d="M9 18V5l12-2v13M6 21a3 3 0 100-6 3 3 0 000 6zM18 19a3 3 0 100-6 3 3 0 000 6z"/></svg>
const IcoAvatar  = ({ s = 22 }: { s?: number }) => (
  <svg viewBox="0 0 24 24" width={s} height={s}>
    <circle cx="12" cy="12" r="11" fill="rgba(255,255,255,0.18)" stroke="white" strokeWidth="1.2"/>
    <circle cx="12" cy="9" r="4" fill="rgba(255,255,255,0.55)"/>
    <path d="M4 22c0-4.4 3.6-8 8-8s8 3.6 8 8" fill="rgba(255,255,255,0.55)"/>
  </svg>
)
const Bar = () => <div className="plat-statusbar"><span>9:41</span><span>▐▐ ◈</span></div>
const Act = ({ ico, lbl }: { ico: React.ReactNode; lbl?: string }) => (
  <div className="plat-action">{ico}{lbl && <small>{lbl}</small>}</div>
)

function PlatformChrome({ platform }: { platform: string }) {
  if (platform === 'tiktok') return (
    <div className="plat-chrome plat-tiktok">
      <Bar/>
      {/* Right sidebar — Avatar · Like · Comment · Bookmark · Share · Disc */}
      <div className="plat-right">
        <div className="plat-action" style={{marginBottom:4}}>
          <div className="plat-tt-avatar"><IcoAvatar s={28}/><div className="plat-tt-plus">+</div></div>
        </div>
        <Act ico={<IcoHeart/>}    lbl="2.8M"/>
        <Act ico={<IcoComment/>}  lbl="48K"/>
        <Act ico={<IcoBookmark/>} lbl="12K"/>
        <Act ico={<IcoShare/>}    lbl="Share"/>
        <div className="plat-action" style={{marginTop:4}}>
          <div className="plat-tt-disc"><div className="plat-tt-disc-inner"/></div>
        </div>
      </div>
      {/* Bottom: @user · desc · music */}
      <div className="plat-bottom">
        <div className="plat-bottom-left">
          <div className="plat-username">@your_username</div>
          <div className="plat-desc">Mô tả video của bạn #hashtag</div>
          <div className="plat-music-row"><IcoMusic/><span>Tên bài hát · Nghệ sĩ</span></div>
        </div>
        {/* TikTok logo bottom-right */}
        <svg viewBox="0 0 24 24" fill="white" width="20" height="20" style={{flexShrink:0,alignSelf:'flex-end',marginBottom:2,opacity:.9}}><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1V9.01a6.29 6.29 0 00-.79-.05 6.34 6.34 0 00-6.34 6.34 6.34 6.34 0 006.34 6.34 6.34 6.34 0 006.33-6.34V8.82a8.18 8.18 0 004.79 1.53V6.88a4.85 4.85 0 01-1.02-.19z"/></svg>
      </div>
    </div>
  )

  if (platform === 'yt-shorts') return (
    <div className="plat-chrome plat-ytshorts">
      <Bar/>
      {/* Top nav: back · Shorts · search · more */}
      <div className="plat-yt-topnav">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
        <span style={{fontWeight:700,fontSize:'0.62rem',flex:1,textAlign:'center'}}>Shorts</span>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
        <IcoMore3Vert/>
      </div>
      {/* Right sidebar: Like · Dislike · Comment · Share · More */}
      <div className="plat-right" style={{bottom:85}}>
        <Act ico={<IcoThumbUp/>} lbl="2.8M"/>
        <Act ico={<IcoThumbDn/>} lbl="Dislike"/>
        <Act ico={<IcoComment/>} lbl="48K"/>
        <Act ico={<IcoShare/>}   lbl="Share"/>
        <Act ico={<IcoMore3Vert/>}/>
      </div>
      {/* Bottom: avatar · channel · subscribe · title */}
      <div className="plat-bottom">
        <div className="plat-bottom-left">
          <div style={{display:'flex',alignItems:'center',gap:5,marginBottom:3}}>
            <IcoAvatar s={20}/>
            <div className="plat-username">@channel_name</div>
            <div style={{fontSize:'0.48rem',border:'1px solid white',borderRadius:3,padding:'1px 4px',whiteSpace:'nowrap'}}>Đăng ký</div>
          </div>
          <div className="plat-desc">Tiêu đề Short · Xem thêm</div>
        </div>
      </div>
      {/* Seek bar at very bottom */}
      <div className="plat-yt-seek"><div className="plat-yt-seek-fill"/></div>
    </div>
  )

  if (platform === 'youtube') return (
    <div className="plat-chrome plat-youtube">
      <Bar/>
      {/* YouTube header bar */}
      <div className="plat-yt-header">
        <svg viewBox="0 0 24 24" fill="#FF0000" width="17" height="17"><path d="M10 15l5.19-3L10 9v6m11.56-7.83c.13.47.22 1.1.28 1.9.07.8.1 1.49.1 2.09L22 12c0 2.19-.16 3.8-.44 4.83-.25.9-.83 1.48-1.73 1.73-.47.13-1.33.22-2.65.28-1.3.07-2.49.1-3.59.1L12 19c-4.19 0-6.8-.16-7.83-.44-.9-.25-1.48-.83-1.73-1.73-.13-.47-.22-1.1-.28-1.9-.07-.8-.1-1.49-.1-2.09L2 12c0-2.19.16-3.8.44-4.83.25-.9.83-1.48 1.73-1.73.47-.13 1.33-.22 2.65-.28 1.3-.07 2.49-.1 3.59-.1L12 5c4.19 0 6.8.16 7.83.44.9.25 1.48.83 1.73 1.73z"/></svg>
        <span style={{color:'white',fontSize:'0.6rem',fontWeight:700}}>YouTube</span>
        <div style={{marginLeft:'auto',display:'flex',gap:8,alignItems:'center'}}>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="white" strokeWidth="2"><path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0"/></svg>
          <IcoAvatar s={16}/>
        </div>
      </div>
      {/* Progress + controls at bottom */}
      <div style={{position:'absolute',bottom:0,left:0,right:0}}>
        <div style={{height:'2px',background:'rgba(255,255,255,0.25)',position:'relative',margin:'0 8px'}}>
          <div style={{position:'absolute',left:0,top:0,bottom:0,width:'38%',background:'#FF0000'}}/>
          <div style={{position:'absolute',left:'38%',top:'-3px',width:'7px',height:'7px',background:'#FF0000',borderRadius:'50%',marginLeft:'-3px'}}/>
        </div>
        <div style={{display:'flex',alignItems:'center',padding:'3px 8px 5px',gap:7,fontSize:'0.5rem',color:'rgba(255,255,255,0.85)'}}>
          <svg viewBox="0 0 24 24" width="12" height="12" fill="white"><polygon points="5,3 19,12 5,21"/></svg>
          <span>1:06 / 2:32</span>
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="white" strokeWidth="2"><polygon points="11,5 6,9 2,9 2,15 6,15 11,19"/><path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07"/></svg>
          <div style={{flex:1}}/>
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="white" strokeWidth="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>
        </div>
      </div>
    </div>
  )

  if (platform === 'facebook') return (
    <div className="plat-chrome plat-facebook">
      <Bar/>
      {/* Right sidebar: Like · Comment · Share · More */}
      <div className="plat-right">
        <Act ico={<IcoHeart/>}      lbl="2.8M"/>
        <Act ico={<IcoComment/>}    lbl="48K"/>
        <Act ico={<IcoShare/>}      lbl="Share"/>
        <Act ico={<IcoMore3Horiz/>}/>
      </div>
      {/* Bottom: avatar · name · follow · desc · music */}
      <div className="plat-bottom">
        <div className="plat-bottom-left">
          <div style={{display:'flex',alignItems:'center',gap:5,marginBottom:3}}>
            <IcoAvatar s={22}/>
            <div className="plat-username">Your Name</div>
            <div style={{fontSize:'0.48rem',border:'1px solid white',borderRadius:3,padding:'1px 5px',whiteSpace:'nowrap'}}>+ Theo dõi</div>
          </div>
          <div className="plat-desc">Mô tả video của bạn #hashtag</div>
          <div className="plat-music-row"><IcoMusic/><span>Tên bài hát · Nghệ sĩ</span></div>
        </div>
      </div>
    </div>
  )

  return null
}
