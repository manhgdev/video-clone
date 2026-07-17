import { useState } from 'react'
import type { DownloadJob, DownloadQuality } from './download.types'
import { downloadApi } from './download.api'
import './DownloadStudio.css'

export default function DownloadStudio() {
  const [url, setUrl] = useState('')
  const [quality, setQuality] = useState<DownloadQuality>('best')
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!url.trim()) return
    setBusy(true)
    setError('')
    try {
      const job = await downloadApi.start(url.trim(), quality)
      setJobs((prev) => [job, ...prev])
      setUrl('')
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'API tải video chưa sẵn sàng — sắp ra mắt.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dl-studio">
      <div className="dl-head">
        <h1>Download Video</h1>
        <p>Dán link video — tải về máy (yt-dlp / backend).</p>
      </div>

      <form className="dl-card" onSubmit={onSubmit}>
        <label className="dl-field">
          <span>URL video</span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…"
            required
          />
        </label>
        <label className="dl-field">
          <span>Chất lượng</span>
          <select value={quality} onChange={(e) => setQuality(e.target.value as DownloadQuality)}>
            <option value="best">Tốt nhất</option>
            <option value="1080">1080p</option>
            <option value="720">720p</option>
            <option value="480">480p</option>
            <option value="audio">Chỉ audio</option>
          </select>
        </label>
        <button type="submit" className="dl-btn" disabled={busy || !url.trim()}>
          {busy ? 'Đang gửi…' : 'Tải về'}
        </button>
        {error && <p className="dl-error">{error}</p>}
      </form>

      <div className="dl-card">
        <h2>Lịch sử / tiến độ</h2>
        {jobs.length === 0 ? (
          <p className="dl-empty">Chưa có job — dán link và bấm Tải về.</p>
        ) : (
          <ul className="dl-jobs">
            {jobs.map((j) => (
              <li key={j.id}>
                <strong>{j.title || j.url}</strong>
                <span>{j.status} · {j.progress}%</span>
                {j.message && <em>{j.message}</em>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
