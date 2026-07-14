import type { Step } from '../types'
import {
  IconCheck,
  IconHeadphones,
  IconMic,
  IconPublish,
  IconTranslate,
  IconVideo,
} from './Icons'
import './Stepper.css'

type Props = {
  step: Step
  canDub: boolean
  canExport: boolean
  onDub: () => void
  onPreviewEditor: () => void
  onExport: () => void
}

export default function Stepper({
  step,
  canDub,
  canExport,
  onDub,
  onPreviewEditor,
  onExport,
}: Props) {
  const steps = [
    { id: 'video' as const, label: 'Video', Icon: IconVideo },
    { id: 'asr' as const, label: 'Nhận dạng', Icon: IconMic },
    { id: 'translate' as const, label: 'Dịch thuật', Icon: IconTranslate },
    { id: 'dub' as const, label: 'Lồng tiếng', Icon: IconHeadphones },
    { id: 'export' as const, label: 'Xuất bản', Icon: IconPublish },
  ]
  const idx = Math.max(
    0,
    steps.findIndex((s) => s.id === step),
  )

  return (
    <div className="stepper-wrap">
      <ol className="stepper">
        {steps.map((s, i) => {
          const state = i < idx ? 'done' : i === idx ? 'current' : ''
          const Icon = s.Icon
          return (
            <li key={s.id} className={state}>
              {i > 0 && <span className="line" aria-hidden />}
              <span className="bubble">
                {i < idx ? <IconCheck size={14} /> : <Icon size={14} />}
              </span>
              <span className="label">{s.label}</span>
            </li>
          )
        })}
      </ol>
      <div className="step-actions">
        <button type="button" disabled={!canDub} onClick={onDub} title="Tạo giọng đọc cho từng đoạn">
          <IconHeadphones size={14} />
          Lồng tiếng
        </button>
        <button
          type="button"
          className={canExport && (step === 'translate' || step === 'dub') ? 'solid pulse' : undefined}
          disabled={!canExport}
          onClick={onPreviewEditor}
          title="Xem video nguồn với bản dịch theo timeline và sửa trực tiếp — xuất bản từ đây"
        >
          <IconVideo size={14} />
          Xem trước & sửa
        </button>
        {/* ponytail: tạm ẩn Xuất bản ngoài — xuất từ editor mới đúng WYSIWYG; hiện lại khi ổn định */}
        {false && (
          <button
            type="button"
            className="solid"
            disabled={!canExport}
            onClick={() => onExport()}
            title="Xuất video (che chữ / chèn dịch theo tùy chọn sidebar)"
          >
            <IconPublish size={14} />
            Xuất bản
          </button>
        )}
      </div>
    </div>
  )
}
