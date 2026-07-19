import DownloadStudio from '@/features/download/DownloadStudio'
import './DownloadPage.css'

type Props = {
  onUseInClone?: (projectId: string, meta: { videoUrl: string; duration: number }) => void
}

export default function DownloadPage({ onUseInClone }: Props) {
  return (
    <div className="dl-page">
      <DownloadStudio onUseInClone={onUseInClone} />
    </div>
  )
}
