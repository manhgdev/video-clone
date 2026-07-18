import type { Segment } from '@/features/project/project.types'

/**
 * Bung compound shell → children absolute time (list caption / preview).
 * Children relative (0..span) hoặc absolute đều ok.
 */
export function expandCompoundShell(shell: Segment): Segment[] {
  const children = shell.compoundChildren
  if (!children?.length) return []
  const t0 = Number(shell.start) || 0
  const t1 = Number(shell.end) || t0
  const span = Math.max(0.05, t1 - t0)
  let maxChildEnd = 0
  for (const ch of children) {
    const en = Number(ch.end) || Number(ch.start) || 0
    if (en > maxChildEnd) maxChildEnd = en
  }
  const absolute = maxChildEnd > span + 0.35
  const out: Segment[] = []
  for (const ch of children) {
    const st = Number(ch.start) || 0
    const en = Number(ch.end) || st
    if (absolute) {
      out.push({
        ...ch,
        start: st,
        end: Math.max(st + 0.05, en),
        isCompound: undefined,
        compoundChildren: undefined,
        groupId: undefined,
      })
      continue
    }
    const cs = ch.coverStart
    const ce = ch.coverEnd
    out.push({
      ...ch,
      start: t0 + st,
      end: t0 + Math.max(st + 0.05, en),
      coverStart: typeof cs === 'number' ? t0 + cs : undefined,
      coverEnd: typeof ce === 'number' ? t0 + ce : undefined,
      isCompound: undefined,
      compoundChildren: undefined,
      groupId: undefined,
    })
  }
  return out
}

/** List hiển thị / preview: shell biến mất, chỉ còn câu gốc. */
export function expandSegmentsForList(list: Segment[]): Segment[] {
  const out: Segment[] = []
  for (const s of list) {
    if (s.isCompound) {
      out.push(...expandCompoundShell(s))
      continue
    }
    out.push(s)
  }
  return out
    .slice()
    .sort((a, b) => a.start - b.start || a.end - b.end)
    .map((s, i) => ({ ...s, index: i }))
}

/** Ghi sửa 1 câu (có thể nằm trong compound children). */
export function patchSegmentInTree(list: Segment[], next: Segment): Segment[] {
  return list.map((s) => {
    if (s.id === next.id) return next
    if (!s.isCompound || !s.compoundChildren?.length) return s
    const t0 = Number(s.start) || 0
    const t1 = Number(s.end) || t0
    const span = Math.max(0.05, t1 - t0)
    let maxChildEnd = 0
    for (const ch of s.compoundChildren) {
      const en = Number(ch.end) || Number(ch.start) || 0
      if (en > maxChildEnd) maxChildEnd = en
    }
    const absolute = maxChildEnd > span + 0.35
    let hit = false
    const children = s.compoundChildren.map((ch) => {
      if (ch.id !== next.id) return ch
      hit = true
      // Lưu relative nếu shell đang relative
      if (absolute) {
        return {
          ...next,
          isCompound: undefined,
          compoundChildren: undefined,
        }
      }
      return {
        ...next,
        start: Math.max(0, next.start - t0),
        end: Math.max(0.05, next.end - t0),
        coverStart:
          typeof next.coverStart === 'number' ? Math.max(0, next.coverStart - t0) : undefined,
        coverEnd:
          typeof next.coverEnd === 'number' ? Math.max(0, next.coverEnd - t0) : undefined,
        isCompound: undefined,
        compoundChildren: undefined,
      }
    })
    return hit ? { ...s, compoundChildren: children } : s
  })
}
