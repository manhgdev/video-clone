with open('d:/DEV/video-clone/frontend/src/features/editor/LivePreviewEditor.tsx', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_dub = """                               >
                                 <AudioWaveformBg />
                                 <div className="relative z-10 flex items-center h-full pointer-events-none truncate mr-4">
                                   <IconHeadphones size={11} className="shrink-0 mr-1 opacity-90" />
                                   {(seg.ttsSpeed ?? 1) !== 1 ? `${seg.ttsSpeed}×` : 'TTS'}
                                 </div>
                                 <VolumeSlider
                                   initialVolume={seg.ttsVolume ?? 100}
                                   maxVolume={200}
                                   onChangeEnd={(v) => {
                                     if (v !== (seg.ttsVolume ?? 100)) {
                                       onChange([{ id: seg.id, ttsVolume: v }])
                                     }
                                   }}
                                 />
                               </button>
"""

lines[7136:7141] = [new_dub]

with open('d:/DEV/video-clone/frontend/src/features/editor/LivePreviewEditor.tsx', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('Replaced dub block')
