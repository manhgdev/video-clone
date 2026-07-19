import TtsStudio from '@/features/tts/TtsStudio'

type Props = {
  voices: { id: string; name: string }[]
  onRefreshVoices?: (lang?: string) => void
  sideOpen?: boolean
  onSideOpenChange?: (open: boolean) => void
}

export default function TtsPage({
  voices,
  onRefreshVoices,
  sideOpen,
  onSideOpenChange,
}: Props) {
  return (
    <TtsStudio
      voices={voices}
      onRefreshVoices={onRefreshVoices}
      sideOpen={sideOpen}
      onSideOpenChange={onSideOpenChange}
    />
  )
}
