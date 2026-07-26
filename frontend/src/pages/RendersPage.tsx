import { useCallback, useEffect, useState } from 'react'
import { api } from '@/features/project/project.api'
import type { RenderedVideo } from '@/features/project/project.types'
import { IconDownload, IconFilm, IconRefresh } from '@/shared/components/Icons'
import './RendersPage.css'

const PAGE_SIZE = 10

function durationLabel(seconds: number) {
  const total = Math.max(0, Math.round(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
    : `${minutes}:${String(secs).padStart(2, '0')}`
}

function sizeLabel(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

function renderName(item: RenderedVideo) {
  return item.name?.trim() || `Render ${item.projectId}`
}

export default function RendersPage({ onEdit }: { onEdit: (projectId: string) => Promise<void> }) {
  const [items, setItems] = useState<RenderedVideo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [canReveal, setCanReveal] = useState(false)
  const [viewing, setViewing] = useState<RenderedVideo | null>(null)
  const [page, setPage] = useState(1)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [nameDraft, setNameDraft] = useState('')
  const [openingId, setOpeningId] = useState<string | null>(null)
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const pageItems = items.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await api.renders()
      setItems(result.items)
      setCanReveal(result.canReveal)
      setPage(1)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không tải được danh sách render')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!viewing) return
    const close = (event: KeyboardEvent) => event.key === 'Escape' && setViewing(null)
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [viewing])

  async function reveal(renderId: string) {
    try {
      await api.revealRender(renderId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không mở được thư mục')
    }
  }

  async function saveName(item: RenderedVideo) {
    const name = nameDraft.trim()
    if (!name || name === renderName(item)) {
      setEditingId(null)
      return
    }
    try {
      const saved = await api.renameRender(item.renderId, name)
      setItems((current) => current.map((row) => row.renderId === item.renderId ? { ...row, name: saved.name } : row))
      setEditingId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không đổi được tên render')
    }
  }

  async function deleteRender(item: RenderedVideo) {
    if (!window.confirm(`Xóa video “${renderName(item)}”? Thao tác này không thể hoàn tác.`)) return
    try {
      await api.deleteRender(item.renderId)
      setItems((current) => current.filter((row) => row.renderId !== item.renderId))
      if (viewing?.renderId === item.renderId) setViewing(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không xóa được video render')
    }
  }

  async function editRender(item: RenderedVideo) {
    setOpeningId(item.renderId)
    setError('')
    try {
      await onEdit(item.projectId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không mở lại được project trong editor')
      setOpeningId(null)
    }
  }

  return (
    <main className="renders-page">
      <header className="renders-head">
        <div>
          <h1>Video đã render</h1>
          <p>{items.length ? `${items.length} video · mới nhất trước` : 'Các video xuất hoàn tất sẽ xuất hiện tại đây.'}</p>
        </div>
        <button type="button" className="renders-refresh" onClick={() => void load()} disabled={loading}>
          <IconRefresh size={16} /> Làm mới
        </button>
      </header>

      {error && <div className="renders-alert">{error} <button type="button" onClick={() => void load()}>Thử lại</button></div>}
      {loading ? (
        <div className="renders-state">Đang tải danh sách…</div>
      ) : items.length === 0 ? (
        <div className="renders-state renders-empty"><IconFilm size={36} /><strong>Chưa có video đã render</strong><span>Xuất một video từ Clone Video để xem tại đây.</span></div>
      ) : (
        <section className="renders-grid" aria-label="Danh sách video đã render">
          {pageItems.map((item) => (
            <article className="render-card" key={item.renderId}>
              <button type="button" className="render-thumb" onClick={() => setViewing(item)} aria-label={`Xem video ${item.renderId}`}>
                <img src={item.thumbnailUrl} alt="" loading="lazy" onError={(event) => { event.currentTarget.hidden = true }} />
                <span className="render-thumb-fallback"><IconFilm size={32} /></span>
                <span className="render-play">▶</span>
                <time>{durationLabel(item.duration)}</time>
              </button>
              <div className="render-info">
                {editingId === item.renderId ? (
                  <input
                    className="render-name-input"
                    value={nameDraft}
                    maxLength={120}
                    autoFocus
                    aria-label="Tên bản render"
                    onChange={(event) => setNameDraft(event.target.value)}
                    onBlur={() => void saveName(item)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') event.currentTarget.blur()
                      if (event.key === 'Escape') {
                        // Thoát edit trực tiếp: blur() sẽ chạy saveName với
                        // nameDraft cũ trong closure → vẫn đổi tên dù đã huỷ.
                        setNameDraft(renderName(item))
                        setEditingId(null)
                      }
                    }}
                  />
                ) : (
                  <button
                    type="button"
                    className="render-name"
                    title="Bấm để đổi tên"
                    onClick={() => { setEditingId(item.renderId); setNameDraft(renderName(item)) }}
                  >
                    {renderName(item)}
                  </button>
                )}
                <span>{new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(item.createdAt))}</span>
                <span>{item.width}×{item.height} · {sizeLabel(item.sizeBytes)}</span>
              </div>
              <div className="render-actions">
                <button type="button" onClick={() => setViewing(item)}>Xem</button>
                {canReveal ? (
                  <button type="button" onClick={() => void reveal(item.renderId)}>Mở thư mục</button>
                ) : (
                  <a href={item.downloadUrl} download={`video-clone-${item.renderId}.mp4`}><IconDownload size={14} /> Tải xuống</a>
                )}
                <button type="button" disabled={openingId === item.renderId} onClick={() => void editRender(item)}>
                  {openingId === item.renderId ? 'Đang mở…' : 'Sửa'}
                </button>
                <button type="button" className="render-delete" onClick={() => void deleteRender(item)}>Xóa</button>
              </div>
            </article>
          ))}
        </section>
      )}

      {!loading && items.length > PAGE_SIZE && (
        <nav className="renders-pagination" aria-label="Phân trang video đã render">
          <button type="button" disabled={safePage === 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Trước</button>
          <span>Trang {safePage}/{totalPages}</span>
          <button type="button" disabled={safePage === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Sau</button>
        </nav>
      )}

      {viewing && (
        <div className="render-view-backdrop" role="presentation">
          <div className="render-view" role="dialog" aria-modal="true" aria-label={`Xem render ${viewing.projectId}`} onMouseDown={(event) => event.stopPropagation()}>
            <div><strong>{renderName(viewing)}</strong><button type="button" onClick={() => setViewing(null)}>Đóng</button></div>
            <video src={viewing.videoUrl} controls autoPlay playsInline />
          </div>
        </div>
      )}
    </main>
  )
}
