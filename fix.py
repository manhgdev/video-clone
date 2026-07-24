with open('d:/DEV/video-clone/frontend/src/features/editor/LivePreviewEditor.tsx', 'r', encoding='utf-8') as f:
    text = f.read()

target_dub = """                                 <IconHeadphones size={11} className="shrink-0 mr-1 opacity-90" />
                                 {(seg.ttsSpeed ?? 1) !== 1 ? `${seg.ttsSpeed}×` : 'TTS'}
                               </button>"""

replacement_dub = """                                 <AudioWaveformBg />
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
                               </button>"""

target_bg = """                                 <span className="truncate pointer-events-none">{baseLabel}</span>
                                 <span
                                   className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                                   onPointerDown={(e) => beginMediaDrag(e, 'bg', clip, 'end')}
                                 />"""

replacement_bg = """                                 <AudioWaveformBg />
                                 <span className="truncate pointer-events-none relative z-10">{baseLabel}</span>
                                 <VolumeSlider
                                   initialVolume={settings.originalAudioVolume ?? 100}
                                   maxVolume={100}
                                   onChangeEnd={(v) => {
                                     if (v !== (settings.originalAudioVolume ?? 100)) {
                                       onSettings({ ...settings, originalAudioVolume: v })
                                     }
                                   }}
                                 />
                                 <span
                                   className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                                   onPointerDown={(e) => beginMediaDrag(e, 'bg', clip, 'end')}
                                 />"""

text = text.replace(target_dub, replacement_dub)
text = text.replace(target_bg, replacement_bg)

with open('d:/DEV/video-clone/frontend/src/features/editor/LivePreviewEditor.tsx', 'w', encoding='utf-8') as f:
    f.write(text)
print('Done!')
