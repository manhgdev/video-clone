import type { ProjectSettings, Segment } from '../types'
import SegmentCard from './SegmentCard'
import './SegmentList.css'

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
  if (segments.length === 0) {
    return (
      <div className="empty">
        <p>Chưa có đoạn thoại.</p>
        <p>Tải video rồi bấm Dịch toàn bộ (nhận dạng → dịch) → Lồng tiếng.</p>
      </div>
    )
  }

  return (
    <div className="segments">
      {segments.map((seg) => (
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
