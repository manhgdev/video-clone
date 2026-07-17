import TtsStudio from '@/features/tts/TtsStudio'

type Props = {
  voices: { id: string; name: string }[]
  onRefreshVoices?: (lang?: string) => void
}

export default function TtsPage({ voices, onRefreshVoices }: Props) {
  return <TtsStudio voices={voices} onRefreshVoices={onRefreshVoices} />
}
