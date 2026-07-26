/**
 * State + luồng sửa segments của App: debounce PUT từng đoạn, replace cả list,
 * và helper chuẩn hóa list từ server.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './project.api'
import { patchSegmentInTree } from './expandCompound'
import type { Segment } from './project.types'

export function asSegmentList(raw: unknown): Segment[] {
  if (!Array.isArray(raw)) return []
  return raw.map((s) => {
    if (!s || typeof s !== 'object') {
      return {
        id: '',
        index: 0,
        start: 0,
        end: 0.1,
        source: '',
        translation: '',
        voice: 'system',
      } as Segment
    }
    const o = s as Segment
    return {
      ...o,
      source: o.source ?? '',
      translation: o.translation ?? '',
      voice: o.voice ?? 'system',
    }
  })
}

/** Server hay đóng dấu Adam sau Dịch — đồng bộ về default đang chọn nếu cả loạt cùng 1 giọng */
export function applyDefaultVoice(segs: Segment[], voice: string): Segment[] {
  if (!voice || !segs.length) return segs
  const uniq = new Set(segs.map((s) => (s.voice || '').trim()).filter(Boolean))
  if (uniq.size <= 1 && (!uniq.size || !uniq.has(voice))) {
    return segs.map((s) => ({ ...s, voice }))
  }
  return segs.map((s) => {
    const v = (s.voice || '').trim()
    if (!v || v === 'system') return { ...s, voice }
    return s
  })
}

export function useSegmentEditing({
  projectId,
  defaultVoice,
}: {
  projectId: string | null
  defaultVoice: string
}) {
  const [segments, setSegments] = useState<Segment[]>([])
  const segSaveTimer = useRef<number | null>(null)
  const segSavePending = useRef<{
    projectId: string
    wasTop: boolean
    seg: Segment
    nextTree: Segment[]
  } | null>(null)
  const projectIdRef = useRef(projectId)
  projectIdRef.current = projectId

  const flushSegmentSave = useCallback(async () => {
    if (segSaveTimer.current != null) {
      window.clearTimeout(segSaveTimer.current)
      segSaveTimer.current = null
    }
    const p = segSavePending.current
    segSavePending.current = null
    if (!p) return
    const { projectId: pid, wasTop, seg, nextTree } = p
    try {
      if (wasTop) await api.updateSegment(pid, seg)
      else await api.replaceSegments(pid, nextTree)
    } catch {
      /* keep local */
    }
  }, [])

  const onSegmentChange = useCallback((seg: Segment) => {
    // Normalize text — tránh .length trên null → trắng trang
    const safe: Segment = {
      ...seg,
      source: seg.source ?? '',
      translation: seg.translation ?? '',
      voice: seg.voice ?? 'system',
    }
    const pid = projectIdRef.current
    setSegments((prev) => {
      const list = Array.isArray(prev) ? prev : []
      const wasTop = list.some((s) => s.id === safe.id)
      const nextTree = wasTop
        ? list.map((s) => (s.id === safe.id ? safe : s))
        : patchSegmentInTree(list, safe)
      if (pid) {
        // Debounce PUT — 389 đoạn × mỗi phím trước đây đơ UI + lock meta
        segSavePending.current = { projectId: pid, wasTop, seg: safe, nextTree }
        if (segSaveTimer.current != null) window.clearTimeout(segSaveTimer.current)
        segSaveTimer.current = window.setTimeout(() => {
          void flushSegmentSave()
        }, 350)
      }
      return nextTree
    })
  }, [flushSegmentSave])

  async function onSegmentsReplace(next: Segment[], opts?: { persist?: boolean }) {
    // A whole-list operation (delete/split/trim) supersedes any debounced
    // single-segment PUT.  Leaving that timer alive can write the deleted
    // segment back after the replace request completes.
    if (segSaveTimer.current != null) {
      window.clearTimeout(segSaveTimer.current)
      segSaveTimer.current = null
    }
    segSavePending.current = null
    // UI cập nhật ngay — không đợi network (tránh đơ khi merge group lớn)
    const ordered = [...next]
      .sort((a, b) => a.start - b.start || a.end - b.end)
      .map((s, i) => ({ ...s, index: i }))
    setSegments(ordered)
    if (!projectId || opts?.persist === false) return
    // Persist nền; không ghi đè local nếu user đã edit tiếp
    const snap = ordered
    void api.replaceSegments(projectId, snap).then((saved) => {
      if (!Array.isArray(saved)) return
      // Chỉ sync nếu list id vẫn khớp snapshot (tránh race)
      setSegments((cur) => {
        if (cur.length !== snap.length) return cur
        const same =
          cur.length === snap.length
          && cur.every((s, i) => s.id === snap[i]?.id)
        if (!same) return cur
        const nextSaved = applyDefaultVoice(asSegmentList(saved), defaultVoice)
        // Giữ compoundChildren local nếu server strip (schema cũ / race)
        return nextSaved.map((s, i) => {
          const loc = snap[i]
          let out = s
          if (
            loc?.isCompound
            && loc.compoundChildren?.length
            && (!s.compoundChildren?.length || s.compoundChildren.length < loc.compoundChildren.length)
          ) {
            out = {
              ...out,
              isCompound: true,
              compoundChildren: loc.compoundChildren,
            }
          }
          // Reset OCR: local đã xóa bbox — đừng nhận lại bbox cũ từ server preserve
          if (loc && loc.bbox == null && s.bbox) {
            out = { ...out, bbox: undefined, captionLayout: undefined, bboxInherited: undefined }
          }
          return out
        })
      })
    }).catch(() => { /* keep local */ })
  }

  // phục hồi nếu state segments bị ghi nhầm (vd. onClick truyền event DOM)
  useEffect(() => {
    if (!projectId || Array.isArray(segments)) return
    void api.segments(projectId)
      .then((segs) => setSegments(applyDefaultVoice(asSegmentList(segs), defaultVoice)))
      .catch(() => setSegments([]))
  }, [projectId, segments, defaultVoice])

  return {
    segments,
    setSegments,
    flushSegmentSave,
    onSegmentChange,
    onSegmentsReplace,
  }
}
