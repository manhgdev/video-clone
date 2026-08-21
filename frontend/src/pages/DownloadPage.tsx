import DownloadStudio from '@/features/download/DownloadStudio'
import './DownloadPage.css'

type Props = {
  onBack: () => void
  onUseInClone?: (projectId: string, meta: { videoUrl: string; duration: number; segments?: unknown[]; settings?: Record<string, unknown> }) => void
}

export default function DownloadPage({ onBack, onUseInClone }: Props) {
  return (
    <div className="dl-page">
      <DownloadStudio onBack={onBack} onUseInClone={onUseInClone} />
    </div>
  )
}
