/**
 * Luồng lồng tiếng + huỷ job: lock chống double-click, busyAt chặn Huỷ dính
 * click sớm, và optimistic cancel.
 */
import { useRef, type Dispatch, type SetStateAction } from 'react'
import { api } from './project.api'
import type { JobStatus, ProjectSettings, Segment } from './project.types'

export function useDubControl({
  projectId,
  status,
  setStatus,
  settings,
  setSegments,
  setProgressMinimized,
  flushSegmentSave,
}: {
  projectId: string | null
  status: JobStatus
  setStatus: Dispatch<SetStateAction<JobStatus>>
  settings: ProjectSettings
  setSegments: Dispatch<SetStateAction<Segment[]>>
  setProgressMinimized: (minimized: boolean) => void
  flushSegmentSave: () => Promise<void>
}) {
  const dubLockRef = useRef(false)
  /** chặn double-click: Dịch/Xuất rồi dính nút Huỷ vừa hiện */
  const busyAt = useRef(0)

  /** Mở khóa lồng tiếng — gọi mọi đường thoát job (huỷ / lỗi / xong / disconnect). */
  function releaseDubLock() {
    dubLockRef.current = false
  }

  async function onDub(opts?: { force?: boolean }) {
    if (!projectId) return
    // Lock đồng bộ (trước setState) — chặn double-click / spam
    if (status.running || dubLockRef.current) return
    dubLockRef.current = true
    busyAt.current = Date.now()
    setProgressMinimized(false)
    const force = Boolean(opts?.force)
    // Chỉ xóa audio UI khi force gen lại — lần thường giữ cache TTS trên đĩa
    if (force) {
      setSegments((segs) =>
        (Array.isArray(segs) ? segs : []).map((s) => ({
          ...s,
          source: s.source ?? '',
          translation: s.translation ?? '',
          audioFile: undefined,
          audioUrl: undefined,
          audioDuration: undefined,
          videoSpeed: undefined,
        })),
      )
    }
    setStatus({
      step: 'dub',
      progress: 0,
      message: force ? 'Đang gen lại TTS (bỏ cache)…' : 'Đang lồng tiếng…',
      running: true,
      error: undefined,
    })
    try {
      // Flush bản dịch đang gõ trước khi server đọc meta
      await flushSegmentSave()
      await api.dub(projectId, { ...settings, forceTts: force })
      setStatus((s) => ({ ...s, running: true }))
      // Safety: nếu poll không về (backend die) — mở khóa sau 2 phút
      window.setTimeout(() => {
        if (dubLockRef.current && Date.now() - busyAt.current > 110_000) {
          releaseDubLock()
        }
      }, 120_000)
    } catch (e) {
      releaseDubLock()
      const msg = e instanceof Error ? e.message : 'Lồng tiếng thất bại'
      setStatus({
        step: 'dub',
        progress: 0,
        message: msg,
        running: false,
        // message đầy đủ — không để error='dub' (popup chỉ hiện "dub")
        error: msg,
      })
    }
  }

  async function onCancel() {
    if (!projectId || !status.running) return
    // chỉ chặn double-click cực sớm (mount Huỷ)
    if (Date.now() - busyAt.current < 400) return
    const stepNow = status.step
    // Mở khóa lồng tiếng ngay — poll dừng khi running=false nên không clear lock được
    releaseDubLock()
    // optimistic — đừng chờ server
    setStatus({
      step: stepNow,
      progress: 0,
      message:
        stepNow === 'export'
          ? 'Đã huỷ xuất bản'
          : stepNow === 'dub'
            ? 'Đã huỷ lồng tiếng'
            : 'Đang huỷ…',
      running: false,
      error: 'cancelled',
    })
    try {
      await api.cancel(projectId)
    } catch {
      /* flag server có thể fail; UI đã dừng */
    } finally {
      releaseDubLock()
    }
  }

  return { busyAt, dubLockRef, releaseDubLock, onDub, onCancel }
}
