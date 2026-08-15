import { useMemo } from 'react'
import type { JobStatus, ProjectSettings, Segment } from '@/features/project/project.types'
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
  logoDetection?: JobStatus['logoDetection']
  coverLogo?: boolean
  hiddenLogoTexts?: string[]
  onCoverLogoChange?: (label: string, covered: boolean) => void
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
  logoDetection,
  coverLogo = false,
  hiddenLogoTexts = [],
  onCoverLogoChange,
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
      {(() => {
        const rawLabels = [
          logoDetection?.text,
          ...(logoDetection?.tracks || []).map((track) => track.text),
        ]
          .map((text) => text?.trim())
          .filter((text): text is string => Boolean(text))
        // Watermark OCR may misread one CJK glyph on a moving handle.  Display
        // the most frequently observed handle once, not several near-duplicates.
        const handles = rawLabels.filter((text) => text.startsWith('@'))
        const handle = handles.length
          ? Array.from(new Set(handles)).sort(
              (a, b) => handles.filter((text) => text === b).length - handles.filter((text) => text === a).length,
            )[0]
          : undefined
        // OCR sees the final + as 十 on some frames.  Both are the same
        // AI-generated watermark, never two separate controls.
        const generated = rawLabels.some((text) => text.includes('生成'))
        const labels = [
          ...(generated ? ['AI生成+'] : []),
          ...Array.from(
            new Set(
              rawLabels.filter((text) => !text.startsWith('@') && !text.includes('生成')),
            ),
          ),
          ...(handle ? [handle] : []),
        ]
        if (!labels.length) return null
        const isExcluded = (label: string) =>
          hiddenLogoTexts.includes(label) ||
          (label.startsWith('@') && hiddenLogoTexts.some((text) => text.startsWith('@'))) ||
          (label.includes('生成') && hiddenLogoTexts.some((text) => text.includes('生成')))
        return (
          <div className="logo-list" aria-label="Logo phát hiện trong video">
            {labels.map((label) => {
              const dynamic = label.startsWith('@')
              // Handle nền tảng chạy quanh khung hình: không thể che đúng nếu
              // chỉ dùng bbox OCR theo frame, nên mặc định luôn tắt.
              const covered = !dynamic && coverLogo && !isExcluded(label)
              return (
              <section className={`logo-summary${dynamic ? ' logo-summary--dynamic' : ''}`} key={label}>
                <div className="logo-summary__title">Logo</div>
                <div className="logo-summary__content">
                  <span className="logo-summary__caption">
                    {dynamic ? 'Watermark động · không che tự động' : 'Watermark cố định · sẽ che khi xuất'}
                  </span>
                  <strong title={label}>{label}</strong>
                </div>
                <label className="logo-summary__cover">
                  <input
                    type="checkbox"
                    checked={covered}
                    disabled={dynamic}
                    onChange={(event) => onCoverLogoChange?.(label, event.target.checked)}
                    aria-label={dynamic ? `Không che tự động logo động ${label}` : `Che logo ${label} trong video`}
                  />
                  <span>{dynamic ? 'Không che tự động' : 'Che khi xuất'}</span>
                </label>
              </section>
              )
            })}
          </div>
        )
      })()}
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
