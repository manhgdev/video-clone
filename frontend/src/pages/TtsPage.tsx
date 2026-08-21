import TtsStudio from '@/features/tts/TtsStudio'

type Props = {
  voices: { id: string; name: string }[]
  onBack: () => void
  onRefreshVoices?: (lang?: string) => void
  sideOpen?: boolean
  onSideOpenChange?: (open: boolean) => void
}

export default function TtsPage({
  voices,
  onBack,
  onRefreshVoices,
  sideOpen,
  onSideOpenChange,
}: Props) {
  return (
    <TtsStudio
      voices={voices}
      onBack={onBack}
      onRefreshVoices={onRefreshVoices}
      sideOpen={sideOpen}
      onSideOpenChange={onSideOpenChange}
    />
  )
}
