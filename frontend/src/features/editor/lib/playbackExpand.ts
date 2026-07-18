import type { Segment } from '@/features/project/project.types'
import { expandCompoundShell } from '@/features/project/expandCompound'
import { reindexSegments } from './timelineBasics'

/** Bung mọi compound → list caption như chưa ghép (preview chữ/mask/TTS). */
export function expandSegmentsForPlayback(list: Segment[]): Segment[] {
  const out: Segment[] = []
  for (const s of list) {
    if (s.isCompound) {
      // Shell không có chữ — chỉ children
      out.push(...expandCompoundShell(s))
      continue
    }
    out.push(s)
  }
  return reindexSegments(out)
}
