import { useMemo } from 'react'
import type { ProjectSettings, Segment } from '@/features/project/project.types'
import SegmentCard from './SegmentCard'
import { expandSegmentsForList } from './expandCompound'
import './SegmentList.css'

function safeSeg(s: Segment): Segment {
  return {
    ...s,
    source: s.source ?? '',
    translation: s.translation ?? '',
    voice: s.voice ?? 'system',
  }
}

type Props = {
  segments: Segment[]
  voices: { id: string; name: string }[]
  defaultVoice: string
  targetLang: string
  sourceLang?: string
  translator?: ProjectSettings['translator']
  videoUrl: string | null
  projectId: string | null
  onChange: (seg: Segment) => void
}

export default function SegmentList({
  segments,
  voices,
  defaultVoice,
  targetLang,
  sourceLang,
  translator,
  videoUrl,
  projectId,
  onChange,
}: Props) {
  // Alt+G: list vẫn hiện từng câu (không hiện shell [Compound ×N])
  const list = useMemo(
    () => expandSegmentsForList(Array.isArray(segments) ? segments : []).map(safeSeg),
    [segments],
  )

  if (list.length === 0) {
    return (
      <div className="empty">
        <p>Chưa có đoạn thoại.</p>
        <p>Tải video rồi bấm Dịch toàn bộ (nhận dạng → dịch) → Lồng tiếng.</p>
      </div>
    )
  }

  return (
    <div className="segments">
      {list.map((seg) => (
        <SegmentCard
          key={seg.id}
          segment={seg}
          voices={voices}
          defaultVoice={defaultVoice}
          targetLang={targetLang}
          sourceLang={sourceLang}
          translator={translator}
          videoUrl={videoUrl}
          projectId={projectId}
          onChange={onChange}
        />
      ))}
    </div>
  )
}
