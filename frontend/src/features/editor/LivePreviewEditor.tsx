import ProgressPopup from '@/shared/components/ProgressPopup'
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import type { JobStatus, ProjectSettings, Segment, TextOverlay } from '@/features/project/project.types'
import { api } from '@/features/project/project.api'
import { IconHeadphones } from '@/shared/components/Icons'
import { cn } from '@/shared/lib/cn'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle, useDefaultLayout } from '@/shared/ui/resizable'
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, arrayMove, horizontalListSortingStrategy } from '@dnd-kit/sortable'
import { fitOverlayFontPx, layoutOcrOverlay, midInsideVerticalWatermark } from '@/features/editor/ocrOverlayLayout'
import { ExportModal, type ExportModalOptions } from '@/features/editor/ExportModal'
import { expandCompoundShell } from '@/features/project/expandCompound'
import { generateLogoKeyframes, logoFrame } from '@/features/editor/lib/logoMotion'
import { AudioWaveformBg, VolumeSlider } from './timeline/AudioWaveformBg'
import {
  type AssetsTab,
  type CtxMenu,
  type EditorSnap,
  type MediaClip,
  type PixelBox,
  type PropTab,
  type SnapGuides,
  type TrackId,
  ASPECT_PRESETS,
  ASSET_TABS,
  AspectIcon,
  aspectWindowNorm,
  centeredAspectCrop,
  BOOKMARK_EPS,
  CAPTION_LANE_DEFS,
  EFFECT_PRESETS,
  FONT_SIZES,
  HISTORY_MAX,
  MIN_CLIP_SEC,
  PX_PER_SEC_BASE,
  SPLIT_EDGE,
  TabSvg,
  TimelineFilmstrip,
  ZOOM_MAX,
  ZOOM_MIN,
  adaptiveCoverLayout,
  autoFontFromBbox,
  fitFixedCoverCaption,
  buildExportSegments,
  captionChromeStyle,
  captionFontCss,
  captionFontStyle,
  captionLaneOf,
  captionPlacement,
  clampCoverBox,
  clipAtTime,
  cloneSnap,
  coverMaskPreviewStyle,
  defaultTrackMute,
  dubClipSeconds,
  dubPlaybackSpeed,
  effectiveOverlayLayout,
  emptyTrackFlags,
  expandSegmentsForPlayback,
  fallbackCoverBox,
  fitTimelineZoom,
  formatTime,
  formatTimecode,
  fullMediaClip,
  isOcrOverlayLayout,
  loadBookmarks,
  loadMediaClips,
  mapTimeAfterRipple,
  mergeTimeRanges,
  normalizeMediaClips,
  overlayCoverSeed,
  overlayDisplayFontStyle,
  overlayTextEnabled,
  persistBookmarks,
  persistMediaClips,
  pickTimelineSeg,
  fileBakedSpeed,
  formatSpeedX,
  speedStatusLines,
  appliedFileSpeed,
  previewVideoRate,
  reindexSegments,
  resolveCaptionFontSize,
  resolveCoverMaskOnly,
  resolveCropRect,
  resolveBelowAboveLayout,
  resolveOverLayout,
  resolveOverlayFontPreferred,
  resolvePreviewOverLayout,
  resolveSegmentCover,
  rippleDeleteMediaClips,
  rippleShiftOverlay,
  rippleShiftSegment,
  seedCoverBox,
  segmentAt,
  segmentAtCover,
  segmentHasDub,
  segmentWithLayout,
  setMeasureFontFamily,
  segmentsAt,
  speedSegmentAt,
  trimSegmentsForVideoRight,
  solidMidAt,
  solidOcrAt,
  solidOverlaysAt,
  sourceToDisplayStyle,
  splitMediaList,
  videoCropStyle,
  withInferredLayout,
  PanelView,
  TrackCtrl,
  CtxItem,
  CtxSep,
  TlButton,
} from '@/features/editor/lib'
import {
  PANEL_SIZES,
  SortablePanel,
  TIMELINE_TOOLS_STORAGE_KEY,
  loadCaptionFont,
  loadTimelineTool,
  type PanelId,
} from '@/features/editor/panelLayout'
import { useSpeedTransaction } from '@/features/editor/useSpeedTransaction'
import { useDubAudioSync } from '@/features/editor/useDubAudioSync'
import { useTimelineDrag } from '@/features/editor/useTimelineDrag'
import { EditorPropertiesPanel } from '@/features/editor/EditorPropertiesPanel'

type Props = {
  videoUrl: string
  /** Độ dài file nguồn (giây) */
  mediaDuration?: number
  /** Clip lần dịch (giây); >0 = chỉ làm việc trong cửa sổ đó (preview N giây) */
  workClipSec?: number
  /** workVideo đã bake chậm 0.80× (preferVideo) → không playbackRate thêm */
  bakedPreferVideo?: boolean
  /** Tốc độ đã bake vào file preview */
  bakedSpeed?: number
  hasBakedSpeed?: boolean
  projectId: string
  segments: Segment[]
  settings: ProjectSettings
  /** Watermark OCR tracks from the same project run as the exporter. */
  logoDetection?: JobStatus['logoDetection']
  voices: { id: string; name: string }[]
  busy: boolean
  /** Tiến độ job (lồng tiếng / xuất…) — hiện % trên track như Âm gốc */
  jobStep?: string
  jobProgress?: number
  jobMessage?: string
  /** Tạo TTS toàn bộ (track Lồng tiếng trống → bấm) */
  onDub?: () => void
  onBack: () => void
  onChange: (segment: Segment) => void | Promise<void>
  /** Thay cả list (split / duplicate / delete caption). persist:false = chỉ UI (compound API đã ghi meta). */
  onSegmentsReplace: (
    segments: Segment[],
    opts?: { persist?: boolean },
  ) => void | Promise<void>
  /** Sau bake tốc độ preview toàn bộ */
  onPreviewRebaked?: (res: {
    segments: Segment[]
    overlays?: TextOverlay[]
    workClipSec: number
    duration: number
    bakedPreferVideo: boolean
    bakedSpeed: number
    videoUrl: string
    timeScale?: number
    prevBakedSpeed?: number
  }) => void
  /** Undo bake: chỉ đổi workVideo/URL, giữ segments từ history (persist trước khi đổi file) */
  onRestoreBakedSpeed?: (speed: number, segments?: Segment[]) => void | Promise<void>
  onExport: (segments?: Segment[], exportEndSec?: number, exportStartSec?: number, renderName?: string, settingsOverride?: Partial<ProjectSettings>, coverDataUrl?: string) => void | Promise<void>
  onSettings: (settings: ProjectSettings) => void
  overlays: TextOverlay[]
  onOverlayChange: (overlay: TextOverlay, isNew?: boolean) => void
  onOverlayDelete: (overlayId: string) => void
  onOverlaysReplace: (overlays: TextOverlay[]) => void | Promise<void>
}

export default function LivePreviewEditor({
  videoUrl,
  mediaDuration: mediaDurationProp,
  workClipSec = 0,
  bakedPreferVideo = false,
  bakedSpeed = 1,
  hasBakedSpeed = false,
  projectId,
  segments,
  settings,
  logoDetection,
  voices,
  busy,
  jobStep = '',
  jobProgress = 0,
  jobMessage: _jobMessage = '',
  onDub,
  onBack,
  onChange,
  onSegmentsReplace,
  onPreviewRebaked,
  onRestoreBakedSpeed,
  onExport,
  onSettings,
  overlays,
  onOverlayChange,
  onOverlayDelete,
  onOverlaysReplace,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const dubAudioRef = useRef<HTMLAudioElement | null>(null)
  const bgAudioRef = useRef<HTMLAudioElement | null>(null)
  const dubTokenRef = useRef('')
  /** id đoạn đã đọc xong (audio.ended) — không restart đến khi tua ra khỏi cửa sổ */
  const dubFinishedIdsRef = useRef<Set<string>>(new Set())
  /** Tua video / đổi đoạn → hard sync TTS; còn lại để audio free-run (tránh ngắt vì seek mỗi timeupdate). */
  const dubHardSyncRef = useRef(false)
  const videoMutedForDubRef = useRef(false)
  const trackRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const previewRef = useRef<HTMLDivElement>(null)
  const rulerScrollRef = useRef<HTMLDivElement>(null)
  const tracksScrollRef = useRef<HTMLDivElement>(null)
  const labelsScrollRef = useRef<HTMLDivElement>(null)
  const tracksColRef = useRef<HTMLDivElement>(null)
  const syncingYRef = useRef(false)
  const bboxDraftRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null)
  const draftRef = useRef<{ id: string; start: number; end: number } | null>(null)
  /** draft multi-move: id → {start,end} */
  const groupDraftRef = useRef<Record<string, { start: number; end: number }> | null>(null)

  // Kéo panel editor → localStorage (mở lại không reset)
  const mainLayout = useDefaultLayout({
    id: 'videoclone.editor.main',
    storage: typeof localStorage !== 'undefined' ? localStorage : undefined,
    panelIds: ['main', 'timeline'],
  })
  const sideLayout = useDefaultLayout({
    id: 'videoclone.editor.sides',
    storage: typeof localStorage !== 'undefined' ? localStorage : undefined,
    panelIds: ['tools', 'preview', 'properties'],
  })

  // Panel order — persist to localStorage
  const [panelOrder, setPanelOrder] = useState<PanelId[]>(() => {
    try {
      const saved = localStorage.getItem('videoclone.panel-order')
      if (saved) {
        const p = JSON.parse(saved) as unknown[]
        if (Array.isArray(p) && p.length === 3 && p.every((x) => ['tools','preview','properties'].includes(x as string)))
          return p as PanelId[]
      }
    } catch { /* ignore */ }
    return ['tools', 'preview', 'properties']
  })

  const outerLayout = useDefaultLayout({
    id: 'videoclone.editor.outer',
    storage: typeof localStorage !== 'undefined' ? localStorage : undefined,
    panelIds: ['left-col', 'right-col'],
  })

  // Layout preset: 'vertical' = preview cột phải full height; 'default' = preview giữa inline
  const [layoutPreset, setLayoutPreset] = useState<'vertical' | 'default'>(() => {
    try { return (localStorage.getItem('videoclone.layout-preset') as 'vertical' | 'default') || 'vertical' } catch { return 'vertical' }
  })
  const [showLayoutMenu, setShowLayoutMenu] = useState(false)

  // Portal targets — 2 containers, chọn theo layoutPreset
  const [rightColPortalEl, setRightColPortalEl] = useState<HTMLDivElement | null>(null)
  const [inlinePortalEl, setInlinePortalEl] = useState<HTMLDivElement | null>(null)
  const previewPortalEl = layoutPreset === 'vertical' ? rightColPortalEl : inlinePortalEl

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }))

  function handlePanelDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const next = arrayMove(panelOrder, panelOrder.indexOf(active.id as PanelId), panelOrder.indexOf(over.id as PanelId))
    setPanelOrder(next)
    try { localStorage.setItem('videoclone.panel-order', JSON.stringify(next)) } catch { /* ignore */ }
  }

  const [time, setTime] = useState(0)
  const layoutCacheRef = useRef<Record<string, { key: string; val: any }>>({})
  const fontApplySeqRef = useRef(0)
  const [, setCaptionFontsReady] = useState(0)
  useEffect(() => { layoutCacheRef.current = {} }, [projectId])
  const captionFontLoadKey = [...new Set([
    settings.subtitleFontFamily || 'system',
    ...segments.map((seg) => seg.fontFamily || settings.subtitleFontFamily || 'system'),
  ])].sort().join('|')
  useEffect(() => {
    let active = true
    void Promise.all(captionFontLoadKey.split('|').map((family) => loadCaptionFont(family))).then(() => {
      if (!active) return
      layoutCacheRef.current = {}
      setCaptionFontsReady((tick) => tick + 1)
    })
    return () => { active = false }
  }, [captionFontLoadKey])

  const [duration, setDuration] = useState(() =>
    Number.isFinite(mediaDurationProp) && (mediaDurationProp ?? 0) > 0 ? mediaDurationProp! : 0,
  )

  useEffect(() => {
    if (Number.isFinite(mediaDurationProp) && (mediaDurationProp ?? 0) > 0) {
      setDuration(mediaDurationProp!)
    }
  }, [mediaDurationProp])

  useEffect(() => {
    if (settings.defaultVoice) setGlobalVoice((v) => v || settings.defaultVoice)
  }, [settings.defaultVoice])
  const [videoSize, setVideoSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    setVideoSize({ width: 0, height: 0 })
  }, [videoUrl])
  const [cropEditing, setCropEditing] = useState(false)
  const [cropDraft, setCropDraft] = useState(() => settings.previewCrop ?? { x: 0.1, y: 0.1, w: 0.8, h: 0.8 })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  /** Multi-select caption (Ctrl/Shift / marquee) */
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  /** Multi-select media clips (video/bg) + TTS clips từ marquee */
  const [selectedMediaIds, setSelectedMediaIds] = useState<string[]>([])
  const [selectedDubIds, setSelectedDubIds] = useState<string[]>([])
  /** Kéo khung chọn trên timeline (OpenCut marquee) — px relative tracks scroll content */
  const [marquee, setMarquee] = useState<{
    x0: number
    y0: number
    x1: number
    y1: number
  } | null>(null)
  const marqueeRef = useRef<{
    x0: number
    y0: number
    x1: number
    y1: number
    additive: boolean
    active: boolean
  } | null>(null)
  const [ttsBusy, setTtsBusy] = useState(false)
  const [ttsError, setTtsError] = useState<string | null>(null)
  /** Draft TTS toàn cục khi không chọn đoạn — Áp dụng cho tất cả */
  const [globalTtsVolume, setGlobalTtsVolume] = useState(100)
  const [globalTtsSpeed, setGlobalTtsSpeed] = useState(1)
  // Panel «tất cả» phản chiếu tốc độ thật của các câu. Flow «ưu tiên 0.8»
  // (matchDuration=preferVideo): khe đã co khi nâng 1× → mặc định 1.20 NGAY
  // từ trước khi dub — nút 1.2× sáng, dub dùng đúng 1.2; mode khác giữ 1×.
  useEffect(() => {
    const vals = segments
      .filter((s) => typeof s.ttsSpeed === 'number' && s.ttsSpeed > 0)
      .map((s) => s.ttsSpeed as number)
    if (!vals.length) {
      setGlobalTtsSpeed(settings.matchDuration === 'preferVideo' ? 1.1 : 1)
      return
    }
    const freq = new Map<number, number>()
    for (const v of vals) freq.set(v, (freq.get(v) ?? 0) + 1)
    const common = [...freq.entries()].sort((a, b) => b[1] - a[1])[0][0]
    setGlobalTtsSpeed(common)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments, settings.matchDuration])
  const [globalVoice, setGlobalVoice] = useState(() => settings.defaultVoice || '')
  const [stemStatus, setStemStatus] = useState<'off' | 'loading' | 'ready' | 'error'>('off')
  const [stemProgress, setStemProgress] = useState(0)
  const [stemError, setStemError] = useState<string | null>(null)
  const [stemRetry, setStemRetry] = useState(0)
  const [trackMute, setTrackMute] = useState(defaultTrackMute)
  const [trackHidden, setTrackHidden] = useState(emptyTrackFlags)
  const [trackLocked, setTrackLocked] = useState(emptyTrackFlags)
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null)
  const [draft, setDraft] = useState<{ id: string; start: number; end: number } | null>(null)
  const [groupDraft, setGroupDraft] = useState<Record<string, { start: number; end: number }> | null>(null)
  const [bboxDraft, setBboxDraft] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [draggingBox, setDraggingBox] = useState(false)
  const [snapGuides, setSnapGuides] = useState<SnapGuides>({ h: false, v: false })
  /** true khi đang kéo pan aspect / crop tự do — hiện tia căn giữa giống bbox */
  const [panningCrop, setPanningCrop] = useState(false)
  const [selectedOverlayId, setSelectedOverlayId] = useState<string | null>(null)
  // Avoid repeatedly creating the same automatic watermark while the parent
  // persists the new overlay after OCR data arrives.
  const autoWatermarkCreateRef = useRef('')
  const legacyWatermarkSyncRef = useRef('')
  /** Track đang focus — click Caption ≠ TTS ≠ Âm gốc ≠ Text */
  const [trackFocus, setTrackFocus] = useState<'video' | 'caption' | 'dub' | 'bg' | 'watermark' | 'text'>('video')
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(null)
  const [videoClips, setVideoClips] = useState<MediaClip[]>([])
  const [bgClips, setBgClips] = useState<MediaClip[]>([])

  const wantNoVocals =
    settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals'
  const muteOriginal =
    settings.processOriginalAudio &&
    (settings.originalAudioMode === 'mute' || settings.originalAudioMode === 'no_vocals')

  const videoSourceStart = videoClips.length === 1 ? (videoClips[0].sourceStart ?? 0) : 0
  const timelineToVideoTime = (value: number) => value + videoSourceStart
  const videoToTimelineTime = (value: number) => Math.max(0, value - videoSourceStart)

  const { syncOriginalBg, pauseDubAudio, syncDubAudio } = useDubAudioSync({
    segments,
    settings,
    bakedSpeed,
    bakedPreferVideo,
    hasBakedSpeed,
    wantNoVocals,
    muteOriginal,
    stemStatus,
    dubMuted: trackMute.dub,
    videoToTimelineTime,
    videoRef,
    bgAudioRef,
    dubAudioRef,
    dubTokenRef,
    dubFinishedIdsRef,
    dubHardSyncRef,
    videoMutedForDubRef,
  })

  const {
    speedDraft,
    setSpeedDraft,
    speedBusy,
    speedCancelling,
    speedError,
    setSpeedError,
    speedProgress,
    speedMessage,
    applyVideoSpeed,
    cancelVideoSpeed,
  } = useSpeedTransaction({
    projectId,
    busy,
    segments,
    matchDuration: settings.matchDuration,
    bakedSpeed,
    bakedPreferVideo,
    hasBakedSpeed,
    wantNoVocals,
    time,
    setTime,
    videoClips,
    bgClips,
    setVideoClips,
    setBgClips,
    videoRef,
    bgAudioRef,
    dubHardSyncRef,
    dubFinishedIdsRef,
    dubTokenRef,
    pushHistory,
    pauseDubAudio,
    onSegmentsReplace,
    onPreviewRebaked,
  })

  const [tool, setTool] = useState<'select' | 'cover' | 'text'>('select')
  const [mainTrackMagnet, setMainTrackMagnet] = useState(() => loadTimelineTool('mainTrackMagnet'))
  const [autoSnapping, setAutoSnapping] = useState(() => loadTimelineTool('autoSnapping'))
  const [mediaLinked, setMediaLinked] = useState(() => loadTimelineTool('mediaLinked'))
  const [zoom, setZoom] = useState(1)
  const zoomTouchedRef = useRef(false)
  const [scrollLeft, setScrollLeft] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [bookmarks, setBookmarks] = useState<number[]>(() => loadBookmarks(projectId))
  const [histTick, setHistTick] = useState(0)
  const pastRef = useRef<EditorSnap[]>([])
  const futureRef = useRef<EditorSnap[]>([])
  const historyQuietRef = useRef(false)

  useEffect(() => {
    try {
      localStorage.setItem(TIMELINE_TOOLS_STORAGE_KEY, JSON.stringify({ mainTrackMagnet, autoSnapping, mediaLinked }))
    } catch { /* Trình duyệt chặn storage: vẫn giữ trạng thái trong phiên hiện tại. */ }
  }, [mainTrackMagnet, autoSnapping, mediaLinked])
  const [assetsTab, setAssetsTab] = useState<AssetsTab>('media')
  const [propTab, setPropTab] = useState<PropTab>('caption')
  const [fontSizeDraft, setFontSizeDraft] = useState(0)
  const [aspectMenuOpen, setAspectMenuOpen] = useState(false)
  const aspectMenuRef = useRef<HTMLDivElement>(null)
  /** Preview canvas zoom: fit = vừa khung; số = scale so với fit */
  const [previewZoom, setPreviewZoom] = useState<'fit' | number>('fit')
  const [fitMenuOpen, setFitMenuOpen] = useState(false)
  const fitMenuRef = useRef<HTMLDivElement>(null)
  const logoFileRef = useRef<HTMLInputElement>(null)
  const [logoDraft, setLogoDraft] = useState<TextOverlay | null>(null)
  const [logoDraftBase, setLogoDraftBase] = useState<TextOverlay | null>(null)
  const [logoDraftFile, setLogoDraftFile] = useState<File | null>(null)
  const [logoApplying, setLogoApplying] = useState(false)
  const [logoError, setLogoError] = useState<string | null>(null)
  const PREVIEW_ZOOM_PRESETS = [0.25, 0.5, 0.75, 1, 1.5, 2] as const
  const pxPerSec = PX_PER_SEC_BASE * zoom

  useEffect(() => {
    setBookmarks(loadBookmarks(projectId))
    pastRef.current = []
    futureRef.current = []
    zoomTouchedRef.current = false
    setVideoClips([])
    setBgClips([])
    setSelectedMediaId(null)
    setTrackMute(defaultTrackMute())
    setTrackHidden(emptyTrackFlags())
    setTrackLocked(emptyTrackFlags())
    setHistTick((n) => n + 1)
  }, [projectId])

  useEffect(() => {
    persistBookmarks(projectId, bookmarks)
  }, [projectId, bookmarks])

  useEffect(() => {
    persistMediaClips(projectId, 'video', videoClips)
  }, [projectId, videoClips])

  useEffect(() => {
    persistMediaClips(projectId, 'bg', bgClips)
  }, [projectId, bgClips])

  /** Tốc độ bake file (remap/API). Soft preferVideo không dùng — chỉ sau Áp dụng. */
  function effectiveBakedSpeed(): number {
    return fileBakedSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed)
  }

  const speedStatus = useMemo(
    () => speedStatusLines(
      settings.matchDuration,
      speedDraft,
      bakedSpeed,
      bakedPreferVideo,
      hasBakedSpeed,
    ),
    [settings.matchDuration, speedDraft, bakedSpeed, bakedPreferVideo, hasBakedSpeed],
  )
  const appliedSpeedX = useMemo(
    () => appliedFileSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed),
    [bakedSpeed, bakedPreferVideo, hasBakedSpeed],
  )

  function takeSnap(): EditorSnap {
    const durNow =
      Number.isFinite(duration) && duration > 0
        ? duration
        : Number.isFinite(mediaDurationProp) && (mediaDurationProp ?? 0) > 0
          ? (mediaDurationProp as number)
          : 0
    return cloneSnap({
      segments,
      overlays,
      settings,
      bookmarks,
      selectedId,
      selectedOverlayId,
      trackFocus,
      videoClips,
      bgClips,
      selectedMediaId,
      bakedSpeed: effectiveBakedSpeed(),
      workClipSec: workClipSec > 0 ? workClipSec : 0,
      mediaDuration: durNow,
    })
  }

  function pushHistory() {
    if (historyQuietRef.current) return
    pastRef.current.push(takeSnap())
    if (pastRef.current.length > HISTORY_MAX) pastRef.current.shift()
    futureRef.current = []
    setHistTick((n) => n + 1)
  }

  /** Ghi history 1 lần / chuỗi thao tác (kéo pan, kéo bbox…). */
  function pushHistoryOnce(gate: { current: boolean }) {
    if (gate.current || historyQuietRef.current) return
    gate.current = true
    pushHistory()
  }

  /** Debounce history khi gõ text liên tục (cùng field). */
  const textHistRef = useRef<{ key: string; t: number }>({ key: '', t: 0 })

  /** Mọi sửa 1 segment / overlay → undo được (Ctrl+Z). */
  function editSegment(next: Segment, opts?: { textField?: string; skipHistory?: boolean }) {
    if (!historyQuietRef.current && !opts?.skipHistory) {
      if (opts?.textField) {
        const key = `${next.id}:${opts.textField}`
        const now = Date.now()
        if (textHistRef.current.key !== key || now - textHistRef.current.t > 600) {
          pushHistory()
          textHistRef.current = { key, t: now }
        }
      } else {
        pushHistory()
        textHistRef.current = { key: '', t: 0 }
      }
    }
    return onChange(next)
  }

  function editOverlay(overlay: TextOverlay, isNew?: boolean, opts?: { textField?: boolean; skipHistory?: boolean }) {
    if (!historyQuietRef.current && !opts?.skipHistory) {
      if (opts?.textField) {
        const key = `ov:${overlay.id}:text`
        const now = Date.now()
        if (textHistRef.current.key !== key || now - textHistRef.current.t > 600) {
          pushHistory()
          textHistRef.current = { key, t: now }
        }
      } else {
        pushHistory()
        textHistRef.current = { key: '', t: 0 }
      }
    }
    return onOverlayChange(overlay, isNew)
  }

  function editSettings(next: ProjectSettings) {
    if (!historyQuietRef.current) {
      pushHistory()
      textHistRef.current = { key: '', t: 0 }
    }
    onSettings(next)
  }

  function applySnap(snap: EditorSnap) {
    historyQuietRef.current = true
    const curBake = effectiveBakedSpeed()
    const wantBake = snap.bakedSpeed > 0.2 ? snap.bakedSpeed : 1
    const bakeChanges = Math.abs(curBake - wantBake) > 0.008 && Boolean(onRestoreBakedSpeed)
    // Timeline/caption/TTS timing lấy từ snapshot (đã scale đúng lúc bake).
    // Server PHẢI mirror editor sau undo — không persist thì baseline/segments
    // trên server còn ở lineage cũ → lần Áp dụng tốc độ sau remap ra timeline
    // khác hẳn. Khi đổi bake: PUT tuần tự bên onRestoreBakedSpeed (tránh race).
    void onSegmentsReplace(snap.segments, { persist: !bakeChanges })
    void onOverlaysReplace(snap.overlays)
    onSettings(snap.settings)
    setBookmarks(snap.bookmarks)
    setSelectedId(snap.selectedId)
    setSelectedIds(snap.selectedId ? [snap.selectedId] : [])
    setSelectedOverlayId(snap.selectedOverlayId)
    setTrackFocus(snap.trackFocus)
    setVideoClips(snap.videoClips)
    setBgClips(snap.bgClips)
    setSelectedMediaId(snap.selectedMediaId)
    setSpeedDraft(wantBake)
    const finish = () => {
      historyQuietRef.current = false
      setHistTick((n) => n + 1)
      dubHardSyncRef.current = true
      pauseDubAudio()
    }
    // Chỉ đổi file video bake — không remap segments lại (tránh lệch history)
    if (bakeChanges && onRestoreBakedSpeed) {
      void Promise.resolve(onRestoreBakedSpeed(wantBake, snap.segments)).finally(finish)
    } else {
      queueMicrotask(finish)
    }
  }

  function undoEdit() {
    if (!pastRef.current.length || historyQuietRef.current) return
    const cur = takeSnap()
    const prev = pastRef.current.pop()!
    futureRef.current.push(cur)
    applySnap(prev)
  }

  function redoEdit() {
    if (!futureRef.current.length || historyQuietRef.current) return
    const cur = takeSnap()
    const next = futureRef.current.pop()!
    pastRef.current.push(cur)
    applySnap(next)
  }

  const canUndo = pastRef.current.length > 0 && !historyQuietRef.current
  const canRedo = futureRef.current.length > 0 && !historyQuietRef.current
  void histTick

  function toggleTrackFlag(
    setFlags: React.Dispatch<React.SetStateAction<Record<TrackId, boolean>>>,
    id: TrackId,
  ) {
    setFlags((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const ctxMenuRef = useRef<HTMLDivElement | null>(null)

  function openCtxMenu(menu: CtxMenu, event: React.MouseEvent) {
    event.preventDefault()
    event.stopPropagation()
    // Snapshot multi-select lúc mở menu (tránh mất khi RMB/focus)
    let next: CtxMenu = { ...menu, x: event.clientX, y: event.clientY }
    if (next.kind === 'segment' || next.kind === 'dub') {
      const snap = expandGroupSelection([
        ...new Set([
          ...(next.ids || []),
          ...selectedIds,
          ...selectedDubIds,
          next.segId,
        ]),
      ])
      next = { ...next, ids: snap }
      // Giữ highlight multi trên timeline khi mở menu
      if (snap.length >= 2) {
        setSelectedIds(snap)
        setSelectedId(next.segId)
        setSelectedDubIds(snap.filter((id) =>
          segments.some((s) => s.id === id && segmentHasDub(s) && s.audioUrl),
        ))
      }
    }
    setCtxMenu(next)
  }

  useLayoutEffect(() => {
    if (!ctxMenu) return
    const el = ctxMenuRef.current
    if (!el) return
    const pad = 8
    const rect = el.getBoundingClientRect()
    let x = ctxMenu.x
    let y = ctxMenu.y
    // Ưu tiên mở phía trên con trỏ khi sát đáy timeline
    if (y + rect.height > window.innerHeight - pad) {
      y = Math.max(pad, ctxMenu.y - rect.height)
    }
    if (x + rect.width > window.innerWidth - pad) {
      x = Math.max(pad, window.innerWidth - rect.width - pad)
    }
    if (y < pad) y = pad
    if (x < pad) x = pad
    if (x !== ctxMenu.x || y !== ctxMenu.y) {
      setCtxMenu({ ...ctxMenu, x, y })
    }
  }, [ctxMenu])

  function triggerDownload(url: string | undefined, filename: string) {
    if (!url) return
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  /** Tải audio theo chế độ hiện tại (gốc / xóa lời / giữ lời). */
  function downloadProjectAudio(kind?: 'original' | 'no_vocals' | 'vocals') {
    const mode = kind
      || (!settings.processOriginalAudio || settings.originalAudioMode === 'original' || settings.originalAudioMode === 'mute'
        ? 'original'
        : settings.originalAudioMode === 'no_vocals'
          ? 'no_vocals'
          : 'vocals')
    const label =
      mode === 'no_vocals' ? 'no_vocals' : mode === 'vocals' ? 'vocals' : 'original'
    triggerDownload(
      api.projectAudioDownloadUrl(projectId, mode),
      `${projectId}_${label}.wav`,
    )
  }

  // Đóng popup: LMB/RMB/pointer ngoài menu, Escape, scroll, blur
  useEffect(() => {
    if (!ctxMenu) return
    const isInside = (t: EventTarget | null) =>
      t instanceof Node && Boolean(ctxMenuRef.current?.contains(t))
    const close = () => setCtxMenu(null)
    const onPointerDown = (e: PointerEvent) => {
      if (isInside(e.target)) return
      close()
    }
    const onContextMenu = (e: MouseEvent) => {
      if (isInside(e.target)) {
        e.preventDefault()
        return
      }
      // RMB ngoài menu → đóng + chặn menu trình duyệt
      close()
      e.preventDefault()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    // capture: LMB/touch đóng ngay; RMB dùng contextmenu (không phụ thuộc target hit)
    window.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('contextmenu', onContextMenu, true)
    window.addEventListener('wheel', close, { capture: true, passive: true })
    window.addEventListener('keydown', onKey)
    window.addEventListener('blur', close)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('contextmenu', onContextMenu, true)
      window.removeEventListener('wheel', close, true)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('blur', close)
    }
  }, [ctxMenu])

  function syncFollowers() {
    const scrl = tracksScrollRef.current
    if (!scrl) return
    setScrollLeft(scrl.scrollLeft)
    if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = scrl.scrollLeft
    if (!syncingYRef.current && labelsScrollRef.current) {
      syncingYRef.current = true
      labelsScrollRef.current.scrollTop = scrl.scrollTop
      syncingYRef.current = false
    }
  }

  function followPlaybackPlayhead(current: number) {
    const scrl = tracksScrollRef.current
    if (!scrl || scrl.clientWidth <= 0) return
    const playhead = current * pxPerSec
    if (playhead < scrl.scrollLeft + scrl.clientWidth * 0.9) return
    const next = Math.min(
      Math.max(0, scrl.scrollWidth - scrl.clientWidth),
      Math.max(0, playhead - scrl.clientWidth * 0.1),
    )
    if (next <= scrl.scrollLeft + 0.5) return
    scrl.scrollLeft = next
    if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = next
    setScrollLeft(next)
  }

  function syncLabelsY() {
    const lab = labelsScrollRef.current
    const trk = tracksScrollRef.current
    if (!lab || !trk || syncingYRef.current) return
    syncingYRef.current = true
    trk.scrollTop = lab.scrollTop
    syncingYRef.current = false
  }

  const selected = selectedId ? segments.find((s) => s.id === selectedId) : undefined
  const lastSegment = segments[segments.length - 1]
  const videoTrackEnd = videoClips.length ? Math.max(...videoClips.map((clip) => clip.end)) : 0
  // Preview Ns → chỉ làm việc trong Ns (khớp xuất). Dịch full → cả video.
  // Ưu tiên videoTrackEnd (sau trim right) — không phình lại full source.
  const sourceDur = Number.isFinite(duration) && duration > 0 ? duration : 0
  const clipCap = workClipSec > 0 ? workClipSec : 0
  const sourceWindow = sourceDur > 0 ? Math.max(0, sourceDur - videoSourceStart) : 0
  const timelineDuration = (() => {
    let d = videoTrackEnd > 0
      ? videoTrackEnd
      : Math.max(sourceWindow, lastSegment?.end ?? 0, 1)
    if (clipCap > 0) {
      const cap = Math.max(0, clipCap - videoSourceStart)
      d = Math.min(d, cap, sourceWindow > 0 ? sourceWindow : cap)
    }
    return Math.max(d, 1)
  })()

  useEffect(() => {
    if (!playing) return
    let frame = 0
    const syncRate = () => {
      const video = videoRef.current
      if (!video || video.paused || video.ended) return
      const current = Math.max(0, video.currentTime - videoSourceStart)
      const active = speedSegmentAt(segments, current)
      const rate = previewVideoRate(
        settings.matchDuration,
        bakedPreferVideo,
        active?.videoSpeed,
        bakedSpeed,
        hasBakedSpeed,
      )
      if (Math.abs(video.playbackRate - rate) > 0.001) video.playbackRate = rate
      // ponytail: one cheap check per painted frame; switch to boundary timers only if profiling needs it.
      frame = window.requestAnimationFrame(syncRate)
    }
    frame = window.requestAnimationFrame(syncRate)
    return () => window.cancelAnimationFrame(frame)
  }, [
    playing,
    segments,
    videoSourceStart,
    settings.matchDuration,
    bakedPreferVideo,
    bakedSpeed,
    hasBakedSpeed,
  ])

  const [tracksViewportW, setTracksViewportW] = useState(0)
  // Fit mặc định 80%; slider cho phép thu nhỏ timeline xuống 30% khung.
  const zoomSliderMin = useMemo(() => {
    const w = tracksViewportW
    if (w <= 0 || timelineDuration <= 0) return ZOOM_MIN
    return fitTimelineZoom(timelineDuration, w, 0.3)
  }, [timelineDuration, tracksViewportW])
  const zoomFitValue = useMemo(() => {
    const w = tracksViewportW
    if (w <= 0 || timelineDuration <= 0) return ZOOM_MIN
    return fitTimelineZoom(timelineDuration, w)
  }, [timelineDuration, tracksViewportW])
  const zoomSliderValue = zoom <= zoomFitValue
    ? 50 * (zoom - zoomSliderMin) / Math.max(1e-6, zoomFitValue - zoomSliderMin)
    : 50 + 50 * Math.log(zoom / zoomFitValue) / Math.max(1e-6, Math.log(ZOOM_MAX / zoomFitValue))
  function zoomFromSlider(value: number) {
    const position = Math.max(0, Math.min(100, value))
    return position <= 50
      ? zoomSliderMin + (zoomFitValue - zoomSliderMin) * position / 50
      : zoomFitValue * Math.pow(ZOOM_MAX / zoomFitValue, (position - 50) / 50)
  }
  const videoSpan = timelineDuration
  const trackWidth = Math.max(1, timelineDuration * pxPerSec)
  const playheadPx = time * pxPerSec - scrollLeft
  const tickInterval = [1, 2, 5, 10, 30, 60, 120, 300, 600].find((c) => c * pxPerSec >= 80) ?? 600
  const ticks = Array.from(
    { length: Math.ceil(timelineDuration / tickInterval) + 1 },
    (_, i) => i * tickInterval,
  ).filter((t) => t <= timelineDuration + 0.001)

  // Đo viewport + fit 80% khi mở / đổi project
  useEffect(() => {
    let ro: ResizeObserver | null = null
    let raf = 0
    const applyFit = (el: HTMLElement, force = false) => {
      const w = el.clientWidth
      setTracksViewportW(w)
      if (!force && zoomTouchedRef.current) return
      if (w < 80 || timelineDuration <= 0) return
      setZoom(fitTimelineZoom(timelineDuration, w))
      setScrollLeft(0)
      el.scrollLeft = 0
      if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = 0
    }
    const bind = () => {
      const el = tracksScrollRef.current
      if (!el) {
        raf = requestAnimationFrame(bind)
        return
      }
      zoomTouchedRef.current = false
      applyFit(el, true)
      if (typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(() => applyFit(el, false))
        ro.observe(el)
      }
    }
    bind()
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
    }
  }, [timelineDuration, projectId])

  function setZoomManual(next: number | ((z: number) => number)) {
    zoomTouchedRef.current = true
    setZoom((z) => {
      const v = typeof next === 'function' ? next(z) : next
      return Math.max(zoomSliderMin, Math.min(ZOOM_MAX, v))
    })
  }

  function zoomToFit() {
    zoomTouchedRef.current = false
    const el = tracksScrollRef.current
    const w = el?.clientWidth ?? 0
    if (w > 80 && timelineDuration > 0) {
      setZoom(fitTimelineZoom(timelineDuration, w))
      setScrollLeft(0)
      if (el) el.scrollLeft = 0
      if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = 0
    }
  }

  const mediaDurRef = useRef(0)

  // Init / clamp clip Video & Âm gốc theo cửa sổ làm việc
  useEffect(() => {
    if (!(timelineDuration > 0)) return
    const prevDur = mediaDurRef.current
    mediaDurRef.current = timelineDuration
    const ensure = (prev: MediaClip[], kind: 'video' | 'bg') => {
      const raw = prev.length ? prev : loadMediaClips(projectId, kind, timelineDuration)
      return normalizeMediaClips(raw, timelineDuration, prevDur)
    }
    setVideoClips((prev) => ensure(prev, 'video'))
    setBgClips((prev) => ensure(prev, 'bg'))
  }, [timelineDuration, projectId])

  useEffect(() => {
    mediaDurRef.current = 0
  }, [projectId])

  const videoFrameReady = videoSize.width > 0 && videoSize.height > 0
  // Keep the canvas stable while metadata loads, but never infer a caption
  // lane from this display-only fallback.
  const sourceWidth = videoFrameReady ? videoSize.width : 1080
  const sourceHeight = videoFrameReady ? videoSize.height : 1920
  // Editor used to play the raw <video> without the export mask.  Keep this
  // in the preview layer so AI-generated static watermarks are visibly hidden
  // before the user presses Export.
  // `auto-watermark-ai-generated` existed briefly before watermarkSource was
  // introduced. Treat it as a watermark too so an open project migrates cleanly
  // instead of leaving the same logo in the Text lane.
  const isWatermarkOverlay = (overlay: TextOverlay) =>
    Boolean(overlay.watermarkSource) || overlay.id === 'auto-watermark-ai-generated'
  const editableWatermarks = overlays.filter(isWatermarkOverlay)
  const hasEditableWatermark = editableWatermarks.length > 0
  const activeWatermarkMasks = (settings.coverLogo && !trackHidden.watermark && !hasEditableWatermark
    ? (logoDetection?.tracks || []).filter((track) => {
        const label = (track.text || '').trim()
        const excluded = settings.hiddenLogoTexts || []
        return Boolean(track.bbox)
          && !label.startsWith('@')
          && !excluded.includes(label)
          && !(label.includes('生成') && excluded.some((text) => text.includes('生成')))
          && time >= Number(track.start || 0) - 0.04
          && time <= Number(track.end || 0) + 0.04
      })
    : [])
  const aspectId = settings.previewAspectRatio ?? 'original'
  const appliedCrop = useMemo(
    () => resolveCropRect(sourceWidth, sourceHeight, aspectId, settings.previewCrop),
    [sourceWidth, sourceHeight, aspectId, settings.previewCrop],
  )
  const crop = cropEditing
    ? { x: 0, y: 0, w: sourceWidth, h: sourceHeight }
    : appliedCrop
  const getCachedPreviewLayout = (s: Segment, override?: PixelBox) => {
    const cl = s.captionLayout
    const key = `v15|${s.id}|${s.translation}|${s.layout}|${s.bboxInherited}|${s.bbox ? `${s.bbox.x},${s.bbox.y},${s.bbox.w},${s.bbox.h}` : ''}|${cl ? `${cl.x},${cl.y},${cl.w},${cl.h},${cl.fontSize},${(cl.lines || []).join('\\n')}` : ''}|${settings.subtitleFontSize}|${s.fontFamily || settings.subtitleFontFamily}|${crop.x},${crop.y},${crop.w},${crop.h}|${override ? `${override.x},${override.y},${override.w},${override.h}` : ''}`
    const cached = layoutCacheRef.current[s.id]
    if (cached && cached.key === key) {
      return cached.val
    }
    const val = resolvePreviewOverLayout(
      s,
      settings,
      sourceWidth,
      sourceHeight,
      crop,
      override,
    )
    layoutCacheRef.current[s.id] = { key, val }
    return val
  }
  const cropPortrait = crop.h >= crop.w

  // Promote the detected static AI watermark to an ordinary effect overlay.
  // The user can now trim it on the timeline and drag/resize its bbox in the
  // preview; export consumes this exact same overlay.
  useEffect(() => {
    const tracks = (logoDetection?.tracks || []).filter((track) => {
      const label = (track.text || '').trim()
      return Boolean(track.bbox) && !label.startsWith('@') && label.includes('生成')
    })
    if (!tracks.length || hasEditableWatermark) return
    const signature = tracks.map((track) => `${track.text}:${track.start}:${track.end}`).join('|')
    if (autoWatermarkCreateRef.current === signature) return
    autoWatermarkCreateRef.current = signature
    // The detector samples several frames. A median bbox keeps a stable logo
    // box and avoids the giant union / repeated blocks shown by raw OCR data.
    const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
    const boxes = tracks.map((track) => track.bbox!).filter(Boolean)
    const x = median(boxes.map((box) => box.x))
    const y = median(boxes.map((box) => box.y))
    const w = median(boxes.map((box) => box.w))
    const h = median(boxes.map((box) => box.h))
    const overlay: TextOverlay = {
      id: 'auto-watermark-ai-generated',
      start: Math.max(0, Math.min(...tracks.map((track) => Number(track.start) || 0))),
      end: Math.max(0.04, Math.max(...tracks.map((track) => Number(track.end) || 0.04))),
      text: 'AI生成+',
      x: Math.round(x * sourceWidth),
      y: Math.round(y * sourceHeight),
      w: Math.max(8, Math.round(w * sourceWidth)),
      h: Math.max(8, Math.round(h * sourceHeight)),
      fontSize: 0,
      color: '#ffffff',
      kind: 'effect',
      // Export uses native inpainting; preview uses a responsive glass blur.
      maskStyle: 'inpaint',
      maskColor: '#101827',
      maskOpacity: 92,
      watermarkSource: 'AI生成+',
    }
    onOverlayChange(overlay, true)
  }, [hasEditableWatermark, logoDetection?.tracks, onOverlayChange, sourceHeight, sourceWidth])

  // One-shot repair for the first automatic clip created before source video
  // metadata was available. It prevents the fallback 1080×1920 coordinates
  // from producing an oversized box on a 16:9 source. The ref is crucial:
  // API round-trips must never turn this into a request loop.
  useEffect(() => {
    const legacy = overlays.find((overlay) => overlay.id === 'auto-watermark-ai-generated' && !overlay.watermarkSource)
    const tracks = (logoDetection?.tracks || []).filter((track) => (track.text || '').includes('生成') && track.bbox)
    if (!legacy || !tracks.length || !videoFrameReady) return
    const signature = `${projectId}:${sourceWidth}x${sourceHeight}:${tracks.map((track) => `${track.start}:${track.end}`).join('|')}`
    if (legacyWatermarkSyncRef.current === signature) return
    legacyWatermarkSyncRef.current = signature
    const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
    const boxes = tracks.map((track) => track.bbox!)
    void onOverlayChange({
      ...legacy,
      x: Math.round(median(boxes.map((box) => box.x)) * sourceWidth),
      y: Math.round(median(boxes.map((box) => box.y)) * sourceHeight),
      w: Math.max(8, Math.round(median(boxes.map((box) => box.w)) * sourceWidth)),
      h: Math.max(8, Math.round(median(boxes.map((box) => box.h)) * sourceHeight)),
      kind: 'effect',
      maskStyle: 'inpaint',
      maskColor: '#101827',
      maskOpacity: 92,
      watermarkSource: 'AI生成+',
    })
  }, [logoDetection?.tracks, onOverlayChange, overlays, projectId, sourceHeight, sourceWidth, videoFrameReady])

  // Khi có size video thật: chuẩn hóa previewCrop theo aspect (giữ pan x/y nếu hợp lệ)
  useEffect(() => {
    if (sourceWidth < 8 || sourceHeight < 8) return
    const id = settings.previewAspectRatio ?? 'original'
    if (!id || id === 'original' || id === 'custom') return
    const win = aspectWindowNorm(sourceWidth, sourceHeight, id)
    if (!win) return
    const cur = settings.previewCrop
    const tol = 0.02
    const needFix =
      !cur
      || Math.abs((cur.w ?? 0) - win.w) > tol
      || Math.abs((cur.h ?? 0) - win.h) > tol
    if (!needFix) return
    const x = cur
      ? Math.max(0, Math.min(1 - win.w, Number(cur.x) || 0))
      : Math.max(0, (1 - win.w) / 2)
    const y = cur
      ? Math.max(0, Math.min(1 - win.h, Number(cur.y) || 0))
      : Math.max(0, (1 - win.h) / 2)
    onSettings({
      ...settings,
      previewAspectRatio: id,
      previewCrop: { x, y, w: win.w, h: win.h },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chỉ khi size/aspect đổi
  }, [sourceWidth, sourceHeight, settings.previewAspectRatio])

  const overCoverMode = settings.coverHardsubs && settings.burnSubs
  const overlayBurnOn = overlayTextEnabled(settings)
  // layout trống + bbox giữa → mid (tránh lane ngang + khung đáy bịa)
  // Timeline: ẩn compound (CapCut Alt+G — chỉ còn video); Preview: bung children
  const timelineLayoutSegs = useMemo(
    () =>
      segments
        .filter((s) => !s.isCompound)
        .map((s) => videoFrameReady ? withInferredLayout(s, sourceHeight, sourceWidth) : s),
    [segments, sourceHeight, sourceWidth, videoFrameReady],
  )
  const compoundShells = useMemo(
    () => segments.filter((s) => s.isCompound),
    [segments],
  )
  // Alt+G: ẩn Caption + Lồng tiếng + Âm gốc (gộp vào shell video) — tháo thì hiện lại
  const compoundMode = compoundShells.length > 0
  // Track Lồng tiếng: chỉ khi đã có file TTS — chưa xong dub thì không hiện ô/track
  const hasDubClips = useMemo(
    () =>
      segments.some(
        (s) =>
          (segmentHasDub(s) && Boolean(s.audioUrl))
          || (s.isCompound
            && Array.isArray(s.compoundChildren)
            && s.compoundChildren.some((c) => segmentHasDub(c) && Boolean(c.audioUrl))),
      ),
    [segments],
  )
  const showDubTrack = !compoundMode && hasDubClips
  /** Đang lồng tiếng: vẫn tua/kéo timeline; chỉ khóa khi job khác (dịch/xuất…). */
  const timelineNavLocked = busy && jobStep !== 'dub' && jobStep !== ''
  const timelineEditLocked = busy && jobStep !== 'dub'
  // Preview/export: bung compound → chữ/mask/TTS y như chưa ghép
  const layoutSegs = useMemo(
    () =>
      expandSegmentsForPlayback(segments).map((s) =>
        videoFrameReady ? withInferredLayout(s, sourceHeight, sourceWidth) : s,
      ),
    [segments, sourceHeight, sourceWidth, videoFrameReady],
  )
  // selected có thể là shell compound (rỗng chữ) — không dùng cho caption layout
  const selectedIsShell = Boolean(selected?.isCompound)
  const selectedLayout = selected && !selectedIsShell
    ? layoutSegs.find((s) => s.id === selected.id)
      ?? (videoFrameReady ? withInferredLayout(selected, sourceHeight, sourceWidth) : selected)
    : null
  const selectedFontPx = resolveCaptionFontSize(selectedLayout ?? undefined, settings, sourceWidth, sourceHeight)
  const fallbackBox = seedCoverBox(selectedLayout ?? undefined, sourceWidth, sourceHeight, selectedFontPx)
    ?? fallbackCoverBox(sourceWidth, sourceHeight, selectedFontPx)
  const selectedLayoutSource = resolveOverLayout(selectedLayout ?? undefined, settings, sourceWidth, sourceHeight)
  // Ưu tiên layout đã nới ngang — không dùng raw bbox hẹp (che hở chữ Trung)
  const selectedBoxSource = bboxDraft
    ?? selectedLayoutSource?.cover
    ?? (selectedLayout?.bbox ? clampCoverBox(selectedLayout.bbox, sourceWidth, sourceHeight) : null)
    ?? resolveSegmentCover(selectedLayout ?? undefined, settings, sourceWidth, sourceHeight)
    ?? fallbackBox
  const verticalWatermarkSegs = useMemo(
    () => layoutSegs.filter((s) => s.layout === 'vertical'),
    [layoutSegs],
  )
  const skipSpuriousMid = (s: Segment) =>
    midInsideVerticalWatermark(s, verticalWatermarkSegs)
  // Prefer caption id trong layoutSegs — bỏ shell compound id
  const captionPreferId =
    selectedId && layoutSegs.some((s) => s.id === selectedId) ? selectedId : null
  const solidAtPlayhead = solidOverlaysAt(layoutSegs, time)
  // Preview masks follow caption [start,end) exactly. Extended cover timing made
  // old/new boxes appear outside their timeline clips and overlap at boundaries.
  const coverSegsRaw = settings.burnSubs
    ? (() => {
        if (overCoverMode) {
          return segmentsAt(layoutSegs, time)
        }
        return solidAtPlayhead
      })()
    : []
  const timelineSegsRaw = segmentsAt(layoutSegs, time)
    .filter((s) => (s.translation || '').trim() && !skipSpuriousMid(s))
  // Một mid / một caption ngang tại một thời điểm — tránh bbox trước đè bbox sau
  const pickOneMid = (list: Segment[]) => {
    const mids = list.filter((s) => captionLaneOf(s, sourceHeight, sourceWidth) === 'mid')
    if (mids.length <= 1) return list
    const keep = solidMidAt(list, time, captionPreferId) ?? mids[0]
    return list.filter((s) => captionLaneOf(s, sourceHeight, sourceWidth) !== 'mid' || s.id === keep.id)
  }
  const pickOneHorizontal = (list: Segment[]) => {
    const hors = list.filter((s) => captionLaneOf(s, sourceHeight, sourceWidth) === 'horizontal')
    if (hors.length <= 1) return list
    // Ưu tiên clip đang trong [start,end); không thì cover pad mới nhất
    const active = hors.find((s) => time >= s.start && time < s.end)
      ?? hors.reduce((a, b) => (a.start >= b.start ? a : b))
    return list.filter((s) => captionLaneOf(s, sourceHeight, sourceWidth) !== 'horizontal' || s.id === active.id)
  }
  const coverSegs = pickOneHorizontal(pickOneMid(coverSegsRaw))
  const timelineSegs = pickOneHorizontal(pickOneMid(timelineSegsRaw))
  const timelineSeg = pickTimelineSeg(layoutSegs, time, captionPreferId)
  const coverSeg = (captionPreferId && coverSegs.find((s) => s.id === captionPreferId))
    ?? coverSegs[0]
    ?? null
  // Khung kéo: OCR solid (mid/label/vertical) + caption ngang — cùng hành vi bbox
  // Không fallback selected shell (translation rỗng → che sai cả span)
  const bboxSeg =
    solidOcrAt(layoutSegs, time, captionPreferId)
    ?? (selectedLayout && (time >= selectedLayout.start && time < selectedLayout.end) ? selectedLayout : null)
    ?? (coverSeg && (isOcrOverlayLayout(coverSeg.layout) || (coverSeg.translation || '').trim()) ? coverSeg : null)
    ?? timelineSeg
    ?? selectedLayout
  const activeCoverDraft =
    bboxSeg && bboxDraft && bboxSeg.id === selected?.id
      ? bboxDraft
      : undefined
  // Che mask: cover mode = mọi hardsub; below/above = chỉ watermark dọc/nhãn (không che mid)
  const maskBoxes =
    settings.burnSubs
      ? coverSegs
          .filter((s) => {
            if (overCoverMode) {
              return (
                isOcrOverlayLayout(s.layout)
                || Boolean(effectiveOverlayLayout(s, sourceHeight, sourceWidth))
                || Boolean(s.translation.trim())
              )
            }
            // below/above: không che chữ hardsub mid/ngang — chỉ dọc/nhãn
            return s.layout === 'vertical' || s.layout === 'label'
          })
          .map((s) => {
            const override = s.id === selected?.id ? activeCoverDraft : undefined
            if (s.translation.trim()) {
              return getCachedPreviewLayout(s, override)?.mask ?? resolveCoverMaskOnly(s, sourceWidth, sourceHeight, crop, override)
            }
            return resolveCoverMaskOnly(s, sourceWidth, sourceHeight, crop, override)
          })
          .filter((b): b is PixelBox => !!b)
      : []
  // Caption "over" layers: cover mode; hoặc dọc/nhãn. Mid/horizontal ở below/above → activeCaptionBox.
  const captionLayers =
    overlayBurnOn
      ? timelineSegs.map((s) => {
          const isVertLabel = s.layout === 'vertical' || s.layout === 'label'
          if (!overCoverMode) {
            // below/above: không vẽ mid/horizontal kiểu cover (đè OCR)
            if (!isVertLabel && s.bboxInherited !== false) return null
          } else if (
            !isOcrOverlayLayout(s.layout)
            && !effectiveOverlayLayout(s, sourceHeight, sourceWidth)
            && !s.translation.trim()
          ) {
            return null
          }
          const layout = getCachedPreviewLayout(s, s.id === selected?.id ? activeCoverDraft : undefined)
          return layout ? { seg: s, layout } : null
        }).filter((x): x is { seg: Segment; layout: NonNullable<ReturnType<typeof resolvePreviewOverLayout>> } => !!x)
      : []
  const captionOverLayout =
    captionLayers.find((c) => c.seg.id === bboxSeg?.id)?.layout
    ?? captionLayers.find((c) => c.seg.id === timelineSeg?.id)?.layout
    ?? captionLayers[0]?.layout
    ?? null
  const bboxLayoutCover =
    bboxSeg && captionLayers.find((c) => c.seg.id === bboxSeg.id)?.layout.cover
  const selectedBox = bboxDraft && selected && bboxSeg?.id === selected.id
    ? bboxDraft
    : bboxLayoutCover
      ?? (bboxSeg
        ? (
            getCachedPreviewLayout(bboxSeg)?.cover
            ?? resolveCoverMaskOnly(bboxSeg, sourceWidth, sourceHeight, crop)
            ?? (bboxSeg.bbox ? clampCoverBox(bboxSeg.bbox, sourceWidth, sourceHeight) : null)
            ?? overlayCoverSeed(bboxSeg, sourceWidth, sourceHeight)
          )
        : null)
      ?? selectedBoxSource
  // Khung kéo (handles): Caption / Cover tool / tab Vùng che chữ
  const showBboxAtPlayhead = (() => {
    if (tool === 'text') return false
    if (bboxDraft) return true
    if (!(trackFocus === 'caption' || tool === 'cover' || propTab === 'mask')) return false
    const target = bboxSeg ?? selected
    // Editor handles follow caption timecode exactly. Only the mask may use
    // coverStart/coverEnd to hide source text before/after speech timing.
    return Boolean(target && time >= target.start && time < target.end)
  })()
  const captionLanes = useMemo(() => {
    const present = new Set(layoutSegs.map((s) => captionLaneOf(s, sourceHeight, sourceWidth)))
    return CAPTION_LANE_DEFS.filter((l) => l.key === 'horizontal' || present.has(l.key))
  }, [layoutSegs, sourceHeight, sourceWidth])
  const previewLogoDraft = logoDraft
    ? { ...logoDraft, positionKeyframes: generateLogoKeyframes(logoDraft, timelineDuration, sourceWidth, sourceHeight, segments, logoDraft.positionSeed) }
    : null
  const previewOverlays = previewLogoDraft
    ? [...overlays.filter((o) => o.id !== previewLogoDraft.id), previewLogoDraft]
    : overlays
  const activeOverlays = previewOverlays.filter((o) =>
    time >= o.start && time < o.end && (!isWatermarkOverlay(o) || (settings.coverLogo && !trackHidden.watermark)),
  )
  const selectedOverlay = overlays.find((o) => o.id === selectedOverlayId) ?? null
  const appliedLogo = overlays.find((o) => o.kind === 'logo') ?? null
  const logoUiState = logoDraft ?? appliedLogo
  const logoDraftChanged = Boolean(logoDraft && (
    logoDraftFile
    || !logoDraftBase
    || JSON.stringify({ ...logoDraft, positionKeyframes: undefined, assetUrl: logoDraft.assetUrl?.startsWith('blob:') ? undefined : logoDraft.assetUrl })
      !== JSON.stringify({ ...logoDraftBase, positionKeyframes: undefined })
  ))
  const logoDraftApplied = Boolean(logoDraft && overlays.some((o) => o.id === logoDraft.id && o.kind === 'logo'))
  const logoToggleRemoves = logoDraftApplied && !logoDraftChanged
  const logoToggleDisabled = logoApplying || (!logoToggleRemoves && (!logoDraftChanged || !logoDraft?.text.trim()))

  useEffect(() => {
    setFontSizeDraft(selected?.fontSize ?? 0)
  }, [selected?.id, selected?.fontSize])

  useEffect(() => () => {
    audioRef.current?.pause()
    dubAudioRef.current?.pause()
    bgAudioRef.current?.pause()
  }, [])

  // Đang lồng tiếng / job: dừng preview TTS cũ (tránh nghe cache lệch)
  useEffect(() => {
    if (!busy) return
    dubAudioRef.current?.pause()
    dubTokenRef.current = ''
    dubFinishedIdsRef.current.clear()
  }, [busy])

  // Stem xóa lời — ưu tiên cache; gen counter tránh race StrictMode / remount preview.
  const stemReadyUrlRef = useRef<string | null>(null)
  const stemGenRef = useRef(0)
  const stemProjectRef = useRef(projectId)
  useEffect(() => {
    if (stemProjectRef.current !== projectId) {
      stemProjectRef.current = projectId
      stemReadyUrlRef.current = null
      stemGenRef.current += 1
      bgAudioRef.current?.pause()
      bgAudioRef.current = null
      setStemStatus('off')
      setStemProgress(0)
      setStemError(null)
    }
  }, [projectId])

  useEffect(() => {
    if (!wantNoVocals) {
      setStemStatus('off')
      setStemProgress(0)
      setStemError(null)
      // Giữ stemReadyUrlRef — bật lại filter không tách lại
      bgAudioRef.current?.pause()
      return
    }

    // Đã có Audio element + URL session — không POST lại
    if (
      stemRetry === 0
      && stemStatus === 'ready'
      && bgAudioRef.current
      && stemReadyUrlRef.current
    ) {
      return
    }

    // Session URL còn (vào lại preview) — gắn Audio ngay, không gọi Demucs
    if (stemRetry === 0 && stemReadyUrlRef.current) {
      const url = stemReadyUrlRef.current
      const a = new Audio(url)
      a.preload = 'auto'
      bgAudioRef.current = a
      setStemProgress(100)
      setStemStatus('ready')
      setStemError(null)
      return
    }

    // Vào preview / bật xóa lời → hiện % ngay (tránh bar chỉ ghi «Xóa lời»)
    setStemStatus('loading')
    setStemProgress((p) => (p > 0 ? p : 1))
    setStemError(null)

    const gen = ++stemGenRef.current
    let poll: number | null = null

    const alive = () => gen === stemGenRef.current

    const applyReady = (audioUrl: string) => {
      if (!alive()) return
      const a = new Audio(audioUrl)
      a.preload = 'auto'
      bgAudioRef.current = a
      stemReadyUrlRef.current = audioUrl
      setStemProgress(100)
      setStemStatus('ready')
      setStemError(null)
    }

    void (async () => {
      try {
        // 1) Cache hit ngay — không loading 1%
        if (stemRetry === 0) {
          const st = await api.noVocalsStatus(projectId)
          if (!alive()) return
          if (st.ready && st.audioUrl) {
            applyReady(st.audioUrl)
            return
          }
          setStemStatus('loading')
          setStemProgress(Math.max(1, Math.min(99, st.progress || 1)))
        } else {
          setStemStatus('loading')
          setStemProgress(1)
        }

        poll = window.setInterval(() => {
          void api.noVocalsProgress(projectId).then((p) => {
            if (!alive()) return
            if (p.ready && p.audioUrl) {
              if (poll != null) window.clearInterval(poll)
              poll = null
              applyReady(p.audioUrl)
              return
            }
            setStemProgress(Math.max(1, Math.min(99, Math.round(p.progress || 0))))
          }).catch(() => { /* ignore */ })
        }, 1200)

        try {
          const res = await api.prepareNoVocals(projectId)
          if (!alive()) return
          if (poll != null) window.clearInterval(poll)
          poll = null
          applyReady(res.audioUrl)
        } catch (e: unknown) {
          if (poll != null) window.clearInterval(poll)
          poll = null
          if (!alive()) return
          // Cache có thể đã ready dù POST fail / abort
          try {
            const st = await api.noVocalsStatus(projectId)
            if (!alive()) return
            if (st.ready && st.audioUrl) {
              applyReady(st.audioUrl)
              return
            }
          } catch { /* ignore */ }
          bgAudioRef.current = null
          // Giữ stemReadyUrlRef nếu đã từng ready — tránh mất cache session
          setStemStatus('error')
          setStemError(e instanceof Error ? e.message : 'Không tách được stem xóa lời')
        }
      } catch (e: unknown) {
        if (!alive()) return
        setStemStatus('error')
        setStemError(e instanceof Error ? e.message : 'Không kiểm tra được stem')
      }
    })()

    return () => {
      // Hủy run này — run mới (StrictMode / remount) bump gen
      if (stemGenRef.current === gen) stemGenRef.current += 1
      if (poll != null) window.clearInterval(poll)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- không re-run khi stemStatus đổi
  }, [projectId, wantNoVocals, stemRetry])

  // Áp mute / stem ngay khi đổi filter hoặc bake speed (không đợi timeupdate).
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const at = speedSegmentAt(segments, videoToTimelineTime(video.currentTime))
    const playRate = previewVideoRate(
      settings.matchDuration,
      bakedPreferVideo,
      at?.videoSpeed,
      bakedSpeed,
      hasBakedSpeed,
    )
    dubHardSyncRef.current = true
    syncOriginalBg(
      video.currentTime,
      !video.paused,
      Boolean(dubTokenRef.current),
      playRate,
      true,
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps -- policy + bake flags
  }, [
    muteOriginal,
    wantNoVocals,
    stemStatus,
    settings.originalAudioVolume,
    trackMute.dub,
    bakedSpeed,
    bakedPreferVideo,
  ])

  useEffect(() => {
    if (!aspectMenuOpen) return
    const close = (e: MouseEvent) => {
      if (aspectMenuRef.current && !aspectMenuRef.current.contains(e.target as Node)) {
        setAspectMenuOpen(false)
      }
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [aspectMenuOpen])

  useEffect(() => {
    if (!fitMenuOpen) return
    const close = (e: MouseEvent) => {
      if (fitMenuRef.current && !fitMenuRef.current.contains(e.target as Node)) {
        setFitMenuOpen(false)
      }
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [fitMenuOpen])

  const aspectLabel = ASPECT_PRESETS.find((p) => p.id === aspectId)?.label ?? 'Bản gốc'
  const fitMenuLabel = previewZoom === 'fit' ? 'Fit' : `${Math.round(previewZoom * 100)}%`

  function seek(segment: Segment) {
    const video = videoRef.current
    focusCaption(segment)
    if (!video) return
    video.currentTime = timelineToVideoTime(segment.start)
    setTime(segment.start)
    void video.play().catch(() => { /* requires explicit user gesture */ })
  }

  const {
    beginDrag,
    beginMediaDrag,
    beginTimelineTextDrag,
    beginMarqueeSelect,
    beginBboxDrag,
  } = useTimelineDrag({
    segments,
    settings,
    busy,
    timelineEditLocked,
    trackLocked,
    timelineDuration,
    pxPerSec,
    time,
    tool,
    autoSnapping,
    mediaLinked,
    sourceWidth,
    sourceHeight,
    crop,
    trackFocus,
    selected,
    selectedId,
    selectedIds,
    selectedMediaIds,
    selectedBox,
    fallbackBox,
    bboxDraft,
    videoClips,
    bgClips,
    tracksScrollRef,
    tracksColRef,
    canvasRef,
    draftRef,
    groupDraftRef,
    marqueeRef,
    bboxDraftRef,
    setDraft,
    setGroupDraft,
    setMarquee,
    setBboxDraft,
    setDraggingBox,
    setSnapGuides,
    setSelectedId,
    setSelectedIds,
    setSelectedMediaId,
    setSelectedMediaIds,
    setSelectedDubIds,
    setSelectedOverlayId,
    setTrackFocus,
    setPropTab,
    setTool,
    setVideoClips,
    setBgClips,
    expandGroupSelection,
    focusCaption,
    focusDub,
    focusVideo,
    focusBg,
    focusText,
    seekPlayhead,
    pushHistory,
    pushHistoryOnce,
    editSegment,
    editOverlay,
    getCachedPreviewLayout,
    onSegmentsReplace,
  })

  /** Ids đang chọn + cùng groupId (OpenCut-style). */
  function expandGroupSelection(ids: string[]): string[] {
    const set = new Set(ids)
    const gids = new Set(
      segments.filter((s) => set.has(s.id) && s.groupId).map((s) => s.groupId as string),
    )
    if (!gids.size) return ids
    for (const s of segments) {
      if (s.groupId && gids.has(s.groupId)) set.add(s.id)
    }
    return [...set]
  }

  const groupOpLockRef = useRef(false)

  /** Group clip (giữ từng đoạn) — Ctrl+G. `forceIds` = snapshot menu multi. */
  function groupSelectedCaptions(forceIds?: string[]) {
    if (timelineEditLocked || groupOpLockRef.current) return
    const ids = expandGroupSelection(
      forceIds?.length
        ? forceIds
        : selectedIds.length
          ? selectedIds
          : selectedId
            ? [selectedId]
            : [],
    )
    if (ids.length < 2) return
    const picked = segments.filter((s) => ids.includes(s.id))
    if (picked.length < 2) return
    groupOpLockRef.current = true
    try {
      pushHistory()
      const gid = `g_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
      const idSet = new Set(picked.map((s) => s.id))
      const next = segments.map((s) => (idSet.has(s.id) ? { ...s, groupId: gid } : s))
      setSelectedIds(picked.map((s) => s.id))
      void Promise.resolve(onSegmentsReplace(next)).finally(() => {
        groupOpLockRef.current = false
      })
    } catch {
      groupOpLockRef.current = false
    }
  }

  /** Bỏ group — Ctrl+Shift+G. */
  function ungroupSelectedCaptions() {
    if (timelineEditLocked || groupOpLockRef.current) return
    const ids = expandGroupSelection(selectedIds.length ? selectedIds : selectedId ? [selectedId] : [])
    if (!ids.length) return
    const idSet = new Set(ids)
    const hasGroup = segments.some((s) => idSet.has(s.id) && s.groupId)
    if (!hasGroup) return
    groupOpLockRef.current = true
    try {
      pushHistory()
      const next = segments.map((s) => {
        if (!idSet.has(s.id) || !s.groupId) return s
        const copy = { ...s }
        delete copy.groupId
        return copy
      })
      void Promise.resolve(onSegmentsReplace(next)).finally(() => {
        groupOpLockRef.current = false
      })
    } catch {
      groupOpLockRef.current = false
    }
  }

  /**
   * CapCut Alt+G — compound clip: 1 shell timeline, children giữ caption+TTS.
   * Đổi tốc độ bake scale shell; children scale theo — không lệch.
   * `forceIds` = snapshot menu multi (không phụ thuộc setState).
   */
  function createCompoundFromSelection(forceIds?: string[]) {
    if (timelineEditLocked || groupOpLockRef.current) return
    // Marquee có thể chọn TTS (selectedDubIds) + caption — gộp id
    const raw = forceIds?.length
      ? forceIds
      : [...selectedIds, ...selectedDubIds]
    const ids = expandGroupSelection([...new Set(raw)])
    if (ids.length < 2) return
    // Đã là compound thì bỏ
    if (ids.some((id) => segments.find((s) => s.id === id)?.isCompound)) {
      setTtsError('Bỏ chọn compound trước khi ghép mới')
      return
    }
    groupOpLockRef.current = true
    pushHistory()
    setGroupDraft(null)
    setDraft(null)
    void (async () => {
      try {
        const res = await api.createCompound(projectId, ids)
        let ordered = reindexSegments(
          (Array.isArray(res.segments) ? res.segments : []).map((s, i) => ({
            ...s,
            index: i,
          })) as Segment[],
        )
        // Bảo vệ: shell phải bung được children (chữ preview y như chưa ghép)
        const shells = ordered.filter((s) => s.isCompound)
        const broken = shells.filter((s) => !expandCompoundShell(s).length)
        if (broken.length) {
          // Fallback client: nest từ selection hiện tại nếu API mất children
          const byId = new Map(segments.map((s) => [s.id, s]))
          const picked = ids.map((id) => byId.get(id)).filter(Boolean) as Segment[]
          if (picked.length >= 2) {
            const t0 = Math.min(...picked.map((s) => s.start))
            const t1 = Math.max(...picked.map((s) => s.end))
            const children = picked
              .slice()
              .sort((a, b) => a.start - b.start)
              .map((s) => ({
                ...s,
                start: Math.max(0, s.start - t0),
                end: Math.max(0.05, s.end - t0),
                coverStart:
                  typeof s.coverStart === 'number' ? Math.max(0, s.coverStart - t0) : undefined,
                coverEnd:
                  typeof s.coverEnd === 'number' ? Math.max(0, s.coverEnd - t0) : undefined,
                groupId: undefined,
                isCompound: undefined,
                compoundChildren: undefined,
              }))
            const cid = res.compoundId || res.mergedId || `cmp_${Date.now().toString(36)}`
            const drop = new Set(ids)
            ordered = reindexSegments([
              ...segments.filter((s) => !drop.has(s.id) && !s.isCompound),
              {
                id: cid,
                index: 0,
                start: t0,
                end: t1,
                source: `[Compound ×${children.length}]`,
                translation: '',
                voice: picked[0].voice || '',
                layout: picked[0].layout || 'horizontal',
                dub: picked.some((s) => segmentHasDub(s)),
                isCompound: true,
                compoundChildren: children,
                coverStart: t0,
                coverEnd: t1,
                captionLayout: null,
                videoSpeed: 1,
              },
            ])
          }
        }
        // API compound đã save_meta — không PUT lại (tránh strip compoundChildren)
        void onSegmentsReplace(ordered, { persist: false })
        // CapCut: ghép xong chỉ còn video — chọn shell trên track Video
        const cid = ordered.find((s) => s.isCompound)?.id || res.compoundId || res.mergedId
        setSelectedId(cid)
        setSelectedIds(cid ? [cid] : [])
        setSelectedDubIds([])
        setSelectedMediaIds([])
        setTrackFocus('video')
        setPropTab('video')
      } catch (e) {
        setTtsError(e instanceof Error ? e.message : 'Ghép compound thất bại')
      } finally {
        groupOpLockRef.current = false
      }
    })()
  }

  /** Tháo compound (restore children + TTS từng câu). */
  function uncompoundSelected() {
    if (timelineEditLocked || groupOpLockRef.current) return
    const id = selectedId || selectedIds[0]
    if (!id) return
    const shell = segments.find((s) => s.id === id)
    if (!shell?.isCompound) return
    groupOpLockRef.current = true
    pushHistory()
    void (async () => {
      try {
        const res = await api.uncompound(projectId, id)
        const ordered = reindexSegments(
          (Array.isArray(res.segments) ? res.segments : []).map((s, i) => ({
            ...s,
            index: i,
          })) as Segment[],
        )
        void onSegmentsReplace(ordered, { persist: false })
        setSelectedIds([])
        setSelectedId(null)
        setTrackFocus('caption')
      } catch (e) {
        setTtsError(e instanceof Error ? e.message : 'Tháo compound thất bại')
      } finally {
        groupOpLockRef.current = false
      }
    })()
  }

  /** Id caption đang chọn — ưu tiên snapshot menu, rồi multi state. */
  function selectionCaptionIds(anchorId?: string | null, menuIds?: string[]): string[] {
    if (menuIds?.length) return expandGroupSelection([...new Set(menuIds)])
    const base =
      selectedIds.length > 0
        ? selectedIds
        : selectedDubIds.length > 0
          ? selectedDubIds
          : selectedId
            ? [selectedId]
            : anchorId
              ? [anchorId]
              : []
    const withAnchor =
      anchorId && !base.includes(anchorId) && base.length === 0
        ? [anchorId]
        : base
    return expandGroupSelection(withAnchor.length ? withAnchor : anchorId ? [anchorId] : [])
  }

  /** Áp patch cho mọi caption trong selection (chuột phải multi). */
  function patchSelectedCaptions(
    anchorId: string | null | undefined,
    patch: (s: Segment) => Segment,
    menuIds?: string[],
  ) {
    const ids = new Set(selectionCaptionIds(anchorId, menuIds))
    if (!ids.size) return
    pushHistory()
    void onSegmentsReplace(segments.map((s) => (ids.has(s.id) ? patch(s) : s)))
  }

  function beginScrub(event: ReactPointerEvent<HTMLElement>) {
    if (timelineNavLocked) return
    const scroller = tracksScrollRef.current
    const col = tracksColRef.current
    const video = videoRef.current
    if (!scroller || !col || !video) return
    event.preventDefault()
    // Chỉ tua playhead — không đổi track focus (đang Âm thanh/TTS thì vẫn giữ)
    const colLeft = col.getBoundingClientRect().left
    let pointerX = event.clientX
    let scrollRaf = 0
    const seekAtPointer = () => {
      const px = pointerX - colLeft + scroller.scrollLeft
      const nextTime = Math.max(0, Math.min(timelineDuration, px / pxPerSec))
      video.currentTime = timelineToVideoTime(nextTime)
      setTime(nextTime)
      if (trackFocus === 'caption' || trackFocus === 'dub') {
        const current = segmentAt(segments, nextTime)
        if (current) setSelectedId(current.id)
      }
    }
    const autoScroll = () => {
      scrollRaf = 0
      const rect = scroller.getBoundingClientRect()
      const edge = Math.min(64, rect.width * 0.12)
      const delta = pointerX < rect.left + edge
        ? Math.max(-40, pointerX - rect.left - edge)
        : pointerX > rect.right - edge
          ? Math.min(40, pointerX - rect.right + edge)
          : 0
      if (delta) {
        const next = Math.max(
          0,
          Math.min(scroller.scrollWidth - scroller.clientWidth, scroller.scrollLeft + delta),
        )
        if (next !== scroller.scrollLeft) {
          scroller.scrollLeft = next
          if (rulerScrollRef.current) rulerScrollRef.current.scrollLeft = next
          setScrollLeft(next)
          seekAtPointer()
          scrollRaf = requestAnimationFrame(autoScroll)
        }
      }
    }
    const update = (clientX: number) => {
      pointerX = clientX
      seekAtPointer()
      if (!scrollRaf) scrollRaf = requestAnimationFrame(autoScroll)
    }
    dubHardSyncRef.current = true
    dubFinishedIdsRef.current.clear()
    update(event.clientX)
    const move = (pointer: PointerEvent) => update(pointer.clientX)
    const commit = () => {
      cancelAnimationFrame(scrollRaf)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', commit)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', commit, { once: true })
  }

  /** Thanh tiến độ preview / toàn màn hình — tua theo chiều ngang bar. */
  function beginPreviewSeek(event: ReactPointerEvent<HTMLElement>) {
    if (timelineNavLocked || timelineDuration <= 0) return
    const video = videoRef.current
    if (!video) return
    event.preventDefault()
    const bar = event.currentTarget
    const seekTo = (clientX: number) => {
      const rect = bar.getBoundingClientRect()
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)))
      const nextTime = ratio * timelineDuration
      video.currentTime = timelineToVideoTime(nextTime)
      setTime(nextTime)
      if (trackFocus === 'caption' || trackFocus === 'dub') {
        const current = pickTimelineSeg(segments, nextTime, selectedId)
        const cov = segmentAtCover(segments, nextTime)
        if (current) {
          setSelectedId(current.id)
        } else if (cov) {
          const prev = selectedId ? segments.find((s) => s.id === selectedId) : null
          const prevLane = prev ? captionLaneOf(prev, sourceHeight, sourceWidth) : null
          if (!(captionLaneOf(cov, sourceHeight, sourceWidth) === 'vertical' && prevLane && prevLane !== 'vertical')) {
            setSelectedId(cov.id)
          }
        }
      }
      dubHardSyncRef.current = true
      syncDubAudio(nextTime, !video.paused)
    }
    seekTo(event.clientX)
    const move = (pointer: PointerEvent) => seekTo(pointer.clientX)
    const commit = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', commit)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function beginFreeCrop() {
    setCropDraft(settings.previewCrop ?? { x: 0.1, y: 0.1, w: 0.8, h: 0.8 })
    setCropEditing(true)
    setAspectMenuOpen(false)
  }

  /** Chọn preset tỷ lệ + crop giữa; sau đó kéo preview để pan. */
  function applyAspectPreset(presetId: string) {
    if (presetId === 'custom') {
      beginFreeCrop()
      return
    }
    const curId = settings.previewAspectRatio ?? 'original'
    if (presetId === curId && presetId !== 'original') {
      setAspectMenuOpen(false)
      return
    }
    // pushHistory trong editSettings
    if (presetId === 'original') {
      editSettings({ ...settings, previewAspectRatio: 'original', previewCrop: null })
      setCropEditing(false)
      setAspectMenuOpen(false)
      return
    }
    // Chưa có size video → vẫn lưu preset; pan khi metadata xong
    const sw = sourceWidth > 8 ? sourceWidth : 1080
    const sh = sourceHeight > 8 ? sourceHeight : 1920
    const centered = centeredAspectCrop(sw, sh, presetId)
    editSettings({
      ...settings,
      previewAspectRatio: presetId,
      previewCrop: centered ?? { x: 0, y: 0, w: 1, h: 1 },
    })
    setCropEditing(false)
    setAspectMenuOpen(false)
  }

  /**
   * Kéo pan khung cắt khi đã chọn tỷ lệ (9:16, 16:9…).
   * Giữ w/h theo aspect; chỉ đổi x/y. Tia giữa hiện suốt lúc kéo, sáng khi snap.
   */
  function beginAspectPan(event: ReactPointerEvent) {
    if (cropEditing || timelineEditLocked || tool === 'text' || tool === 'cover') return
    const id = settings.previewAspectRatio ?? 'original'
    if (!id || id === 'original' || id === 'custom') return
    const sw = sourceWidth > 8 ? sourceWidth : 1080
    const sh = sourceHeight > 8 ? sourceHeight : 1920
    const win = aspectWindowNorm(sw, sh, id)
    if (!win || (win.w >= 0.999 && win.h >= 0.999)) return
    const canvas = canvasRef.current
    if (!canvas) return
    event.preventDefault()
    event.stopPropagation()
    const rect = canvas.getBoundingClientRect()
    const startX = event.clientX
    const startY = event.clientY
    const cur =
      settings.previewCrop
      && Number.isFinite(settings.previewCrop.x)
      && Number.isFinite(settings.previewCrop.y)
        ? settings.previewCrop
        : centeredAspectCrop(sw, sh, id)
    if (!cur) return
    const ox = Math.max(0, Math.min(1 - win.w, Number(cur.x) || 0))
    const oy = Math.max(0, Math.min(1 - win.h, Number(cur.y) || 0))
    const ow = win.w
    const oh = win.h
    const aspectIdLive = id
    // Nới snap ~5% cửa sổ pan (dễ dính giữa hơn 2%)
    const snapTol = Math.max(0.035, Math.min(0.08, Math.min(ow, oh) * 0.12))
    const centerX = (1 - ow) / 2
    const centerY = (1 - oh) / 2
    const settingsSnap = settings
    let last = { x: ox, y: oy, w: ow, h: oh }
    const histGate = { current: false }
    setPanningCrop(true)
    // Hiện tia ngay từ frame đầu (mờ → sáng khi dính giữa)
    setSnapGuides({
      v: Math.abs(ox - centerX) <= snapTol,
      h: Math.abs(oy - centerY) <= snapTol,
    })
    const update = (clientX: number, clientY: number, altKey: boolean) => {
      // dx theo full frame 0–1 (không nhân ow) — kéo mượt, snap đúng
      const dx = (clientX - startX) / Math.max(1, rect.width)
      const dy = (clientY - startY) / Math.max(1, rect.height)
      let x = Math.max(0, Math.min(1 - ow, ox - dx))
      let y = Math.max(0, Math.min(1 - oh, oy - dy))
      let guideV = false
      let guideH = false
      if (!altKey) {
        if (Math.abs(x - centerX) <= snapTol) {
          x = centerX
          guideV = true
        }
        if (Math.abs(y - centerY) <= snapTol) {
          y = centerY
          guideH = true
        }
      }
      last = { x, y, w: ow, h: oh }
      setSnapGuides({ v: guideV, h: guideH })
      // 1 history / lần kéo (trước khi đổi settings)
      if (
        Math.abs(x - ox) > 0.001
        || Math.abs(y - oy) > 0.001
      ) {
        pushHistoryOnce(histGate)
      }
      onSettings({
        ...settingsSnap,
        previewAspectRatio: aspectIdLive,
        previewCrop: last,
      })
    }
    const onMove = (e: PointerEvent) => {
      e.preventDefault()
      update(e.clientX, e.clientY, e.altKey)
    }
    const onUp = (e: PointerEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      // commit lần cuối
      update(e.clientX, e.clientY, e.altKey)
      setPanningCrop(false)
      setSnapGuides({ h: false, v: false })
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp, { once: true })
  }

  function beginCropDrag(
    event: ReactPointerEvent,
    mode: 'move' | 'nw' | 'ne' | 'se' | 'sw',
  ) {
    const canvas = canvasRef.current
    if (!canvas) return
    event.preventDefault()
    event.stopPropagation()
    const rect = canvas.getBoundingClientRect()
    const startX = event.clientX
    const startY = event.clientY
    const original = { ...cropDraft }
    const min = 0.05
    const snapTol = 0.02
    const histGate = { current: false }
    setPanningCrop(true)
    setSnapGuides({ h: false, v: false })
    const update = (clientX: number, clientY: number, altKey: boolean) => {
      const dx = (clientX - startX) / Math.max(1, rect.width)
      const dy = (clientY - startY) / Math.max(1, rect.height)
      let { x, y, w, h } = original
      if (mode === 'move') {
        x = Math.max(0, Math.min(1 - w, x + dx))
        y = Math.max(0, Math.min(1 - h, y + dy))
        // Snap tâm khung tự do về giữa canvas (0–1)
        let guideV = false
        let guideH = false
        if (!altKey) {
          const cx = x + w / 2
          const cy = y + h / 2
          if (Math.abs(cx - 0.5) <= snapTol) {
            x = Math.max(0, Math.min(1 - w, 0.5 - w / 2))
            guideV = true
          }
          if (Math.abs(cy - 0.5) <= snapTol) {
            y = Math.max(0, Math.min(1 - h, 0.5 - h / 2))
            guideH = true
          }
        }
        setSnapGuides({ v: guideV, h: guideH })
      } else {
        if (mode.includes('w')) { const right = x + w; x = Math.max(0, Math.min(right - min, x + dx)); w = right - x }
        if (mode.includes('e')) w = Math.max(min, Math.min(1 - x, w + dx))
        if (mode.includes('n')) { const bottom = y + h; y = Math.max(0, Math.min(bottom - min, y + dy)); h = bottom - y }
        if (mode.includes('s')) h = Math.max(min, Math.min(1 - y, h + dy))
        setSnapGuides({ h: false, v: false })
      }
      if (
        Math.abs(x - original.x) > 0.001
        || Math.abs(y - original.y) > 0.001
        || Math.abs(w - original.w) > 0.001
        || Math.abs(h - original.h) > 0.001
      ) {
        pushHistoryOnce(histGate)
      }
      setCropDraft({ x, y, w, h })
    }
    const onMove = (e: PointerEvent) => update(e.clientX, e.clientY, e.altKey)
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setPanningCrop(false)
      setSnapGuides({ h: false, v: false })
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp, { once: true })
  }

  function beginOverlayDrag(event: ReactPointerEvent, overlay: TextOverlay) {
    if (timelineEditLocked || tool === 'text' || trackLocked.text) return
    const canvas = canvasRef.current
    if (!canvas) return
    event.preventDefault()
    const rect = canvas.getBoundingClientRect()
    const original = { x: overlay.x, y: overlay.y }
    setSelectedOverlayId(overlay.id)
    setTrackFocus(isWatermarkOverlay(overlay) ? 'watermark' : 'text')
    setPropTab('overlay')
    const histGate = { current: false }

    const update = (move: PointerEvent) => {
      const dx = ((move.clientX - event.clientX) / rect.width) * crop.w
      const dy = ((move.clientY - event.clientY) / rect.height) * crop.h
      const next = {
        ...overlay,
        x: Math.round(Math.max(0, Math.min(sourceWidth - overlay.w, original.x + dx))),
        y: Math.round(Math.max(0, Math.min(sourceHeight - overlay.h, original.y + dy))),
      }
      if (Math.abs(next.x - original.x) > 1 || Math.abs(next.y - original.y) > 1) {
        pushHistoryOnce(histGate)
      }
      if (logoDraft?.id === overlay.id) setLogoDraft(next)
      else onOverlayChange(next) // history đã ghi 1 lần
    }
    const commit = () => { window.removeEventListener('pointermove', update); window.removeEventListener('pointerup', commit) }
    window.addEventListener('pointermove', update)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function relayoutCaptionSegment(
    seg: Segment,
    patch: Pick<Segment, 'fontSize' | 'fontFamily'>,
  ): Segment {
    const next = { ...seg, ...patch, captionLayout: null }
    setMeasureFontFamily(captionFontCss(next.fontFamily || settings.subtitleFontFamily || 'system'))
    if (!next.translation.trim() || !(settings.coverHardsubs && settings.burnSubs)) return next
    if (isOcrOverlayLayout(next.layout)) {
      const preferred = (next.fontSize ?? 0) > 0 ? Number(next.fontSize) : 0
      const seed = overlayCoverSeed(next, sourceWidth, sourceHeight)
      if (!seed) return next
      const laid = layoutOcrOverlay(next.layout, seed, next.translation, preferred, sourceWidth, sourceHeight)
      return segmentWithLayout(next, {
        cover: laid.cover,
        caption: laid.caption,
        lines: laid.lines,
        fontPx: laid.fontPx,
      }, laid.fontPx)
    }
    const fontPx = resolveCaptionFontSize(next, settings, sourceWidth, sourceHeight)
    const base = next.bbox
      ? clampCoverBox(next.bbox, sourceWidth, sourceHeight)
      : resolveSegmentCover(next, settings, sourceWidth, sourceHeight)
        ?? seedCoverBox(next, sourceWidth, sourceHeight, fontPx)
        ?? fallbackCoverBox(sourceWidth, sourceHeight, fontPx)
    return segmentWithLayout(
      next,
      adaptiveCoverLayout(base, next.translation, fontPx, sourceWidth, sourceHeight),
      fontPx,
    )
  }

  function applyFontSize(scope: 'one' | 'all', sizeOverride?: number) {
    const size = sizeOverride !== undefined ? sizeOverride : fontSizeDraft
    setFontSizeDraft(size)
    const relayout = (seg: Segment) => relayoutCaptionSegment(seg, {
      fontSize: size,
      fontFamily: seg.fontFamily,
    })
    if (scope === 'one') {
      if (selected) editSegment(relayout(selected))
      return
    }
    // «Tất cả» = cùng lane (Caption / CAP-MID / Dọc / Nhãn) — không đụng lane khác
    const src = selected ?? bboxSeg
    const lane = src ? captionLaneOf(src, sourceHeight, sourceWidth) : null
    pushHistory()
    void onSegmentsReplace(segments.map((seg) => {
      if (lane && captionLaneOf(seg, sourceHeight, sourceWidth) !== lane) return seg
      return relayout(seg)
    }))
    // Chỉ cập nhật font dự án khi áp lane Caption đáy
    if (size > 0 && lane === 'horizontal') {
      onSettings({ ...settings, subtitleFontSize: size })
    }
  }

  async function applyFontFamily(scope: 'one' | 'all', family: string) {
    const seq = ++fontApplySeqRef.current
    await loadCaptionFont(family, selected?.translation || 'Phụ đề tiếng Việt')
    if (seq !== fontApplySeqRef.current) return
    const relayout = (seg: Segment) => relayoutCaptionSegment(seg, {
      fontSize: seg.fontSize,
      fontFamily: family,
    })
    if (scope === 'one') {
      if (selected) editSegment(relayout(selected))
      return
    }
    pushHistory()
    onSettings({ ...settings, subtitleFontFamily: family })
    void onSegmentsReplace(segments.map(relayout))
  }

  function applyCaptionColor(scope: 'one' | 'all', textColor: string) {
    if (scope === 'one') {
      if (selected) editSegment({ ...selected, textColor })
      return
    }
    pushHistory()
    onSettings({ ...settings, captionTextColor: textColor })
    void onSegmentsReplace(segments.map((seg) => ({ ...seg, textColor })))
  }

  function applyCaptionModeAll(mode: 'cover' | 'below' | 'above' | 'none') {
    pushHistory()
    if (mode === 'cover') {
      onSettings({ ...settings, coverHardsubs: true, burnSubs: true })
      return
    }
    if (mode === 'none') {
      onSettings({ ...settings, coverHardsubs: false, burnSubs: false })
      void onSegmentsReplace(
        segments.map((s) => ({ ...s, captionLayout: null })),
      )
      return
    }
    // below/above: tắt che, xóa layout bake cover (đỡ đè OCR như mode cover)
    onSettings({
      ...settings,
      coverHardsubs: false,
      burnSubs: true,
      captionPlacement: mode,
    })
    void onSegmentsReplace(
      segments.map((s) => ({ ...s, captionLayout: null })),
    )
  }

  async function previewTts(forSeg?: Segment) {
    const target = forSeg ?? selected
    if (!target || ttsBusy) return
    if (forSeg) setSelectedId(forSeg.id)
    setTtsBusy(true); setTtsError(null)
    pauseDubAudio()
    try {
      const voice = target.voice || settings.defaultVoice
      const speed = target.ttsSpeed ?? 1.1
      const result = await api.previewTts(projectId, target.id, {
        text: target.translation,
        voice,
        lang: settings.targetLang === 'none' ? 'vi' : settings.targetLang,
        speed,
      })
      editSegment({ ...target, ttsSpeed: speed, audioUrl: result.audioUrl, audioDuration: result.duration })
      audioRef.current?.pause()
      audioRef.current = new Audio(result.audioUrl)
      await audioRef.current.play()
    } catch (error) {
      setTtsError(error instanceof Error ? error.message : 'Không thể nghe TTS')
    } finally {
      setTtsBusy(false)
    }
  }

  function playSegmentDub(seg: Segment) {
    setSelectedId(seg.id)
    setPropTab('audio')
    const video = videoRef.current
    if (!video) return
    video.currentTime = timelineToVideoTime(seg.start)
    setTime(seg.start)
    void video.play().catch(() => { /* requires gesture */ })
  }

  function addTextOverlay(clientX?: number, clientY?: number) {
    const rect = canvasRef.current?.getBoundingClientRect()
    const x = rect && clientX !== undefined
      ? crop.x + Math.max(0, Math.min(crop.w * 0.85, ((clientX - rect.left) / rect.width) * crop.w))
      : crop.x + crop.w * 0.25
    const y = rect && clientY !== undefined
      ? crop.y + Math.max(0, Math.min(crop.h * 0.85, ((clientY - rect.top) / rect.height) * crop.h))
      : crop.y + crop.h * 0.2
    const overlay: TextOverlay = {
      id: crypto.randomUUID(), start: time, end: Math.min(timelineDuration, time + 3),
      text: 'Nhập nội dung',
      x: Math.round(x), y: Math.round(y),
      w: Math.round(sourceWidth * 0.5), h: Math.round(sourceHeight * 0.12),
      fontSize: 42, color: '#ffffff',
      kind: 'text',
    }
    setSelectedOverlayId(overlay.id)
    setTrackFocus('text')
    setTool('select')
    setPropTab('overlay')
    pushHistory()
    editOverlay(overlay, true)
  }

  function fitTextLogo(logo: TextOverlay, text = logo.text, fontSize = logo.fontSize) {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (ctx) ctx.font = `800 ${fontSize}px ${captionFontCss(logo.fontFamily ?? 'system')}`
    const w = Math.ceil((ctx?.measureText(text || 'LOGO').width ?? fontSize * 3) + fontSize * .5)
    const h = Math.ceil(fontSize * 1.45)
    return { ...logo, text, fontSize, w: Math.min(crop.w, Math.max(24, w)), h: Math.max(18, h) }
  }

  function editLogo(source: 'text' | 'image' | 'icon' = 'text') {
    if (logoDraftFile && logoDraft?.assetUrl?.startsWith('blob:')) URL.revokeObjectURL(logoDraft.assetUrl)
    const existing = overlays.find((o) => o.kind === 'logo')
    const shortEdge = Math.min(sourceWidth, sourceHeight)
    let draft: TextOverlay = existing
      ? { ...existing, positionKeyframes: [] }
      : {
          id: crypto.randomUUID(), start: 0, end: timelineDuration, text: 'LOGO',
          x: Math.round(crop.x + crop.w * .04), y: Math.round(crop.y + crop.h * .04),
          w: Math.max(48, Math.round(shortEdge * .12)), h: Math.max(24, Math.round(shortEdge * .06)),
          fontSize: 12, fontFamily: 'system', color: '#ffffff', kind: 'logo',
          logoSource: source, scope: 'full', motion: 'random', opacity: 85,
          visibleSec: 4, hiddenSec: 2, fadeSec: .5, safeMargin: 4, positionSeed: Date.now(),
        }
    draft = { ...draft, logoSource: source }
    if (source === 'text' && (!existing || existing.logoSource !== 'text')) {
      draft = fitTextLogo(draft)
    }
    setLogoDraft(draft)
    setLogoDraftBase(existing ? { ...existing } : null)
    setLogoDraftFile(null)
    setLogoError(null)
    setSelectedOverlayId(draft.id)
    setTrackFocus('text')
    setPropTab('overlay')
    return draft
  }

  async function stageLogoFile(file: File, iconId?: string) {
    if (logoDraftFile && logoDraft?.assetUrl?.startsWith('blob:')) URL.revokeObjectURL(logoDraft.assetUrl)
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.src = url
    await img.decode()
    const base = editLogo(iconId ? 'icon' : 'image')
    const h = Math.max(24, Math.round(Math.min(sourceWidth, sourceHeight) * .08))
    const w = Math.max(24, Math.round(h * img.naturalWidth / Math.max(1, img.naturalHeight)))
    setLogoDraft({ ...base, logoSource: iconId ? 'icon' : 'image', iconId, assetUrl: url, w, h })
    setLogoDraftFile(file)
  }

  function unapplyLogo() {
    const applied = overlays.find((o) => o.kind === 'logo')
    if (!applied) return
    const preserved = logoDraft ?? applied
    pushHistory()
    onOverlayDelete(applied.id)
    // Keep one shared draft so both left Assets and right Properties switch
    // back to "Apply logo" instead of one panel disappearing.
    setLogoDraft({ ...preserved, positionKeyframes: [] })
    setLogoDraftBase(null)
    setLogoDraftFile(null)
    setLogoError(null)
    setSelectedOverlayId(preserved.id)
    setTrackFocus('text')
    setPropTab('overlay')
  }

  async function applyLogoDraft() {
    if (!logoDraft || logoApplying) return
    setLogoApplying(true)
    setLogoError(null)
    try {
      let next = { ...logoDraft }
      if (logoDraftFile) {
        const uploaded = await api.uploadLogoAsset(projectId, logoDraftFile)
        next.assetUrl = uploaded.url
        URL.revokeObjectURL(logoDraft.assetUrl || '')
      }
      next.positionSeed = Date.now()
      next.positionKeyframes = generateLogoKeyframes(next, timelineDuration, sourceWidth, sourceHeight, segments, next.positionSeed)
      const exists = overlays.some((o) => o.id === next.id)
      pushHistory()
      await editOverlay(next, !exists)
      setLogoDraft(next)
      setLogoDraftBase({ ...next })
      setLogoDraftFile(null)
      setSelectedOverlayId(next.id)
    } catch (e) {
      setLogoError(e instanceof Error ? e.message : String(e))
    } finally {
      setLogoApplying(false)
    }
  }

  async function selectLogoIcon(iconId: 'play' | 'camera' | 'star') {
    const canvas = document.createElement('canvas'); canvas.width = 256; canvas.height = 256
    const ctx = canvas.getContext('2d'); if (!ctx) return
    ctx.fillStyle = '#fff'; ctx.strokeStyle = '#fff'; ctx.lineWidth = 22; ctx.lineJoin = 'round'
    if (iconId === 'play') { ctx.beginPath(); ctx.moveTo(72, 42); ctx.lineTo(210, 128); ctx.lineTo(72, 214); ctx.closePath(); ctx.fill() }
    if (iconId === 'camera') { ctx.strokeRect(34, 70, 188, 132); ctx.strokeRect(82, 45, 92, 30); ctx.beginPath(); ctx.arc(128, 136, 48, 0, Math.PI * 2); ctx.stroke() }
    if (iconId === 'star') { ctx.beginPath(); for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5; const r = i % 2 ? 52 : 108; const x = 128 + Math.cos(a) * r; const y = 128 + Math.sin(a) * r; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y) } ctx.closePath(); ctx.fill() }
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
    if (blob) await stageLogoFile(new File([blob], `${iconId}.png`, { type: 'image/png' }), iconId)
  }

  /** Thêm vùng hiệu ứng (làm mờ / màu / khối) — khung tự do, kéo + resize. */
  function addEffectOverlay(
    preset: (typeof EFFECT_PRESETS)[number],
    clientX?: number,
    clientY?: number,
  ) {
    const rect = canvasRef.current?.getBoundingClientRect()
    const fw = Math.max(1, sourceWidth)
    const fh = Math.max(1, sourceHeight)
    const defaultW = Math.round(fw * 0.42)
    const defaultH = Math.round(fh * 0.12)
    let x = Math.round(crop.x + crop.w * 0.29)
    let y = Math.round(crop.y + crop.h * 0.72)
    if (rect && clientX !== undefined && clientY !== undefined) {
      x = Math.round(
        crop.x + Math.max(0, Math.min(crop.w - defaultW, ((clientX - rect.left) / rect.width) * crop.w - defaultW / 2)),
      )
      y = Math.round(
        crop.y + Math.max(0, Math.min(crop.h - defaultH, ((clientY - rect.top) / rect.height) * crop.h - defaultH / 2)),
      )
    }
    const overlay: TextOverlay = {
      id: crypto.randomUUID(),
      start: time,
      end: Math.min(timelineDuration || time + 4, time + 4),
      text: preset.label,
      x,
      y,
      w: defaultW,
      h: defaultH,
      fontSize: 0,
      color: '#ffffff',
      kind: 'effect',
      maskStyle: preset.maskStyle,
      maskColor: preset.maskColor,
      maskOpacity: preset.maskOpacity,
    }
    setSelectedOverlayId(overlay.id)
    setTrackFocus('text')
    setTool('select')
    setPropTab('overlay')
    setAssetsTab('effects')
    pushHistory()
    editOverlay(overlay, true)
  }

  function beginOverlayResize(
    event: ReactPointerEvent,
    overlay: TextOverlay,
    edge: 'nw' | 'ne' | 'sw' | 'se' | 'e' | 's' | 'w' | 'n',
  ) {
    if (timelineEditLocked || trackLocked.text) return
    const canvas = canvasRef.current
    if (!canvas) return
    event.preventDefault()
    event.stopPropagation()
    const rect = canvas.getBoundingClientRect()
    const orig = { x: overlay.x, y: overlay.y, w: overlay.w, h: overlay.h }
    setSelectedOverlayId(overlay.id)
    setTrackFocus(isWatermarkOverlay(overlay) ? 'watermark' : 'text')
    setPropTab('overlay')
    const histGate = { current: false }

    const update = (move: PointerEvent) => {
      const dx = ((move.clientX - event.clientX) / rect.width) * crop.w
      const dy = ((move.clientY - event.clientY) / rect.height) * crop.h
      let { x, y, w, h } = orig
      const minW = 24
      const minH = 16
      if (edge.includes('e')) w = Math.max(minW, orig.w + dx)
      if (edge.includes('s')) h = Math.max(minH, orig.h + dy)
      if (edge.includes('w')) {
        const nw = Math.max(minW, orig.w - dx)
        x = orig.x + (orig.w - nw)
        w = nw
      }
      if (edge.includes('n')) {
        const nh = Math.max(minH, orig.h - dy)
        y = orig.y + (orig.h - nh)
        h = nh
      }
      if (overlay.kind === 'logo' && overlay.logoSource !== 'text' && (edge.length === 2)) {
        const ratio = orig.w / Math.max(1, orig.h)
        if (Math.abs(dx) >= Math.abs(dy)) h = w / ratio
        else w = h * ratio
      }
      x = Math.max(0, Math.min(sourceWidth - w, Math.round(x)))
      y = Math.max(0, Math.min(sourceHeight - h, Math.round(y)))
      w = Math.round(Math.min(w, sourceWidth - x))
      h = Math.round(Math.min(h, sourceHeight - y))
      const next = { ...overlay, x, y, w, h }
      if (
        Math.abs(next.x - orig.x) > 1
        || Math.abs(next.y - orig.y) > 1
        || Math.abs(next.w - orig.w) > 1
        || Math.abs(next.h - orig.h) > 1
      ) {
        pushHistoryOnce(histGate)
      }
      if (logoDraft?.id === overlay.id) setLogoDraft(next)
      else onOverlayChange(next)
    }
    const commit = () => {
      window.removeEventListener('pointermove', update)
      window.removeEventListener('pointerup', commit)
    }
    window.addEventListener('pointermove', update)
    window.addEventListener('pointerup', commit, { once: true })
  }

  function focusCaption(seg: Segment, opts?: { additive?: boolean; range?: boolean }) {
    setSelectedOverlayId(null)
    setSelectedMediaId(null)
    setTrackFocus('caption')
    setPropTab('caption')
    if (opts?.range && selectedId) {
      const lane = captionLaneOf(seg, sourceHeight, sourceWidth)
      const laneSegs = segments
        .filter((s) => captionLaneOf(s, sourceHeight, sourceWidth) === lane)
        .slice()
        .sort((a, b) => a.start - b.start)
      const a = laneSegs.findIndex((s) => s.id === selectedId)
      const b = laneSegs.findIndex((s) => s.id === seg.id)
      if (a >= 0 && b >= 0) {
        const lo = Math.min(a, b)
        const hi = Math.max(a, b)
        const ids = expandGroupSelection(laneSegs.slice(lo, hi + 1).map((s) => s.id))
        setSelectedIds(ids)
        setSelectedId(seg.id)
        return
      }
    }
    if (opts?.additive) {
      setSelectedIds((prev) => {
        if (prev.includes(seg.id)) {
          const next = prev.filter((id) => id !== seg.id)
          // Bỏ cả groupmates nếu unselect 1 member
          const gid = seg.groupId
          const cleaned = gid
            ? next.filter((id) => {
                const s = segments.find((x) => x.id === id)
                return s?.groupId !== gid
              })
            : next
          setSelectedId(cleaned[cleaned.length - 1] ?? null)
          return cleaned
        }
        setSelectedId(seg.id)
        return expandGroupSelection([...prev, seg.id])
      })
      return
    }
    setSelectedId(seg.id)
    // Click đơn: chọn cả group nếu có
    setSelectedIds(expandGroupSelection([seg.id]))
  }

  function focusDub(seg: Segment, opts?: { keepMulti?: boolean }) {
    setSelectedOverlayId(null)
    setSelectedMediaId(null)
    setSelectedId(seg.id)
    if (!opts?.keepMulti) {
      setSelectedIds([])
      setSelectedDubIds([seg.id])
    }
    setTrackFocus('dub')
    setPropTab('audio')
  }

  function focusBg(clipId?: string) {
    setSelectedOverlayId(null)
    setTrackFocus('bg')
    setPropTab('audio')
    const clip = (clipId ? bgClips.find((c) => c.id === clipId) : null)
      ?? clipAtTime(bgClips, time)
      ?? bgClips[0]
    setSelectedMediaId(clip?.id ?? null)
  }

  function focusVideo(clipId?: string) {
    setSelectedOverlayId(null)
    setSelectedId(null)
    setSelectedIds([])
    setTrackFocus('video')
    setPropTab('video')
    if (tool === 'cover') setTool('select')
    const clip = (clipId ? videoClips.find((c) => c.id === clipId) : null)
      ?? clipAtTime(videoClips, time)
      ?? videoClips[0]
    setSelectedMediaId(clip?.id ?? null)
  }

  function focusText(overlayId: string) {
    const overlay = overlays.find((item) => item.id === overlayId)
    if (overlay?.kind === 'logo') {
      editLogo(overlay.logoSource ?? 'text')
      return
    }
    setSelectedOverlayId(overlayId)
    setSelectedMediaId(null)
    setTrackFocus(overlay && isWatermarkOverlay(overlay) ? 'watermark' : 'text')
    setPropTab('overlay')
  }

  /** Chọn clip: giữ playhead nếu đã trong [start,end) hoặc cover pad (mid/OCR). */
  function selectClipKeepPlayhead(start: number, end: number, cover?: { start: number; end: number }) {
    const lo = cover ? Math.min(start, cover.start) : start
    const hi = cover ? Math.max(end, cover.end) : end
    if (time < lo || time >= hi) {
      const mid = start + Math.max(SPLIT_EDGE, Math.min((end - start) / 2, end - start - SPLIT_EDGE))
      seekPlayhead(mid)
    }
  }

  function rangeUnderPlayhead(start: number, end: number) {
    return time > start + SPLIT_EDGE && time < end - SPLIT_EDGE
  }

  function segmentTimelineRange(seg: Segment, lane: 'caption' | 'dub') {
    if (lane === 'caption') return { start: seg.start, end: seg.end }
    const videoRate = previewVideoRate(
      settings.matchDuration,
      bakedPreferVideo,
      seg.videoSpeed,
      bakedSpeed,
      hasBakedSpeed,
    )
    return {
      start: seg.start,
      end: seg.start + dubClipSeconds(seg, segments, videoRate, bakedSpeed),
    }
  }

  type ToolTarget =
    | { kind: 'seg'; seg: Segment }
    | { kind: 'ov'; ov: TextOverlay }
    | { kind: 'media'; track: 'video' | 'bg'; clip: MediaClip }

  /** Target = đúng track đang focus (Video / Âm gốc / Caption / TTS / Text độc lập) */
  const editTarget: ToolTarget | null = (() => {
    if (trackFocus === 'video') {
      const byId = selectedMediaId ? videoClips.find((c) => c.id === selectedMediaId) : undefined
      const under = clipAtTime(videoClips, time)
      const clip = (byId && rangeUnderPlayhead(byId.start, byId.end) ? byId : null) || under || byId
      return clip ? { kind: 'media', track: 'video', clip } : null
    }
    if (trackFocus === 'bg') {
      const byId = selectedMediaId ? bgClips.find((c) => c.id === selectedMediaId) : undefined
      const under = clipAtTime(bgClips, time)
      const clip = (byId && rangeUnderPlayhead(byId.start, byId.end) ? byId : null) || under || byId
      return clip ? { kind: 'media', track: 'bg', clip } : null
    }
    if (trackFocus === 'text' || trackFocus === 'watermark') {
      return selectedOverlay ? { kind: 'ov', ov: selectedOverlay } : null
    }
    if (trackFocus === 'caption' || trackFocus === 'dub') {
      const lane = trackFocus
      const selectedRange = selected ? segmentTimelineRange(selected, lane) : null
      if (selected && selectedRange && rangeUnderPlayhead(selectedRange.start, selectedRange.end)) {
        return { kind: 'seg', seg: selected }
      }
      const at = lane === 'dub'
        ? segments.find((seg) => {
            if (seg.isCompound || !segmentHasDub(seg) || !seg.audioUrl) return false
            const range = segmentTimelineRange(seg, 'dub')
            return rangeUnderPlayhead(range.start, range.end)
          })
        : segmentAt(segments, time)
      if (at) {
        const range = segmentTimelineRange(at, lane)
        if (rangeUnderPlayhead(range.start, range.end)) return { kind: 'seg', seg: at }
      }
      return selected ? { kind: 'seg', seg: selected } : null
    }
    return null
  })()

  function clipRange(target: NonNullable<typeof editTarget>) {
    if (target.kind === 'seg') {
      return segmentTimelineRange(target.seg, trackFocus === 'dub' ? 'dub' : 'caption')
    }
    if (target.kind === 'ov') return { start: target.ov.start, end: target.ov.end }
    return { start: target.clip.start, end: target.clip.end }
  }

  const playheadInClip = (() => {
    if (!editTarget) return false
    const { start, end } = clipRange(editTarget)
    return rangeUnderPlayhead(start, end)
  })()
  const canTrimLeft = (() => {
    if (!editTarget || timelineEditLocked) return false
    const { start, end } = clipRange(editTarget)
    return time > start + 0.02 && time <= end - MIN_CLIP_SEC
  })()
  const canTrimRight = (() => {
    if (!editTarget || timelineEditLocked) return false
    const { start, end } = clipRange(editTarget)
    return time >= start + MIN_CLIP_SEC && time < end - 0.02
  })()
  const canSplit = Boolean(
    editTarget &&
      !timelineEditLocked &&
      playheadInClip &&
      clipRange(editTarget).end - clipRange(editTarget).start > SPLIT_EDGE * 2 + 0.02,
  )
  const canDuplicate = Boolean(editTarget && !timelineEditLocked)
  const canDeleteClip = Boolean(
    editTarget &&
      !timelineEditLocked &&
      !(editTarget.kind === 'media' && (editTarget.track === 'video' ? videoClips : bgClips).length <= 1),
  )
  const bookmarkActive = bookmarks.some((b) => Math.abs(b - time) <= BOOKMARK_EPS)

  const splitDisabledReason = !editTarget
    ? 'Click chọn clip trên track (Video / Caption / TTS / Âm gốc / Text) trước'
    : !playheadInClip
      ? 'Đặt playhead vào giữa clip đang chọn rồi Split'
      : ''

  function seekPlayhead(next: number) {
    const video = videoRef.current
    const clamped = Math.max(0, Math.min(timelineDuration, next))
    if (video) video.currentTime = timelineToVideoTime(clamped)
    setTime(clamped)
    const current = segmentAt(segments, clamped)
    if (current) setSelectedId(current.id)
  }

  function splitAtPlayhead() {
    if (!editTarget || !canSplit) return
    pushHistory()
    const t = time
    if (editTarget.kind === 'ov') {
      const ov = editTarget.ov
      editOverlay({ ...ov, end: t })
      editOverlay(
        { ...ov, id: crypto.randomUUID(), start: t, end: ov.end },
        true,
      )
      return
    }
    if (editTarget.kind === 'media') {
      if (editTarget.track === 'video') {
        const next = splitMediaList(videoClips, editTarget.clip.id, t)
        setVideoClips(next)
        if (mediaLinked) {
          const bgClip = clipAtTime(bgClips, t)
          if (bgClip) setBgClips(splitMediaList(bgClips, bgClip.id, t))
        }
        setSelectedMediaId(next.find((c) => c.start === t)?.id ?? null)
      } else {
        const next = splitMediaList(bgClips, editTarget.clip.id, t)
        setBgClips(next)
        setSelectedMediaId(next.find((c) => c.start === t)?.id ?? null)
      }
      return
    }
    const seg = editTarget.seg
    const left: Segment = { ...seg, end: t }
    const right: Segment = {
      ...seg,
      id: crypto.randomUUID(),
      start: t,
      end: seg.end,
      audioUrl: undefined,
      audioFile: undefined,
      audioDuration: undefined,
      captionLayout: null,
    }
    if (trackFocus === 'dub') {
      left.dub = seg.dub
      right.dub = true
    }
    void onSegmentsReplace(
      reindexSegments(segments.flatMap((s) => (s.id === seg.id ? [left, right] : [s]))),
    )
    setSelectedId(right.id)
  }

  function trimLeftToPlayhead() {
    if (!editTarget || !canTrimLeft) return
    pushHistory()
    const t = time
    if (editTarget.kind === 'ov') {
      editOverlay(
        { ...editTarget.ov, start: Math.min(t, editTarget.ov.end - MIN_CLIP_SEC) },
        false,
        { skipHistory: true },
      )
      return
    }
    if (editTarget.kind === 'media') {
      const start = Math.min(t, editTarget.clip.end - MIN_CLIP_SEC)
      if (editTarget.track === 'video' && mainTrackMagnet) {
        const removed = mergeTimeRanges([{ start: editTarget.clip.start, end: start }])
        if (!removed.length) return
        const shiftMedia = (list: MediaClip[]) => list
          .map((c) => {
            const nextStart = mapTimeAfterRipple(c.start, removed)
            const nextEnd = mapTimeAfterRipple(c.end, removed)
            return {
              ...c,
              start: nextStart,
              end: nextEnd,
              sourceStart: c.id === editTarget.clip.id
                ? (c.sourceStart ?? c.start) + start - c.start
                : c.sourceStart,
            }
          })
          .filter((c) => c.end - c.start >= SPLIT_EDGE)
        setVideoClips(shiftMedia)
        void onSegmentsReplace(reindexSegments(
          segments
            .map((s) => rippleShiftSegment(s, removed))
            .filter((s): s is Segment => Boolean(s)),
        ))
        void onOverlaysReplace(
          overlays
            .map((o) => rippleShiftOverlay(o, removed))
            .filter((o): o is TextOverlay => Boolean(o)),
        )
        if (mediaLinked) setBgClips(shiftMedia)
        setBookmarks((prev) => prev
          .map((bookmark) => mapTimeAfterRipple(bookmark, removed))
          .filter((bookmark, index, list) => list.findIndex((item) => Math.abs(item - bookmark) < 0.02) === index)
          .sort((a, b) => a - b))
        const nextTime = mapTimeAfterRipple(t, removed)
        if (videoRef.current) videoRef.current.currentTime = start
        setTime(nextTime)
        return
      }
      const patch = (list: MediaClip[]) =>
        list.map((c) => (c.id === editTarget.clip.id ? { ...c, start } : c))
      if (editTarget.track === 'video') {
        setVideoClips(patch)
        if (mediaLinked) setBgClips((list) => list.map((c) =>
          Math.abs(c.start - editTarget.clip.start) <= 0.02 && Math.abs(c.end - editTarget.clip.end) <= 0.02
            ? { ...c, start }
            : c,
        ))
      } else setBgClips(patch)
      return
    }
    const seg = editTarget.seg
    void editSegment(
      { ...seg, start: Math.min(t, seg.end - MIN_CLIP_SEC), captionLayout: null },
      { skipHistory: true },
    )
  }

  function trimRightToPlayhead() {
    if (!editTarget || !canTrimRight) return
    pushHistory()
    const t = time
    const dubTrimAt = (seg: Segment, boundary: number) => {
      const videoRate = previewVideoRate(
        settings.matchDuration,
        bakedPreferVideo,
        seg.videoSpeed,
        bakedSpeed,
        hasBakedSpeed,
      )
      const speed = dubPlaybackSpeed(seg, bakedSpeed)
      return {
        videoRate,
        rawDuration: Math.max(
          0.05,
          ((boundary - seg.start - 0.04) * speed) / Math.max(0.2, videoRate),
        ),
      }
    }
    if (editTarget.kind === 'ov') {
      editOverlay(
        { ...editTarget.ov, end: Math.max(t, editTarget.ov.start + MIN_CLIP_SEC) },
        false,
        { skipHistory: true },
      )
      return
    }
    if (editTarget.kind === 'media') {
      const end = Math.max(t, editTarget.clip.start + MIN_CLIP_SEC)
      const patch = (list: MediaClip[]) =>
        list.map((c) => (c.id === editTarget.clip.id ? { ...c, end } : c))
      if (editTarget.track === 'video') {
        const trimsTimelineTail = !videoClips.some(
          (clip) => clip.id !== editTarget.clip.id && clip.end > editTarget.clip.end + 0.02,
        )
        const affectedEnd = trimsTimelineTail
          ? Number.POSITIVE_INFINITY
          : editTarget.clip.end
        const previousById = new Map(segments.map((item) => [item.id, item] as const))
        const capDubAtBoundary = (
          next: Segment,
          previous: Segment,
          boundary: number,
          rangeEnd: number,
        ): Segment => {
          let out = next
          if (next.isCompound && next.compoundChildren?.length && previous.compoundChildren?.length) {
            const previousChildren = new Map(
              previous.compoundChildren.map((item) => [item.id, item] as const),
            )
            out = {
              ...out,
              compoundChildren: next.compoundChildren.map((child) => {
                const previousChild = previousChildren.get(child.id)
                return previousChild
                  ? capDubAtBoundary(
                      child,
                      previousChild,
                      boundary - next.start,
                      rangeEnd - next.start,
                    )
                  : child
              }),
            }
          }
          if (
            previous.start >= boundary
            || previous.start >= rangeEnd
            || !segmentHasDub(previous)
            || (!previous.audioUrl && !previous.audioFile && !(previous.audioDuration && previous.audioDuration > 0.05))
          ) {
            return out
          }
          const { videoRate, rawDuration } = dubTrimAt(previous, boundary)
          const dubEnd = previous.start + dubClipSeconds(
            previous,
            segments,
            videoRate,
            bakedSpeed,
          )
          if (dubEnd <= boundary + 0.02) return out
          return {
            ...out,
            audioDuration: Math.min(previous.audioDuration ?? rawDuration, rawDuration),
          }
        }
        const trimmedSegments = trimSegmentsForVideoRight(
          segments,
          end,
          editTarget.clip.end,
          trimsTimelineTail,
        ).map((item) => {
          const previous = previousById.get(item.id)
          return previous
            ? capDubAtBoundary(item, previous, end, affectedEnd)
            : item
        })
        pauseDubAudio()
        void onSegmentsReplace(reindexSegments(trimmedSegments))
        setVideoClips(patch)
        if (mediaLinked) setBgClips((list) => list.map((c) =>
          Math.abs(c.start - editTarget.clip.start) <= 0.02 && Math.abs(c.end - editTarget.clip.end) <= 0.02
            ? { ...c, end }
            : c,
        ))
      } else setBgClips(patch)
      return
    }
    const seg = editTarget.seg
    if (trackFocus === 'dub') {
      const { rawDuration } = dubTrimAt(seg, t)
      void editSegment(
        {
          ...seg,
          audioDuration: Math.min(seg.audioDuration ?? rawDuration, rawDuration),
        },
        { skipHistory: true },
      )
      return
    }
    void editSegment({
      ...seg,
      end: Math.max(t, seg.start + MIN_CLIP_SEC),
      captionLayout: null,
      audioUrl: undefined,
      audioFile: undefined,
      audioDuration: undefined,
    }, { skipHistory: true })
  }

  function duplicateClip() {
    if (!editTarget || !canDuplicate) return
    pushHistory()
    if (editTarget.kind === 'ov') {
      const ov = editTarget.ov
      const dur = ov.end - ov.start
      const start = Math.min(timelineDuration - MIN_CLIP_SEC, ov.end)
      editOverlay(
        { ...ov, id: crypto.randomUUID(), start, end: Math.min(timelineDuration, start + dur) },
        true,
      )
      return
    }
    if (editTarget.kind === 'media') {
      const c = editTarget.clip
      const dur = c.end - c.start
      const start = Math.min(timelineDuration - MIN_CLIP_SEC, c.end)
      const copy: MediaClip = {
        id: crypto.randomUUID(),
        start,
        end: Math.min(timelineDuration, start + dur),
      }
      if (editTarget.track === 'video') {
        setVideoClips((list) => [...list, copy].sort((a, b) => a.start - b.start))
      } else {
        setBgClips((list) => [...list, copy].sort((a, b) => a.start - b.start))
      }
      setSelectedMediaId(copy.id)
      return
    }
    const seg = editTarget.seg
    const dur = seg.end - seg.start
    const start = Math.min(timelineDuration - MIN_CLIP_SEC, seg.end)
    const copy: Segment = {
      ...seg,
      id: crypto.randomUUID(),
      start,
      end: Math.min(timelineDuration, start + dur),
      audioUrl: undefined,
      audioFile: undefined,
      audioDuration: undefined,
      captionLayout: null,
    }
    void onSegmentsReplace(reindexSegments([...segments, copy]))
    setSelectedId(copy.id)
  }

  function removeDubClips(ids: string[], recordHistory = true) {
    const drop = new Set(ids.filter((id) => segments.some((seg) => seg.id === id)))
    if (!drop.size) return
    if (recordHistory) pushHistory()
    void onSegmentsReplace(segments.map((seg) => (
      drop.has(seg.id)
        ? {
            ...seg,
            dub: false,
            audioUrl: undefined,
            audioFile: undefined,
            audioDuration: undefined,
          }
        : seg
    )))
    pauseDubAudio()
    dubTokenRef.current = ''
    setSelectedId(null)
    setSelectedDubIds([])
  }

  function deleteSelectedClip() {
    if (!editTarget || !canDeleteClip) return
    pushHistory()
    if (editTarget.kind === 'ov') {
      onOverlayDelete(editTarget.ov.id)
      setSelectedOverlayId(null)
      return
    }
    if (editTarget.kind === 'media') {
      // Multi-select: xóa + ripple đóng gap (kéo phần sau về trước)
      const drop = new Set(
        selectedMediaIds.length > 0 ? selectedMediaIds : [editTarget.clip.id],
      )
      drop.add(editTarget.clip.id)
      const src = editTarget.track === 'video' ? videoClips : bgClips
      const deleted = src.filter((c) => drop.has(c.id))
      const { next, removed } = rippleDeleteMediaClips(src, drop)
      const withoutDeleted = src.filter((c) => !drop.has(c.id))
      const result = mainTrackMagnet ? next : withoutDeleted
      const packed = result.length ? result : [fullMediaClip(timelineDuration)]
      if (editTarget.track === 'video') {
        setVideoClips(packed)
        // Ripple toàn project: caption / TTS / text / âm gốc theo cùng vùng xóa
        if (mainTrackMagnet && removed.length) {
          const segs = reindexSegments(
            segments
              .map((s) => rippleShiftSegment(s, removed))
              .filter((s): s is Segment => Boolean(s)),
          )
          void onSegmentsReplace(segs)
          const ovs = overlays
            .map((o) => rippleShiftOverlay(o, removed))
            .filter((o): o is TextOverlay => Boolean(o))
          void onOverlaysReplace(ovs)
          if (mediaLinked) setBgClips((list) => {
            const shifted = list
              .map((c) => {
                const start = mapTimeAfterRipple(c.start, removed)
                const end = mapTimeAfterRipple(c.end, removed)
                return { ...c, start, end: Math.max(start + MIN_CLIP_SEC, end) }
              })
              .filter((c) => c.end - c.start >= SPLIT_EDGE)
            return shifted.length ? shifted : [fullMediaClip(timelineDuration)]
          })
          setBookmarks((prev) =>
            prev
              .map((b) => mapTimeAfterRipple(b, removed))
              .filter((b, i, arr) => arr.findIndex((x) => Math.abs(x - b) < 0.02) === i)
              .sort((a, b) => a - b),
          )
          // Playhead: kéo về theo ripple
          const tNew = mapTimeAfterRipple(time, removed)
          const vid = videoRef.current
          if (vid) {
            try {
              vid.currentTime = timelineToVideoTime(tNew)
            } catch { /* ignore */ }
          }
          setTime(tNew)
        }
        if (!mainTrackMagnet && mediaLinked && deleted.length) {
          setBgClips((list) => list.filter((c) => !deleted.some((d) =>
            Math.abs(c.start - d.start) <= 0.02 && Math.abs(c.end - d.end) <= 0.02,
          )))
        }
        setSelectedMediaId(packed[0]?.id ?? null)
        setSelectedMediaIds([])
      } else {
        // Âm gốc: ripple chỉ track bg (không đụng video/caption)
        setBgClips(packed)
        if (removed.length) {
          const tNew = mapTimeAfterRipple(time, removed)
          setTime(tNew)
        }
        setSelectedMediaId(packed[0]?.id ?? null)
        setSelectedMediaIds([])
      }
      return
    }
    // Caption / TTS: xóa + ripple đóng gap toàn timeline
    if (trackFocus === 'dub') {
      removeDubClips(
        [...new Set([...selectedDubIds, editTarget.seg.id])],
        false,
      )
      return
    }
    const id = editTarget.seg.id
    const dropSeg = segments.find((s) => s.id === id)
    if (!dropSeg) return
    const removed = mergeTimeRanges([{ start: dropSeg.start, end: dropSeg.end }])
    const segs = reindexSegments(
      segments
        .filter((s) => s.id !== id)
        .map((s) => rippleShiftSegment(s, removed))
        .filter((s): s is Segment => Boolean(s)),
    )
    void onSegmentsReplace(segs)
    if (removed.length) {
      const ovs = overlays
        .map((o) => rippleShiftOverlay(o, removed))
        .filter((o): o is TextOverlay => Boolean(o))
      void onOverlaysReplace(ovs)
      setVideoClips((list) => {
        const shifted = list
          .map((c) => {
            const start = mapTimeAfterRipple(c.start, removed)
            const end = mapTimeAfterRipple(c.end, removed)
            return { ...c, start, end: Math.max(start + MIN_CLIP_SEC, end) }
          })
          .filter((c) => c.end - c.start >= SPLIT_EDGE)
        return shifted.length ? shifted : list
      })
      setBgClips((list) => {
        const shifted = list
          .map((c) => {
            const start = mapTimeAfterRipple(c.start, removed)
            const end = mapTimeAfterRipple(c.end, removed)
            return { ...c, start, end: Math.max(start + MIN_CLIP_SEC, end) }
          })
          .filter((c) => c.end - c.start >= SPLIT_EDGE)
        return shifted.length ? shifted : list
      })
      const tNew = mapTimeAfterRipple(time, removed)
      const vid = videoRef.current
      if (vid) {
        try {
          vid.currentTime = timelineToVideoTime(tNew)
        } catch { /* ignore */ }
      }
      setTime(tNew)
    }
    setSelectedId(segs[0]?.id ?? null)
  }

  function extractAudioFromVideo() {
    pushHistory()
    onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
    setPropTab('audio')
  }

  function toggleBookmarkAtPlayhead() {
    pushHistory()
    const t = Math.round(time * 1000) / 1000
    setBookmarks((prev) => {
      const hit = prev.find((b) => Math.abs(b - t) <= BOOKMARK_EPS)
      if (hit !== undefined) return prev.filter((b) => b !== hit)
      return [...prev, t].sort((a, b) => a - b)
    })
  }

  function togglePlay() {
    const video = videoRef.current
    if (!video) return
    if (video.paused) {
      void video.play().catch(() => { /* requires gesture */ })
    } else {
      video.pause()
      pauseDubAudio()
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void previewRef.current?.requestFullscreen()
  }

  /* Keyboard shortcuts (OpenCut-style). No dependency array on purpose:
     re-registering each render keeps every closure fresh (time, segments, selection). */
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement)?.matches('input, textarea, select')) return
      const video = videoRef.current

      const seekTo = (next: number) => {
        if (!video) return
        const clamped = Math.max(0, Math.min(timelineDuration, next))
        video.currentTime = timelineToVideoTime(clamped)
        setTime(clamped)
        const current = segmentAt(segments, clamped)
        if (current) setSelectedId(current.id)
      }
      const seekBy = (delta: number) => { if (video) seekTo(videoToTimelineTime(video.currentTime) + delta) }
      const stepSegment = (dir: -1 | 1) => {
        const index = segments.findIndex((s) => s.id === selected?.id)
        const next = segments[index + dir]
        if (next) { setSelectedId(next.id); seekTo(next.start) }
      }

      switch (event.code) {
        case 'KeyZ':
          if (event.ctrlKey || event.metaKey) {
            event.preventDefault()
            if (event.shiftKey) redoEdit()
            else undoEdit()
          }
          break
        case 'KeyY':
          if (event.ctrlKey || event.metaKey) {
            event.preventDefault()
            redoEdit()
          } else {
            event.preventDefault()
            setAutoSnapping((value) => !value)
          }
          break
        case 'Space':
        case 'KeyK':
          event.preventDefault()
          if (video) void (video.paused ? video.play() : video.pause())
          break
        case 'KeyJ': event.preventDefault(); seekBy(-5); break
        case 'KeyL': event.preventDefault(); seekBy(5); break
        case 'ArrowLeft':  event.preventDefault(); seekBy(event.shiftKey ? -1 : -1 / 30); break
        case 'ArrowRight': event.preventDefault(); seekBy(event.shiftKey ? 1 : 1 / 30); break
        case 'ArrowUp':    event.preventDefault(); stepSegment(-1); break
        case 'ArrowDown':  event.preventDefault(); stepSegment(1); break
        case 'Home': event.preventDefault(); seekTo(0); break
        case 'End':  event.preventDefault(); seekTo(timelineDuration); break
        case 'KeyT':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); setMainTrackMagnet((value) => !value) }
          break
        case 'KeyS':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); splitAtPlayhead() }
          break
        case 'KeyB':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); toggleBookmarkAtPlayhead() }
          break
        case 'KeyF':
          if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); toggleFullscreen() }
          break
        case 'Escape':
          setSelectedOverlayId(null)
          setTool('select')
          setSelectedIds(selectedId ? [selectedId] : [])
          setSelectedMediaIds([])
          setSelectedDubIds([])
          break
        case 'KeyG':
          // CapCut: Alt+G = compound; Ctrl+G = group; Ctrl+Shift+G = ungroup/uncompound
          if (event.altKey && !event.ctrlKey && !event.metaKey) {
            event.preventDefault()
            createCompoundFromSelection()
            break
          }
          if (event.ctrlKey || event.metaKey) {
            event.preventDefault()
            if (event.shiftKey) {
              const cur = segments.find((s) => s.id === (selectedId || selectedIds[0]))
              if (cur?.isCompound) uncompoundSelected()
              else ungroupSelectedCaptions()
            } else {
              groupSelectedCaptions()
            }
          }
          break
        case 'KeyM':
          // Compound (cùng Alt+G) — giữ tương thích
          if ((event.ctrlKey || event.metaKey) && !event.shiftKey) {
            event.preventDefault()
            createCompoundFromSelection()
          }
          break
        case 'KeyD':
          if ((event.ctrlKey || event.metaKey) && !event.shiftKey) {
            event.preventDefault()
            if (canDuplicate) duplicateClip()
          }
          break
        case 'KeyC':
          if ((event.ctrlKey || event.metaKey) && !event.shiftKey && trackFocus === 'caption') {
            event.preventDefault()
            try {
              const ids = expandGroupSelection(selectedIds.length ? selectedIds : selectedId ? [selectedId] : [])
              const payload = segments.filter((s) => ids.includes(s.id))
              if (payload.length) {
                sessionStorage.setItem(
                  'vc-editor-clip-clipboard',
                  JSON.stringify(payload.map(({ id: _i, index: _x, ...rest }) => rest)),
                )
              }
            } catch { /* ignore */ }
          }
          break
        case 'KeyV':
          if ((event.ctrlKey || event.metaKey) && !event.shiftKey && trackFocus === 'caption') {
            event.preventDefault()
            try {
              const raw = sessionStorage.getItem('vc-editor-clip-clipboard')
              if (!raw) break
              const items = JSON.parse(raw) as Omit<Segment, 'id' | 'index'>[]
              if (!Array.isArray(items) || !items.length) break
              pushHistory()
              const t0 = time
              const base = Math.min(...items.map((s) => Number(s.start) || 0))
              const pasted: Segment[] = items.map((s, i) => {
                const dur = Math.max(0.15, (Number(s.end) || 0) - (Number(s.start) || 0))
                const st = t0 + ((Number(s.start) || 0) - base)
                return {
                  ...s,
                  id: `paste_${Date.now().toString(36)}_${i}`,
                  index: 0,
                  start: st,
                  end: st + dur,
                  groupId: s.groupId ? `g_paste_${Date.now().toString(36)}` : undefined,
                } as Segment
              })
              // cùng paste batch → 1 group mới nếu clipboard đã group
              if (items.some((s) => s.groupId)) {
                const gid = `g_paste_${Date.now().toString(36)}`
                for (const p of pasted) p.groupId = gid
              }
              const next = reindexSegments([...segments, ...pasted])
              void onSegmentsReplace(next)
              setSelectedId(pasted[0].id)
              setSelectedIds(pasted.map((p) => p.id))
            } catch { /* ignore */ }
          }
          break
        case 'KeyA':
          if ((event.ctrlKey || event.metaKey) && trackFocus === 'caption') {
            const anchor = segments.find((s) => s.id === selectedId) ?? segments[0]
            if (anchor) {
              event.preventDefault()
              const lane = captionLaneOf(anchor, sourceHeight, sourceWidth)
              setSelectedIds(segments.filter((s) => captionLaneOf(s, sourceHeight, sourceWidth) === lane).map((s) => s.id))
              if (!selectedId) setSelectedId(anchor.id)
            }
          }
          break
        case 'Delete':
        case 'Backspace':
          if (canDeleteClip) {
            event.preventDefault()
            if (trackFocus === 'caption' && selectedIds.length > 1) {
              pushHistory()
              const drop = new Set(expandGroupSelection(selectedIds))
              void onSegmentsReplace(reindexSegments(segments.filter((s) => !drop.has(s.id))))
              setSelectedId(null)
              setSelectedIds([])
            } else {
              deleteSelectedClip()
            }
          }
          break
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  })

  /* Effective properties tab: overlay chỉ khi có overlay; caption/audio/video luôn mở được (mode «Tất cả»). */
  const effectivePropTab: PropTab = (() => {
    if (propTab === 'overlay' && !selectedOverlay && !logoDraft) return selected ? 'caption' : 'video'
    return propTab
  })()
  const isOverlaySeg = isOcrOverlayLayout(selected?.layout)
  const dubOn = selected?.layout === 'vertical' || selected?.layout === 'label'
    ? selected?.dub === true
    : selected?.dub !== false
  const focusCaptionSeg = timelineSeg ?? selected
  const overlayLaidFont =
    captionOverLayout?.fontPx
    ?? (timelineSeg && isOcrOverlayLayout(timelineSeg.layout) && captionOverLayout
      ? fitOverlayFontPx(
          timelineSeg.layout,
          captionOverLayout.cover,
          timelineSeg.translation,
          resolveOverlayFontPreferred(timelineSeg),
        )
      : undefined)
  const placement = captionPlacement(settings)
  // below/above: mid + horizontal — cỡ = bbox che, neo trên/dưới dải OCR
  const activeCaptionMeta = (() => {
    if (!overlayBurnOn || !timelineSeg?.translation.trim() || placement === 'over') {
      return null as null | { box: PixelBox; fontPx: number; lines: string[] }
    }
    if (timelineSeg.layout === 'vertical' || timelineSeg.layout === 'label') return null
    if (timelineSeg.bboxInherited === false) return null
    const laid = resolveBelowAboveLayout(
      timelineSeg,
      settings,
      sourceWidth,
      sourceHeight,
      crop,
      placement,
    )
    if (!laid) return null
    return {
      box: laid.caption,
      fontPx: laid.fontPx ?? resolveCaptionFontSize(timelineSeg, settings, sourceWidth, sourceHeight),
      lines: laid.lines,
    }
  })()
  const activeCaptionBox = activeCaptionMeta?.box ?? null
  const activeCaptionPx =
    activeCaptionMeta?.fontPx
    ?? overlayLaidFont
    ?? resolveCaptionFontSize(focusCaptionSeg ?? undefined, settings, sourceWidth, sourceHeight)
  const showCoverBlur = settings.burnSubs && maskBoxes.length > 0
  const coverMaskStyle = settings.coverMaskStyle ?? 'blur'
  const coverMaskColor = settings.coverMaskColor ?? '#4c1d95'
  const coverMaskOpacity = settings.coverMaskOpacity ?? 40
  const fontSizeOptions = FONT_SIZES.includes(fontSizeDraft) || fontSizeDraft === 0
    ? FONT_SIZES
    : [...FONT_SIZES, fontSizeDraft].sort((a, b) => a - b)

  function commitCoverBox(patch: Partial<PixelBox>) {
    if (!selected) return
    const display: PixelBox = { ...selectedBoxSource, ...patch }
    const norm = clampCoverBox(display, sourceWidth, sourceHeight)
    const prev = clampCoverBox(selectedBoxSource, sourceWidth, sourceHeight)
    const sizeChanged =
      Math.abs(norm.w - prev.w) > 2 || Math.abs(norm.h - prev.h) > 2
    const overlayLay =
      effectiveOverlayLayout(selected, sourceHeight, sourceWidth)
      ?? (isOcrOverlayLayout(selected.layout) ? selected.layout : null)
    if (overlayLay && selected.translation.trim() && settings.burnSubs) {
      const lockFs = resolveOverlayFontPreferred(selected)
      const preferred = sizeChanged
        ? lockFs
        : (lockFs || Number(selected.captionLayout?.fontSize) || 0)
      const laid = layoutOcrOverlay(
        overlayLay,
        norm,
        selected.translation,
        preferred,
        sourceWidth,
        sourceHeight,
      )
      editSegment(segmentWithLayout({ ...selected, bboxInherited: false }, {
        cover: norm,
        caption: laid.caption,
        lines: laid.lines,
        fontPx: laid.fontPx,
      }, laid.fontPx))
      return
    }
    // Ngang: cùng fit lúc kéo — thả không nhảy cỡ
    if (selected.translation.trim() && settings.burnSubs) {
      const layout = fitFixedCoverCaption(
        norm,
        selected.translation,
        sourceWidth,
        sourceHeight,
      )
      const fitFs = layout.fontPx ?? autoFontFromBbox(norm, selected.translation, 0)
      editSegment(segmentWithLayout(
        { ...selected, bboxInherited: false },
        { ...layout, cover: norm },
        fitFs,
      ))
      return
    }
    editSegment({ ...selected, bbox: norm, bboxInherited: false, captionLayout: null })
  }

  /** Kéo vùng che full ngang (~96% khung), giữ Y/Cao hiện tại. */
  function stretchCoverFullWidth() {
    if (!selected || sourceWidth <= 0) return
    const cur = selectedBox
    const w = Math.min(sourceWidth, Math.round(sourceWidth * 0.96))
    const x = Math.round((sourceWidth - w) / 2)
    commitCoverBox({ x, w, y: cur.y, h: cur.h })
  }

  /**
   * Áp **vị trí** khung che (Y):
   * - full: cùng lane (Caption / CAP-MID / …)
   * - range Từ→đến: **mọi bbox** chồng khoảng thời gian (không lọc lane)
   * Giữ W/H/X từng clip — chỉ dời Y.
   */
  function applyCoverMaskToAll(
    range?: { mode: 'full' } | { mode: 'range'; fromSec: number; toSec: number },
  ) {
    const srcSeg = selected ?? bboxSeg
    if (!srcSeg || sourceWidth <= 0 || sourceHeight <= 0) return
    const lane = captionLaneOf(srcSeg, sourceHeight, sourceWidth)
    const byTime = range?.mode === 'range'
    const t0 = byTime ? Math.min(range!.fromSec, range!.toSec) : null
    const t1 = byTime ? Math.max(range!.fromSec, range!.toSec) : null
    // Y nguồn = bbox clip đang chọn (raw) — không resolve layout
    const srcYRaw =
      (srcSeg.bbox ? srcSeg.bbox.y : null)
      ?? (selectedBox ? selectedBox.y : null)
      ?? (selectedBoxSource ? selectedBoxSource.y : null)
    if (srcYRaw == null || !Number.isFinite(srcYRaw)) return
    const srcY = Math.round(srcYRaw)
    pushHistory()
    let changed = 0
    const next = segments.map((seg) => {
      if (byTime) {
        const s0 = floatSegStart(seg)
        const s1 = floatSegEnd(seg)
        if (s1 <= t0! || s0 >= t1!) return seg
      } else {
        if (!(seg.translation || '').trim()) return seg
        if (captionLaneOf(seg, sourceHeight, sourceWidth) !== lane) return seg
      }
      if (!seg.bbox || seg.bbox.w <= 0 || seg.bbox.h <= 0) return seg
      const own = {
        x: Math.round(seg.bbox.x),
        y: Math.round(seg.bbox.y),
        w: Math.round(seg.bbox.w),
        h: Math.round(seg.bbox.h),
      }
      const maxY = Math.max(0, sourceHeight - own.h)
      const newY = Math.max(0, Math.min(maxY, srcY))
      const dy = newY - own.y
      // Freeze chữ trước khi dời Y — không để render path fit lại
      const prevCl = seg.captionLayout
      const lines =
        Array.isArray(prevCl?.lines) && prevCl!.lines!.length
          ? prevCl!.lines!.map(String)
          : (seg.translation || '').trim()
            ? [(seg.translation || '').trim()]
            : ['']
      const fontSize =
        (prevCl && prevCl.fontSize > 0 ? prevCl.fontSize : 0)
        || (seg.fontSize && seg.fontSize > 0 ? seg.fontSize : 0)
        || settings.subtitleFontSize
        || 28
      const capX = Math.round(prevCl && prevCl.w > 0 ? prevCl.x : own.x)
      const capY0 = Math.round(prevCl && prevCl.h > 0 ? prevCl.y : own.y)
      const capW = Math.round(prevCl && prevCl.w > 0 ? prevCl.w : own.w)
      const capH = Math.round(prevCl && prevCl.h > 0 ? prevCl.h : own.h)
      if (dy === 0 && seg.bboxInherited === false && prevCl?.fontSize === fontSize) return seg
      changed += 1
      return {
        ...seg,
        bbox: { x: own.x, y: newY, w: own.w, h: own.h },
        bboxInherited: false,
        captionLayout: {
          x: capX,
          y: capY0 + dy,
          w: capW,
          h: capH,
          lines,
          fontSize,
        },
      }
    })
    if (changed === 0) return
    layoutCacheRef.current = {}
    void onSegmentsReplace(next)
  }

  /** Reset bbox: 'one' = clip đang chọn; 'all' = mọi clip có bbox. */
  function resetOcrRegion(scope: 'one' | 'all') {
    const clearBbox = (seg: Segment): Segment => ({
      ...seg,
      bbox: null,
      captionLayout: null,
      bboxInherited: undefined,
    })
    if (scope === 'one') {
      const src = selected ?? bboxSeg
      if (!src) return
      pushHistory()
      layoutCacheRef.current = {}
      // replace list (không chỉ editSegment) — đảm bảo persist null bbox
      void onSegmentsReplace(segments.map((s) => (s.id === src.id ? clearBbox(s) : s)))
      return
    }
    pushHistory()
    layoutCacheRef.current = {}
    const next = segments.map((seg) =>
      seg.bbox || seg.captionLayout ? clearBbox(seg) : seg,
    )
    void onSegmentsReplace(next)
  }

  function floatSegStart(seg: Segment) {
    return Math.max(0, Number(seg.start) || 0)
  }
  function floatSegEnd(seg: Segment) {
    const s = floatSegStart(seg)
    return Math.max(s + 0.05, Number(seg.end) || s)
  }

  const applyAllLaneLabel = (() => {
    const src = selected ?? bboxSeg
    if (!src) return 'lane'
    const key = captionLaneOf(src, sourceHeight, sourceWidth)
    return CAPTION_LANE_DEFS.find((l) => l.key === key)?.label ?? key
  })()

  const [isExportModalOpen, setIsExportModalOpen] = useState(false)

  async function handleConfirmExport(options: ExportModalOptions) {
    if (busy) return
    const updatedSettings: ProjectSettings = {
      ...settings,
      exportResolution: options.exportResolution as ProjectSettings['exportResolution'],
      exportVideo: options.exportVideo,
      exportVideoFormat: options.exportVideoFormat,
      exportAudio: options.exportAudio,
      exportAudioFormat: options.exportAudioFormat,
      exportSrt: options.exportSrt,
      exportSrtFormat: options.exportSrtFormat,
      exportGif: options.exportGif,
      exportGifRes: options.exportGifRes,
      exportOutputDir: options.exportOutputDir,
    }
    onSettings(updatedSettings)
    await Promise.all([
      ...new Set([
        updatedSettings.subtitleFontFamily || 'system',
        ...segments.map((seg) => seg.fontFamily || updatedSettings.subtitleFontFamily || 'system'),
        ...overlays.map((overlay) => overlay.fontFamily || 'system'),
      ]),
    ].map((family) => loadCaptionFont(family)))
    // Chốt bbox/line chỉ sau khi font bundle thật đã sẵn sàng.
    layoutCacheRef.current = {}
    const payload = buildExportSegments(segments, updatedSettings, sourceWidth, sourceHeight)
    // Sau Áp dụng tốc độ: work file = đồng hồ display — dùng start/end timeline (không sourceStart 1×).
    // Chưa bake: cắt trên file 1× qua sourceStart + span.
    const timelineFinal =
      Boolean(hasBakedSpeed)
      || Boolean(bakedPreferVideo)
      || Math.abs(appliedFileSpeed(bakedSpeed, bakedPreferVideo, hasBakedSpeed) - 1) > 0.02
    let exportStartSec = 0
    let exportEndSec = 0
    if (videoClips.length >= 1) {
      if (timelineFinal) {
        exportStartSec = Math.max(0, Math.min(...videoClips.map((c) => c.start)))
        exportEndSec = Math.max(...videoClips.map((c) => c.end), timelineDuration)
      } else {
        const c0 = videoClips[0]
        exportStartSec = videoClips.length === 1 ? (c0.sourceStart ?? 0) : 0
        exportEndSec = videoClips.length === 1
          ? exportStartSec + Math.max(0, c0.end - c0.start)
          : Math.max(0, ...videoClips.map((c) => c.end))
      }
    }
    const exportOverride: Partial<ProjectSettings> = {
      exportResolution: options.exportResolution as ProjectSettings['exportResolution'],
      exportVideo: options.exportVideo,
      exportVideoFormat: options.exportVideoFormat,
      exportAudio: options.exportAudio,
      exportAudioFormat: options.exportAudioFormat,
      exportSrt: options.exportSrt,
      exportSrtFormat: options.exportSrtFormat,
      exportGif: options.exportGif,
      exportGifRes: options.exportGifRes,
      exportOutputDir: options.exportOutputDir,
    }
    setIsExportModalOpen(false)
    await Promise.resolve(onExport(payload, exportEndSec, exportStartSec, options.renderName, exportOverride, options.coverDataUrl))
  }

  return (
    <div className="live-preview-editor-root bg-background text-foreground flex h-full min-h-0 w-full flex-col overflow-hidden">

      {/* ── Header — OpenCut EditorHeader: h-[3.4rem] px-3 pt-0.5 ── */}
      <header className="bg-background flex h-12 shrink-0 items-center justify-between px-3">
        <div className="flex items-center gap-1 min-w-0">
          <button
            type="button"
            className="flex items-center justify-center rounded-sm size-8 p-1 hover:bg-accent hover:text-accent-foreground transition-colors shrink-0"
            onClick={onBack}
            title="Thoát editor"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M19 12H5M12 5l-7 7 7 7" />
            </svg>
          </button>
          <span className="text-[0.9rem] h-8 px-2 py-1 rounded-sm truncate max-w-[240px] hover:bg-accent hover:text-accent-foreground cursor-default">
            Video Clone Studio
          </span>
        </div>
        <nav className="flex items-center gap-2 shrink-0">
          <span
            className="text-xs text-muted-foreground max-w-[300px] truncate"
            title={[
              speedStatus.inputLine,
              speedStatus.appliedLine,
              speedStatus.exportLine,
            ].join(' · ')}
          >
            {speedStatus.matchLabel}
          </span>
          <span className="text-xs text-muted-foreground">{busy ? 'Đang xử lý…' : 'Đã lưu'}</span>
          {/* Layout preset picker */}
          <div className="relative">
            <button
              type="button"
              title="Bố cục"
              className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground transition-colors cursor-pointer"
              onClick={() => setShowLayoutMenu(v => !v)}
            >
              {/* layout icon: 2 panels side by side + bottom bar */}
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <rect x="1" y="1" width="14" height="14" rx="2"/>
                <line x1="9" y1="1" x2="9" y2="10"/>
                <line x1="1" y1="10" x2="15" y2="10"/>
              </svg>
            </button>

            {showLayoutMenu && (
              <>
                {/* backdrop */}
                <div className="fixed inset-0 z-[9998]" onClick={() => setShowLayoutMenu(false)} />
                <div
                  className="absolute right-0 top-9 z-[9999] min-w-[200px] rounded-md border border-border bg-background shadow-xl py-1 text-xs"
                  style={{ color: 'var(--foreground)' }}
                >
                  {([ ['default', 'Mặc định'], ['vertical', 'Dọc'] ] as const).map(([preset, label]) => (
                    <button
                      key={preset}
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent transition-colors text-left"
                      onClick={() => {
                        setLayoutPreset(preset)
                        try { localStorage.setItem('videoclone.layout-preset', preset) } catch { /* ignore */ }
                        setShowLayoutMenu(false)
                      }}
                    >
                      <span className="w-4 flex items-center justify-center">
                        {layoutPreset === preset && (
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                        )}
                      </span>
                      {label}
                    </button>
                  ))}
                  <div className="my-1 border-t border-border" />
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent transition-colors text-left text-muted-foreground"
                    onClick={() => {
                      setLayoutPreset('vertical')
                      try {
                        localStorage.removeItem('videoclone.layout-preset')
                        localStorage.removeItem('videoclone.editor.outer')
                        localStorage.removeItem('videoclone.editor.main')
                        localStorage.removeItem('videoclone.editor.sides')
                      } catch { /* ignore */ }
                      setShowLayoutMenu(false)
                    }}
                  >
                    <span className="w-4" />
                    Đặt lại bố cục tùy chỉnh
                  </button>
                </div>
              </>
            )}
          </div>

          <button
            type="button"
            className="h-8 px-4 rounded-md bg-[#00c4cc] hover:bg-[#00b3ba] text-black font-semibold text-xs transition-colors disabled:opacity-50 cursor-pointer shadow-sm"
            onClick={() => setIsExportModalOpen(true)}
            disabled={busy}
          >
            Xuất bản
          </button>
        </nav>
      </header>

      {/* ── Editor layout — vertical gap-[0.18rem], panels rounded-sm ── */}
      <div className="min-h-0 min-w-0 flex-1">
        <ResizablePanelGroup
          id="videoclone.editor.outer"
          direction="horizontal"
          className="size-full"
          defaultLayout={outerLayout.defaultLayout}
          onLayoutChanged={outerLayout.onLayoutChanged}
        >
          <ResizablePanel id="left-col" defaultSize={70} minSize={30} className="pl-2">
        <ResizablePanelGroup
          id="videoclone.editor.main"
          direction="vertical"
          className="size-full"
          defaultLayout={mainLayout.defaultLayout}
          onLayoutChanged={mainLayout.onLayoutChanged}
        >

          {/* Main content: Assets | Preview | Properties */}
          <ResizablePanel id="main" defaultSize={62} minSize={15} className="min-h-0">
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handlePanelDragEnd}>
              <SortableContext items={layoutPreset === 'vertical' ? panelOrder.filter(p => p !== 'preview') : panelOrder} strategy={horizontalListSortingStrategy}>
              <ResizablePanelGroup
                key={`${panelOrder.join('|')}-${layoutPreset}`}
                id={layoutPreset === 'vertical' ? 'videoclone.editor.sides-v' : 'videoclone.editor.sides'}
                direction="horizontal"
                className="size-full"
                {...(layoutPreset !== 'vertical'
                  ? { defaultLayout: sideLayout.defaultLayout, onLayoutChanged: sideLayout.onLayoutChanged }
                  : {})}
              >
                {panelOrder.map((panelId, pIdx) => (
                  <React.Fragment key={panelId}>
                    {/* Handle: thêm giữa panel thực. Trong 'vertical' mode, bỏ qua preview (nó ở right-col) */}
                    {pIdx > 0 &&
                      (layoutPreset !== 'vertical' || panelId !== 'preview') &&
                      panelOrder.slice(0, pIdx).some(p => layoutPreset !== 'vertical' || p !== 'preview') &&
                      <ResizableHandle />}

                    {panelId === 'tools' && (
                    <SortablePanel
                      {...PANEL_SIZES.tools}
                      defaultSize={layoutPreset === 'vertical' ? 50 : PANEL_SIZES.tools.defaultSize}
                      maxSize={layoutPreset === 'vertical' ? 85 : PANEL_SIZES.tools.maxSize}
                      id="tools"
                    >
                      <div className="panel bg-background flex h-full rounded-sm border border-border overflow-hidden">

                  {/* Icon tab rail */}
                  <div className="scrollbar-hidden flex p-1 flex-col items-center justify-start gap-0.5 overflow-y-auto shrink-0">
                    {ASSET_TABS.map((tab) => (
                      <button
                        key={tab.key}
                        type="button"
                        aria-label={tab.label}
                        title={tab.label}
                        className={cn(
                          'flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors',
                          assetsTab === tab.key
                            ? 'bg-accent text-accent-foreground'
                            : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                        )}
                        onClick={() => {
                          setAssetsTab(tab.key)
                          if (tab.key === 'logo' && appliedLogo && !logoDraft) {
                            editLogo(appliedLogo.logoSource ?? 'text')
                          }
                        }}
                      >
                        {tab.icon}
                      </button>
                    ))}
                  </div>

                  <div className="w-px bg-border shrink-0" />

                  {/* Active view */}
                  <div className="flex-1 overflow-hidden">
                    {assetsTab === 'media' && (
                      <PanelView title="Media">
                        <p className="px-2 py-6 text-center text-[12px] text-muted-foreground">
                          Media sắp ra mắt...
                        </p>
                      </PanelView>
                    )}

                    {assetsTab === 'text' && (
                      <PanelView title="Text">
                        {/* Add-text card, like OpenCut TextView's "Default text" */}
                        <button
                          type="button"
                          className="w-full h-16 rounded-md bg-accent hover:bg-muted transition-colors flex items-center justify-center text-xl font-semibold text-foreground mb-2"
                          onClick={() => addTextOverlay()}
                        >
                          Default text
                        </button>
                        <div className="flex flex-col gap-0.5">
                          {overlays.map((overlay) => (
                            <div
                              key={overlay.id}
                              className={cn(
                                'flex items-center gap-1 rounded-sm px-2 py-1.5 text-[11px] cursor-pointer transition-colors',
                                overlay.id === selectedOverlayId
                                  ? 'bg-secondary text-secondary-foreground'
                                  : 'hover:bg-accent text-muted-foreground hover:text-accent-foreground',
                              )}
                              onClick={() => { setSelectedOverlayId(overlay.id); setPropTab('overlay') }}
                            >
                              <span className="flex-1 truncate">{overlay.text}</span>
                              <span className="tabular-nums opacity-60 shrink-0">{formatTime(overlay.start)}</span>
                              <button
                                type="button"
                                className="shrink-0 p-0.5 rounded hover:text-destructive"
                                title="Xóa"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  onOverlayDelete(overlay.id)
                                  if (selectedOverlayId === overlay.id) setSelectedOverlayId(null)
                                }}
                              >
                                <TabSvg><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /></TabSvg>
                              </button>
                            </div>
                          ))}
                          {overlays.length === 0 && (
                            <p className="text-muted-foreground text-[11px] px-2 py-1">Chưa có text overlay nào.</p>
                          )}
                        </div>
                      </PanelView>
                    )}

                    {assetsTab === 'logo' && (
                      <PanelView title="Logo / Watermark" showScrollbar>
                        <p className="pb-2 text-[11px] text-muted-foreground">Chọn nguồn, xem trước rồi bấm Áp dụng logo.</p>
                        <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Nguồn logo</p>
                        <div className="grid grid-cols-3 gap-1.5">
                          {(['text', 'image', 'icon'] as const).map((source) => (
                            <button key={source} type="button" className={cn('rounded-md border p-2 text-xs transition-all hover:-translate-y-px hover:border-primary/70 hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', logoUiState?.logoSource === source ? 'border-primary bg-primary/15 text-primary ring-1 ring-primary' : 'border-border bg-accent/40')} onClick={() => source === 'text' ? editLogo('text') : source === 'image' ? logoFileRef.current?.click() : void selectLogoIcon('star')}>
                              <span className="mb-1 block text-base leading-none" aria-hidden>{source === 'text' ? 'T' : source === 'image' ? '▧' : '★'}</span>{logoUiState?.logoSource === source ? '✓ ' : ''}{source === 'text' ? 'Chữ' : source === 'image' ? 'PNG/Ảnh' : 'Icon'}
                            </button>
                          ))}
                        </div>
                        <input ref={logoFileRef} type="file" accept="image/png,image/webp,image/jpeg" className="hidden" onChange={(e) => { const file = e.target.files?.[0]; if (file) void stageLogoFile(file); e.currentTarget.value = '' }} />
                        <div className="mt-2 grid grid-cols-3 gap-1.5">
                          {(['play', 'camera', 'star'] as const).map((id) => <button key={id} type="button" className={cn('rounded-md border px-2 py-1.5 text-xs capitalize transition-colors hover:border-primary/70 hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', logoUiState?.iconId === id ? 'border-primary bg-primary/15 text-primary ring-1 ring-primary' : 'border-border')} onClick={() => void selectLogoIcon(id)}><span className="mr-1" aria-hidden>{id === 'play' ? '▶' : id === 'camera' ? '▣' : '★'}</span>{logoUiState?.iconId === id ? '✓ ' : ''}{id}</button>)}
                        </div>
                        {appliedLogo && <div className="mt-2 flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5 text-[10px] text-emerald-700"><span className="size-1.5 rounded-full bg-emerald-500" />Đang áp dụng trên video</div>}
                        {logoError && <p className="mt-2 text-[10px] text-destructive">{logoError}</p>}
                        {logoDraft && <button type="button" disabled={logoToggleDisabled} className={cn('mt-3 w-full rounded-md px-3 py-2 text-xs font-medium transition-colors disabled:opacity-50', logoToggleRemoves ? 'border border-destructive/50 text-destructive hover:bg-destructive/10' : 'bg-primary text-primary-foreground hover:bg-primary/90')} onClick={() => logoToggleRemoves ? unapplyLogo() : void applyLogoDraft()}>{logoApplying ? 'Đang áp dụng…' : logoToggleRemoves ? 'Hủy áp dụng logo' : 'Áp dụng logo'}</button>}
                        {!logoDraft && overlays.find((o) => o.kind === 'logo') && <button type="button" className="mt-3 w-full rounded-md border border-destructive/50 px-3 py-2 text-xs text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive" onClick={unapplyLogo}>Hủy áp dụng logo</button>}
                      </PanelView>
                    )}

                    {assetsTab === 'captions' && (
                      <PanelView title="Captions" showScrollbar>
                        <div className="flex flex-col gap-0.5">
                          {segments.map((segment) => (
                            <button
                              key={segment.id}
                              type="button"
                              className={cn(
                                'w-full text-left rounded-sm px-2 py-1.5 text-[11px] transition-colors',
                                segment.id === selected?.id
                                  ? 'bg-secondary text-secondary-foreground'
                                  : 'hover:bg-accent text-muted-foreground hover:text-accent-foreground',
                              )}
                              onClick={() => seek(segment)}
                            >
                              <span className="tabular-nums opacity-60 mr-1.5">{formatTime(segment.start)}</span>
                              {segment.translation || '(chưa dịch)'}
                            </button>
                          ))}
                        </div>
                      </PanelView>
                    )}

                    {assetsTab === 'effects' && (
                      <PanelView title="Effects">
                        <p className="px-1 pb-2 text-[11px] text-muted-foreground leading-snug">
                          Kéo preset vào preview hoặc bấm để thêm vùng làm mờ — chỉnh khung tự do, mặt nạ blur/màu/khối.
                        </p>
                        <div className="flex flex-col gap-1.5 px-0.5">
                          {EFFECT_PRESETS.map((preset) => (
                            <button
                              key={preset.id}
                              type="button"
                              draggable
                              title={`${preset.desc} — kéo vào video hoặc bấm thêm`}
                              className="flex items-center gap-2 rounded-md border border-border bg-accent/50 hover:bg-accent px-2 py-2 text-left transition-colors cursor-grab active:cursor-grabbing"
                              onDragStart={(e) => {
                                e.dataTransfer.setData('application/x-videoclone-effect', preset.id)
                                e.dataTransfer.effectAllowed = 'copy'
                              }}
                              onClick={() => addEffectOverlay(preset)}
                            >
                              <span
                                className="size-10 shrink-0 rounded-md border border-border overflow-hidden"
                                style={coverMaskPreviewStyle(preset.maskStyle, preset.maskColor, preset.maskOpacity)}
                                aria-hidden
                              />
                              <span className="min-w-0 flex-1">
                                <span className="block text-[12px] font-medium text-foreground">{preset.label}</span>
                                <span className="block text-[10px] text-muted-foreground truncate">{preset.desc}</span>
                              </span>
                            </button>
                          ))}
                        </div>
                        {overlays.filter((o) => o.kind === 'effect').length > 0 && (
                          <div className="mt-3 border-t border-border pt-2 flex flex-col gap-0.5">
                            <p className="px-1 text-[10px] text-muted-foreground uppercase tracking-wide">Trên timeline</p>
                            {overlays.filter((o) => o.kind === 'effect').map((ov) => (
                              <div
                                key={ov.id}
                                className={cn(
                                  'flex items-center gap-1 rounded-sm px-2 py-1.5 text-[11px] cursor-pointer',
                                  ov.id === selectedOverlayId
                                    ? 'bg-secondary text-secondary-foreground'
                                    : 'hover:bg-accent text-muted-foreground',
                                )}
                                onClick={() => {
                                  setSelectedOverlayId(ov.id)
                                  setTrackFocus('text')
                                  setPropTab('overlay')
                                  if (videoRef.current) {
                                    videoRef.current.currentTime = timelineToVideoTime(ov.start)
                                    setTime(ov.start)
                                  }
                                }}
                              >
                                <span className="flex-1 truncate">{ov.text || 'Hiệu ứng'}</span>
                                <span className="tabular-nums opacity-60">{formatTime(ov.start)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </PanelView>
                    )}

                    {!['media', 'text', 'logo', 'captions', 'effects'].includes(assetsTab) && (
                      <div className="text-muted-foreground p-4 text-sm">
                        {ASSET_TABS.find((t) => t.key === assetsTab)?.label} sắp ra mắt...
                      </div>
                    )}
                  </div>
                </div>
                    </SortablePanel>
                    )}

                    {/* 'default' mode: preview slot inline — portal target nằm trong sides group */}
                    {panelId === 'preview' && layoutPreset !== 'vertical' && (
                      <SortablePanel {...PANEL_SIZES.preview} id="preview">
                        <div
                          ref={(el) => { if (el) setInlinePortalEl(el) }}
                          className="relative h-full w-full"
                        />
                      </SortablePanel>
                    )}

                    {panelId === 'preview' && previewPortalEl && createPortal(
                      <div ref={previewRef} className="panel preview-panel bg-background relative flex size-full min-h-0 min-w-0 flex-col rounded-sm border border-border overflow-hidden">

                  {/* Viewport — Fit = vừa panel; % = scale + scroll */}
                  <div
                    className={cn(
                      'flex-1 min-h-0 w-full flex items-center justify-center px-3 pt-2',
                      previewZoom === 'fit' ? 'overflow-hidden' : 'overflow-auto',
                    )}
                  >
                    <div
                      className={cn(
                        previewZoom === 'fit' && 'flex h-full w-full min-h-0 min-w-0 items-center justify-center',
                      )}
                      style={
                        previewZoom === 'fit'
                          ? undefined
                          : {
                              transform: `scale(${previewZoom})`,
                              transformOrigin: 'center center',
                              flexShrink: 0,
                            }
                      }
                    >
                    <div
                      ref={canvasRef}
                      className={cn(
                        // Không gắn container-type lên root — containment giết backdrop-filter → mask «Làm mờ» mất tác dụng
                        'relative shadow-lg',
                        previewZoom === 'fit' && 'max-h-full max-w-full',
                        previewZoom === 'fit' && (cropPortrait ? 'h-full w-auto' : 'w-full h-auto'),
                        tool === 'text' ? 'cursor-crosshair' : tool === 'cover' ? 'cursor-cell' : 'cursor-default',
                      )}
                      style={{
                        aspectRatio: `${crop.w} / ${crop.h}`,
                        cursor:
                          !cropEditing
                          && tool === 'select'
                          && aspectId !== 'original'
                          && aspectId !== 'custom'
                            ? 'grab'
                            : undefined,
                        ...(previewZoom !== 'fit'
                          ? (() => {
                              const base = 480
                              if (cropPortrait) {
                                const h = Math.min(base, crop.h)
                                return { height: h, width: Math.round(h * (crop.w / crop.h)) }
                              }
                              const w = Math.min(base, crop.w)
                              return { width: w, height: Math.round(w * (crop.h / crop.w)) }
                            })()
                          : {}),
                      }}
                      onPointerDown={(event) => {
                        if (tool === 'text') {
                          addTextOverlay(event.clientX, event.clientY)
                          return
                        }
                        // Kéo pan khung cắt khi đã chọn tỷ lệ (9:16 / 16:9…)
                        if (
                          tool === 'select'
                          && !cropEditing
                          && !busy
                          && aspectId !== 'original'
                          && aspectId !== 'custom'
                        ) {
                          beginAspectPan(event)
                        }
                      }}
                      onDragOver={(e) => {
                        if (e.dataTransfer.types.includes('application/x-videoclone-effect')) {
                          e.preventDefault()
                          e.dataTransfer.dropEffect = 'copy'
                        }
                      }}
                      onDrop={(e) => {
                        const pid = e.dataTransfer.getData('application/x-videoclone-effect')
                        if (!pid) return
                        e.preventDefault()
                        const preset = EFFECT_PRESETS.find((p) => p.id === pid)
                        if (preset) addEffectOverlay(preset, e.clientX, e.clientY)
                      }}
                    >
                      <div className="absolute inset-0 overflow-hidden bg-black">
                        <video
                          key={videoUrl}
                          ref={videoRef}
                          className="absolute max-w-none pointer-events-none select-none"
                          style={videoCropStyle(
                            sourceWidth,
                            sourceHeight,
                            crop,
                            settings.videoScaleX ?? settings.videoScale ?? 100,
                            settings.videoScaleY ?? settings.videoScale ?? 100,
                          )}
                          src={videoUrl}
                          controls={false}
                          playsInline
                          onPlay={() => {
                            setPlaying(true)
                            syncDubAudio(videoRef.current ? videoToTimelineTime(videoRef.current.currentTime) : time, true)
                          }}
                          onPause={() => {
                            setPlaying(false)
                            pauseDubAudio()
                          }}
                          onLoadedMetadata={(event) => {
                            const { duration: mediaDuration, videoWidth, videoHeight } = event.currentTarget
                            setDuration(mediaDuration)
                            if (videoWidth > 0 && videoHeight > 0) setVideoSize({ width: videoWidth, height: videoHeight })
                            // Đổi URL (bake tốc độ) → element reset về 0 trong khi playhead
                            // timeline vẫn ở t — play sẽ chạy sai chỗ. Khôi phục đúng t.
                            const want = timelineToVideoTime(time)
                            if (
                              Number.isFinite(want)
                              && want > 0.01
                              && Math.abs(event.currentTarget.currentTime - want) > 0.25
                            ) {
                              event.currentTarget.currentTime = Math.max(
                                videoSourceStart,
                                Math.min(want, Math.max(videoSourceStart, mediaDuration - 0.05)),
                              )
                            }
                            if (event.currentTarget.currentTime < videoSourceStart) {
                              event.currentTarget.currentTime = videoSourceStart
                            }
                            // Preview clip: đứng ở đầu cửa sổ làm việc
                            if (workClipSec > 0 && event.currentTarget.currentTime > workClipSec) {
                              event.currentTarget.currentTime = videoSourceStart
                            }
                          }}
                          onTimeUpdate={(event) => {
                            let current = videoToTimelineTime(event.currentTarget.currentTime)
                            // Không cho chạy quá cửa sổ preview (xuất cũng chỉ đoạn này)
                            if (current >= timelineDuration - 0.04) {
                              current = timelineDuration
                              event.currentTarget.pause()
                              event.currentTarget.currentTime = timelineToVideoTime(timelineDuration)
                              setPlaying(false)
                              pauseDubAudio()
                            }
                            setTime(current)
                            if (!event.currentTarget.paused) followPlaybackPlayhead(current)
                            // Focus Video/BG: xem clip — không nhảy chọn Mid/Dọc (tránh hiện khung kéo)
                            if (trackFocus === 'caption' || trackFocus === 'dub') {
                              const now = pickTimelineSeg(segments, current, selectedId)
                              const cov = segmentAtCover(segments, current)
                              if (now) {
                                setSelectedId(now.id)
                              } else if (cov) {
                                const prev = selectedId ? segments.find((s) => s.id === selectedId) : null
                                const prevLane = prev ? captionLaneOf(prev, sourceHeight, sourceWidth) : null
                                if (captionLaneOf(cov, sourceHeight, sourceWidth) === 'vertical' && prevLane && prevLane !== 'vertical') {
                                  /* keep */
                                } else {
                                  setSelectedId(cov.id)
                                }
                              }
                            }
                            const laneSeg = speedSegmentAt(segments, current)
                            event.currentTarget.playbackRate = previewVideoRate(
                              settings.matchDuration,
                              bakedPreferVideo,
                              laneSeg?.videoSpeed,
                              bakedSpeed,
                              hasBakedSpeed,
                            )
                            syncDubAudio(current, !event.currentTarget.paused)
                          }}
                          onSeeked={(event) => {
                            const t = videoToTimelineTime(event.currentTarget.currentTime)
                            if (trackFocus === 'caption' || trackFocus === 'dub') {
                              const current = pickTimelineSeg(segments, t, selectedId)
                              const cov = segmentAtCover(segments, t)
                              if (current) {
                                setSelectedId(current.id)
                              } else if (cov) {
                                const prev = selectedId ? segments.find((s) => s.id === selectedId) : null
                                const prevLane = prev ? captionLaneOf(prev, sourceHeight, sourceWidth) : null
                                if (captionLaneOf(cov, sourceHeight, sourceWidth) === 'vertical' && prevLane && prevLane !== 'vertical') {
                                  /* keep */
                                } else {
                                  setSelectedId(cov.id)
                                }
                              }
                            }
                            // Scrub timeline → ép TTS/stem khớp lại một lần
                            dubHardSyncRef.current = true
                            syncDubAudio(t, !event.currentTarget.paused)
                          }}
                        />

                        {/* Logo cố định dùng cùng bbox/timestamp với exporter.
                            Nhờ vậy preview Editor không còn hiển thị video nguồn trần. */}
                        {activeWatermarkMasks.map((track, index) => {
                          const bbox = track.bbox!
                          const box = {
                            x: bbox.x * sourceWidth,
                            y: bbox.y * sourceHeight,
                            w: bbox.w * sourceWidth,
                            h: bbox.h * sourceHeight,
                          }
                          return (
                            <div
                              key={`watermark-mask-${track.start || 0}-${index}`}
                              className="pointer-events-none absolute z-[18]"
                              style={{
                                ...sourceToDisplayStyle(box, crop),
                                ...coverMaskPreviewStyle('blur', '#101827', 92),
                              }}
                            />
                          )
                        })}

                        {!cropEditing
                          && aspectId !== 'original'
                          && aspectId !== 'custom'
                          && tool === 'select' && (
                          <div className="pointer-events-none absolute left-1/2 top-2 z-[25] -translate-x-1/2 rounded-md bg-black/65 px-2 py-1 text-[10px] text-white/90">
                            Kéo video để chỉnh khung {aspectLabel}
                          </div>
                        )}

                        {cropEditing && (
                          <div className="absolute inset-0 z-[60] overflow-hidden">
                            <div
                              className="absolute border-2 border-cyan-400 cursor-move"
                              style={{
                                left: `${cropDraft.x * 100}%`,
                                top: `${cropDraft.y * 100}%`,
                                width: `${cropDraft.w * 100}%`,
                                height: `${cropDraft.h * 100}%`,
                                boxShadow: '0 0 0 9999px rgba(0,0,0,.55)',
                              }}
                              onPointerDown={(e) => beginCropDrag(e, 'move')}
                            >
                              {(['nw', 'ne', 'se', 'sw'] as const).map((handle) => (
                                <span
                                  key={handle}
                                  className={cn(
                                    'absolute size-4 rounded-sm border-2 border-cyan-500 bg-white',
                                    handle === 'nw' && '-left-2 -top-2 cursor-nwse-resize',
                                    handle === 'ne' && '-right-2 -top-2 cursor-nesw-resize',
                                    handle === 'se' && '-right-2 -bottom-2 cursor-nwse-resize',
                                    handle === 'sw' && '-left-2 -bottom-2 cursor-nesw-resize',
                                  )}
                                  onPointerDown={(e) => beginCropDrag(e, handle)}
                                />
                              ))}
                            </div>
                            <div className="absolute right-3 top-3 flex gap-2">
                              <button
                                type="button"
                                className="rounded-md bg-black/70 px-3 py-1.5 text-xs text-white"
                                onClick={() => setCropEditing(false)}
                              >
                                Hủy
                              </button>
                              <button
                                type="button"
                                className="rounded-md bg-cyan-500 px-3 py-1.5 text-xs font-medium text-black"
                                onClick={() => {
                                  editSettings({ ...settings, previewAspectRatio: 'custom', previewCrop: cropDraft })
                                  setCropEditing(false)
                                }}
                              >
                                Áp dụng
                              </button>
                            </div>
                          </div>
                        )}

                      {/* Snap guides — bbox / pan aspect / crop tự do (CapCut-style) */}
                      {(draggingBox || panningCrop) && (
                        <div className="absolute inset-0 z-[80] pointer-events-none overflow-visible" aria-hidden>
                          {/* Tia dọc — luôn mờ khi pan; sáng khi snap giữa ngang */}
                          <div
                            className={cn(
                              'absolute inset-y-0 left-1/2 w-0 -translate-x-1/2 border-l-2 border-dashed transition-opacity',
                              snapGuides.v
                                ? 'border-fuchsia-400 opacity-100 shadow-[0_0_10px_rgba(232,121,249,0.85)]'
                                : panningCrop
                                  ? 'border-fuchsia-300/50 opacity-70'
                                  : 'opacity-0',
                            )}
                          />
                          {/* Tia ngang — luôn mờ khi pan; sáng khi snap giữa dọc */}
                          <div
                            className={cn(
                              'absolute inset-x-0 top-1/2 h-0 -translate-y-1/2 border-t-2 border-dashed transition-opacity',
                              snapGuides.h
                                ? 'border-fuchsia-400 opacity-100 shadow-[0_0_10px_rgba(232,121,249,0.85)]'
                                : panningCrop
                                  ? 'border-fuchsia-300/50 opacity-70'
                                  : 'opacity-0',
                            )}
                          />
                        </div>
                      )}

                      {/* Blur che chữ — kính CapCut (z dưới chữ; overflow hidden + isolation) */}
                      {showCoverBlur && tool !== 'text' && maskBoxes.map((box, i) => (
                        <div
                          key={`mask-${i}-${box.x}-${box.y}`}
                          className="absolute z-[9] pointer-events-none overflow-hidden rounded-[1px]"
                          style={{
                            ...sourceToDisplayStyle(box, crop),
                            ...coverMaskPreviewStyle(coverMaskStyle, coverMaskColor, coverMaskOpacity),
                          }}
                          aria-hidden
                        />
                      ))}

                      {/* Bbox kéo — suốt thanh vàng Mid [start,end); không ẩn sớm vì selected cũ */}
                      {bboxSeg && selectedBox && showBboxAtPlayhead && tool !== 'text' && (
                        <div
                          className={cn(
                            'group/bbox absolute border-2 border-violet-400 cursor-move z-10 overflow-visible',
                            !showCoverBlur && 'bg-violet-900/10 border-dashed',
                            draggingBox && 'opacity-80 ring-2 ring-violet-300',
                            (tool === 'cover' || effectivePropTab === 'mask') && 'border-yellow-400 ring-1 ring-yellow-400/50',
                          )}
                          style={{
                            ...sourceToDisplayStyle(selectedBox, crop),
                            ...(!maskBoxes.length && showCoverBlur
                              ? coverMaskPreviewStyle(coverMaskStyle, coverMaskColor, coverMaskOpacity)
                              : {}),
                          }}
                          onPointerDown={(e) => beginBboxDrag(e, 'move', bboxSeg)}
                        >
                          {(['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const).map((handle) => (
                            <span
                              key={handle}
                              className={cn(
                                'absolute w-3.5 h-3.5 rounded-sm bg-white border-2 border-violet-500 shadow-sm z-20 touch-none opacity-0 hover:opacity-100 transition-opacity',
                                handle === 'nw' && 'top-[-6px] left-[-6px] cursor-nwse-resize',
                                handle === 'n'  && 'top-[-6px] left-[calc(50%-6px)] cursor-ns-resize',
                                handle === 'ne' && 'top-[-6px] right-[-6px] cursor-nesw-resize',
                                handle === 'e'  && 'top-[calc(50%-6px)] right-[-6px] cursor-ew-resize',
                                handle === 'se' && 'bottom-[-6px] right-[-6px] cursor-nwse-resize',
                                handle === 's'  && 'bottom-[-6px] left-[calc(50%-6px)] cursor-ns-resize',
                                handle === 'sw' && 'bottom-[-6px] left-[-6px] cursor-nesw-resize',
                                handle === 'w'  && 'top-[calc(50%-6px)] left-[-6px] cursor-ew-resize',
                              )}
                              onPointerDown={(e) => { e.stopPropagation(); beginBboxDrag(e, handle, bboxSeg) }}
                            />
                          ))}

                        </div>
                      )}

                      {/* Phụ đề dịch — mọi clip overlapping (mid + dọc + đáy cùng lúc) */}
                      {captionLayers.map(({ seg: layerSeg, layout: layerLayout }) => {
                        const overlayLay = effectiveOverlayLayout(layerSeg, sourceHeight, sourceWidth)
                        const fontPx = layerLayout.fontPx
                          ?? (overlayLay
                            ? fitOverlayFontPx(
                                overlayLay,
                                layerLayout.cover,
                                layerSeg.translation,
                                resolveOverlayFontPreferred(layerSeg),
                              )
                            : activeCaptionPx)
                        const lines = layerLayout.lines.length
                          ? layerLayout.lines
                          : [layerSeg.translation]
                        return (
                        <div
                          key={layerSeg.id}
                          className={cn(
                            '@container [container-type:size] absolute z-20 pointer-events-none flex items-center justify-center',
                            // Không clip chữ VI (tránh cắt đuôi); mask che hardsub dùng cover box riêng.
                            'overflow-visible',
                          )}
                          style={sourceToDisplayStyle(
                            // Horizontal cover mode: bám cover (che + chữ giữa khung tím)
                            overlayLay || settings.coverHardsubs
                              ? layerLayout.cover
                              : layerLayout.caption,
                            crop,
                          )}
                        >
                          {overlayLay === 'vertical' ? (
                            <div
                              className="text-white font-bold"
                              style={{
                                display: 'flex',
                                flexDirection: 'column',
                                alignItems: 'center',
                                justifyContent: 'center',
                                gap: '0.08em',
                                width: '100%',
                                height: '100%',
                                overflow: 'visible',
                                margin: 0,
                                padding: '0.04em 0.04em',
                                boxSizing: 'border-box',
                                ...overlayDisplayFontStyle('vertical', layerLayout.cover, fontPx, lines.length),
                                 ...captionChromeStyle(settings, layerSeg),
                                 transform: 'translateY(-0.06em)',
                              }}
                            >
                              {lines.map((unit, i) => (
                                <span
                                  key={i}
                                  style={{
                                    fontSize: 'inherit',
                                    lineHeight: 1,
                                    whiteSpace: 'nowrap',
                                    maxWidth: '100%',
                                    overflow: 'visible',
                                    writingMode: 'horizontal-tb',
                                  }}
                                >
                                  {unit}
                                </span>
                              ))}
                            </div>
                          ) : overlayLay === 'label' || overlayLay === 'mid' ? (
                            <p
                              className={cn(
                                'max-w-full h-fit text-center text-white font-bold flex flex-col items-center justify-center',
                                'overflow-visible',
                              )}
                              style={{
                                ...overlayDisplayFontStyle(
                                  overlayLay,
                                  layerLayout.cover,
                                  fontPx,
                                  lines.length,
                                ),
                                ...captionChromeStyle(settings, layerSeg),
                                // CAP-MID/label: bỏ bóng chữ (text-shadow) — chỉ chữ trắng trên bbox
                                textShadow: 'none',
                                WebkitTextStroke: '0',
                                // CAP-MID: padding comes from the same em value
                                // used by its source-coordinate bbox calculation.
                                whiteSpace: 'normal',
                                boxSizing: 'border-box',
                                margin: 0,
                              }}
                            >
                              {lines.map((line, i) => (
                                <span
                                  key={i}
                                  className="block max-w-full text-center overflow-visible"
                                  style={{
                                    whiteSpace: 'nowrap',
                                  }}
                                >
                                  {line}
                                </span>
                              ))}
                            </p>
                          ) : (
                            <p
                              className="max-w-full h-fit text-center text-white font-bold flex flex-col items-center justify-center overflow-visible"
                              style={{
                                // boxSource = cover (container), không dùng caption.w — caption hẹp → cqw phình chữ to hơn bbox
                                ...captionFontStyle(
                                  fontPx,
                                  Math.max(1, layerLayout.cover.h),
                                  'h',
                                ),
                                ...captionChromeStyle(settings, layerSeg),
                                lineHeight: 1.12,
                                margin: 0,
                                // Giữ 1px hai mép; bbox đã chừa phần còn lại để không cắt đuôi.
                                padding: '0.02em 6px',
                                boxSizing: 'border-box',
                                width: '100%',
                              }}
                            >
                              {lines.map((line, i) => (
                                <span
                                  key={i}
                                  className="block text-center whitespace-nowrap"
                                  style={{ maxWidth: 'none' }}
                                >
                                  {line}
                                </span>
                              ))}
                            </p>
                          )}
                        </div>
                        )
                      })}
                      {/* below/above: soft shadow như bản đẹp — không nền, không stroke dày */}
                      {activeCaptionBox && timelineSeg?.translation.trim() && (
                        <div
                          className={cn(
                            '@container [container-type:size] absolute z-[22] pointer-events-none flex items-center justify-center',
                            'overflow-visible',
                          )}
                          style={sourceToDisplayStyle(activeCaptionBox, crop)}
                        >
                          <p
                            className={cn(
                              'w-full h-full max-w-full text-center text-white font-bold leading-tight flex flex-col items-center justify-center',
                              'overflow-visible',
                            )}
                            style={{
                              ...captionFontStyle(activeCaptionPx, activeCaptionBox.h),
                              lineHeight: 1.12,
                              ...captionChromeStyle(settings, timelineSeg),
                              transform: 'none',
                              backgroundColor: (settings.captionBgStyle || 'none') === 'none'
                                ? 'transparent'
                                : undefined,
                              padding: (settings.captionBgStyle || 'none') === 'none' ? 0 : undefined,
                              margin: 0,
                              boxSizing: 'border-box',
                            }}
                          >
                            {activeCaptionMeta?.lines.map((line, i) => (
                              <span key={i} className="block max-w-full whitespace-nowrap">
                                {line}
                              </span>
                            ))}
                          </p>
                        </div>
                      )}

                      {/* Text + effect overlays */}
                      {activeOverlays.map((overlay) => {
                        const isFx = overlay.kind === 'effect'
                        const sel = overlay.id === selectedOverlayId
                        if (overlay.kind === 'logo') {
                          const isLogoDraft = logoDraft?.id === overlay.id && !logoToggleRemoves
                          const motionFrame = logoFrame(overlay, time)
                          const frame = isLogoDraft && !playing
                            ? { ...motionFrame, opacity: (overlay.opacity ?? 85) / 100 }
                            : motionFrame
                          const display = { ...overlay, x: frame.x, y: frame.y }
                          const blockedByCaption = captionLayers.some(({ layout }) =>
                            display.x < layout.cover.x + layout.cover.w
                            && display.x + display.w > layout.cover.x
                            && display.y < layout.cover.y + layout.cover.h
                            && display.y + display.h > layout.cover.y,
                          )
                          return (
                            <div key={overlay.id} className={cn('group absolute z-[25] cursor-move overflow-visible outline outline-1 outline-transparent hover:outline-cyan-300/70', isLogoDraft && 'outline-dashed !outline-amber-300', sel && !isLogoDraft && 'outline-2 !outline-cyan-300')} style={{ ...sourceToDisplayStyle(display, crop), containerType: 'size', opacity: blockedByCaption ? 0 : frame.opacity * (isLogoDraft ? .55 : 1) }} onPointerDown={(e) => { if (isLogoDraft) beginOverlayDrag(e, overlay); else { e.stopPropagation(); editLogo(overlay.logoSource ?? 'text') } }}>
                              {overlay.logoSource !== 'text' && overlay.assetUrl
                                ? <img src={overlay.assetUrl} className="size-full object-contain pointer-events-none" draggable={false} />
                                : <div className="size-full flex items-center justify-center text-center font-bold" style={{ ...captionFontStyle(overlay.fontSize, overlay.h), fontFamily: captionFontCss(overlay.fontFamily ?? 'system'), color: overlay.color, textShadow: '0 2px 4px #000' }}>{overlay.text || 'LOGO'}</div>}

                              {sel && (['nw', 'ne', 'sw', 'se'] as const).map((edge) => <span key={edge} className={cn('absolute size-4 rounded-sm bg-cyan-400 border border-white opacity-0 hover:opacity-100 transition-opacity', edge === 'nw' && '-left-2 -top-2 cursor-nwse-resize', edge === 'ne' && '-right-2 -top-2 cursor-nesw-resize', edge === 'sw' && '-left-2 -bottom-2 cursor-nesw-resize', edge === 'se' && '-right-2 -bottom-2 cursor-nwse-resize')} onPointerDown={(e) => beginOverlayResize(e, overlay, edge)} />)}
                            </div>
                          )
                        }
                        if (isFx) {
                          const style = overlay.maskStyle ?? 'blur'
                          const color = overlay.maskColor ?? '#4c1d95'
                          const opacity = overlay.maskOpacity ?? 45
                          return (
                            <div
                              key={overlay.id}
                              className={cn(
                                'absolute z-[15] cursor-move overflow-visible',
                                sel && 'ring-2 ring-fuchsia-400',
                              )}
                              style={sourceToDisplayStyle(overlay, crop)}
                              onPointerDown={(e) => {
                                e.stopPropagation()
                                beginOverlayDrag(e, overlay)
                              }}
                            >
                              <div
                                className={cn(
                                  'absolute inset-0 overflow-hidden rounded-sm border border-dashed',
                                  sel ? 'border-fuchsia-200/90' : 'border-transparent',
                                )}
                                style={coverMaskPreviewStyle(style, color, opacity)}
                              />
                              {sel && (
                                <>

                                  {(['nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w'] as const).map((edge) => {
                                    const pos: Record<string, string> = {
                                      nw: 'left-0 top-0 -translate-x-1/2 -translate-y-1/2 cursor-nwse-resize',
                                      ne: 'right-0 top-0 translate-x-1/2 -translate-y-1/2 cursor-nesw-resize',
                                      sw: 'left-0 bottom-0 -translate-x-1/2 translate-y-1/2 cursor-nesw-resize',
                                      se: 'right-0 bottom-0 translate-x-1/2 translate-y-1/2 cursor-nwse-resize',
                                      n: 'left-1/2 top-0 -translate-x-1/2 -translate-y-1/2 cursor-ns-resize',
                                      s: 'left-1/2 bottom-0 -translate-x-1/2 translate-y-1/2 cursor-ns-resize',
                                      e: 'right-0 top-1/2 translate-x-1/2 -translate-y-1/2 cursor-ew-resize',
                                      w: 'left-0 top-1/2 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize',
                                    }
                                    return (
                                      <span
                                        key={edge}
                                        className={cn('absolute z-20 size-2.5 rounded-sm bg-fuchsia-400 border border-white', pos[edge])}
                                        onPointerDown={(e) => beginOverlayResize(e, overlay, edge)}
                                      />
                                    )
                                  })}
                                </>
                              )}
                            </div>
                          )
                        }
                        return (
                          <div
                            key={overlay.id}
                            className={cn(
                              '@container [container-type:size] absolute cursor-move overflow-visible z-[15]',
                              sel && 'ring-1 ring-yellow-300',
                            )}
                            style={sourceToDisplayStyle(overlay, crop)}
                            onPointerDown={(e) => beginOverlayDrag(e, overlay)}
                          >

                            <textarea
                              className="block w-full h-full bg-transparent outline-none resize-none text-center font-bold cursor-move"
                              style={{
                                ...captionFontStyle(overlay.fontSize, overlay.h),
                                // Font bundle 700 = đúng bytes export (extrabold là synthetic, xuất không có)
                                fontFamily: captionFontCss(overlay.fontFamily ?? 'system'),
                                color: overlay.color,
                                textShadow: '0 2px 4px #000',
                                lineHeight: 1.25,
                                border: sel ? '1px dashed #ffd166' : '1px dashed transparent',
                              }}
                              value={overlay.text}
                              onPointerDown={(e) => beginOverlayDrag(e, overlay)}
                              onFocus={() => setSelectedOverlayId(overlay.id)}
                              onChange={(e) => editOverlay({ ...overlay, text: e.target.value })}
                            />
                          </div>
                        )
                      })}

                      </div>
                    </div>
                    </div>
                  </div>

                  {/* Thanh tiến độ — preview + toàn màn hình (kéo tua) */}
                  {timelineDuration > 0 && (
                    <div className="preview-seek-wrap shrink-0 px-4 pb-1 pt-2">
                      <div
                        role="slider"
                        aria-label="Tiến độ phát"
                        aria-valuemin={0}
                        aria-valuemax={timelineDuration}
                        aria-valuenow={time}
                        className="group relative h-2 cursor-pointer rounded-full bg-muted/80 touch-none"
                        onPointerDown={beginPreviewSeek}
                      >
                        <div
                          className="pointer-events-none absolute inset-y-0 left-0 rounded-full bg-primary/90"
                          style={{ width: `${Math.min(100, (time / timelineDuration) * 100)}%` }}
                        />
                        <div
                          className="pointer-events-none absolute top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-background bg-primary opacity-0 shadow transition-opacity group-hover:opacity-100"
                          style={{ left: `calc(${Math.min(100, (time / timelineDuration) * 100)}% - 6px)` }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Preview toolbar — OpenCut: grid-cols-[1fr_auto_1fr] pb-3 pt-5 px-5 */}
                  <div className="preview-toolbar grid grid-cols-[1fr_auto_1fr] items-center pb-2 pt-2 px-4 shrink-0">
                    {/* Left: timecode */}
                    <div className="flex items-center">
                      <span className="font-mono text-xs tabular-nums">{formatTimecode(time)}</span>
                      <span className="text-muted-foreground px-2 font-mono text-xs">/</span>
                      <span className="text-muted-foreground font-mono text-xs tabular-nums">{formatTimecode(timelineDuration)}</span>
                    </div>

                    {/* Center: play/pause */}
                    <button
                      type="button"
                      className="preview-play-button flex h-8 w-8 items-center justify-center rounded-md text-foreground hover:bg-accent transition-colors"
                      onClick={togglePlay}
                      aria-label={playing ? 'Tạm dừng' : 'Phát'}
                      title="Phát / dừng (Space hoặc K)"
                    >
                      {playing
                        ? <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
                        : <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M8 5.14v13.72c0 .83.9 1.34 1.61.9l10.9-6.86a1.05 1.05 0 0 0 0-1.8L9.61 4.24A1.05 1.05 0 0 0 8 5.14Z" /></svg>
                      }
                    </button>

                    {/* Right: tools + fullscreen */}
                    <div className="justify-self-end flex items-center gap-2.5">
                      <div className="flex items-center gap-0.5">
                        {(['select', 'cover', 'text'] as const).map((t) => (
                          <button
                            key={t}
                            type="button"
                            title={t === 'select' ? 'Chọn / kéo thả' : t === 'cover' ? 'Vùng che chữ' : 'Chèn text (click lên video)'}
                            className={cn(
                              'flex h-8 w-8 items-center justify-center rounded-md transition-colors',
                              tool === t
                                ? 'bg-accent text-accent-foreground'
                                : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                            )}
                            onClick={() => setTool(t)}
                          >
                            {t === 'select' && <TabSvg><path d="m3 3 7.07 16.97 2.51-7.39 7.39-2.51L3 3z" /></TabSvg>}
                            {t === 'cover' && <TabSvg><rect x="4" y="4" width="16" height="16" rx="1" strokeDasharray="3 3" /></TabSvg>}
                            {t === 'text' && <TabSvg><polyline points="4 7 4 4 20 4 20 7" /><line x1="9" y1="20" x2="15" y2="20" /><line x1="12" y1="4" x2="12" y2="20" /></TabSvg>}
                          </button>
                        ))}
                      </div>
                      <div className="w-px h-4 bg-border" />
                      <div ref={aspectMenuRef} className="relative">
                        {aspectMenuOpen && (
                          <div className="absolute bottom-full right-0 mb-2 w-[200px] max-h-[340px] overflow-y-auto rounded-lg border border-border bg-popover py-1.5 shadow-lg text-popover-foreground text-[13px] z-50">
                            {ASPECT_PRESETS.filter((p) => p.id === 'original' || p.id === 'custom').map((preset) => {
                              const disabled = 'disabled' in preset && preset.disabled
                              return (
                                <button
                                  key={preset.id}
                                  type="button"
                                  disabled={disabled}
                                  className={cn(
                                    'flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed',
                                    aspectId === preset.id && 'text-primary',
                                  )}
                                  onClick={() => {
                                    if (disabled) return
                                    applyAspectPreset(preset.id)
                                  }}
                                >
                                  <span className="w-4 shrink-0 text-primary">
                                    {aspectId === preset.id ? '✓' : ''}
                                  </span>
                                  <span className="flex-1">{preset.label}</span>
                                </button>
                              )
                            })}
                            <div className="my-1 border-t border-border" />
                            {ASPECT_PRESETS.filter((p) => 'orient' in p && p.orient === 'landscape').map((preset) => (
                              <button
                                key={preset.id}
                                type="button"
                                className={cn(
                                  'flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent',
                                  aspectId === preset.id && 'text-primary',
                                )}
                                onClick={() => applyAspectPreset(preset.id)}
                              >
                                <span className="w-4 shrink-0 text-primary">
                                  {aspectId === preset.id ? '✓' : ''}
                                </span>
                                <span className="flex-1">{preset.label}</span>
                                {'orient' in preset && <AspectIcon orient={preset.orient} />}
                              </button>
                            ))}
                            <div className="my-1 border-t border-border" />
                            {ASPECT_PRESETS.filter((p) => 'orient' in p && p.orient !== 'landscape').map((preset) => (
                              <button
                                key={preset.id}
                                type="button"
                                className={cn(
                                  'flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent',
                                  aspectId === preset.id && 'text-primary',
                                )}
                                onClick={() => applyAspectPreset(preset.id)}
                              >
                                <span className="w-4 shrink-0 text-primary">
                                  {aspectId === preset.id ? '✓' : ''}
                                </span>
                                <span className="flex-1">{preset.label}</span>
                                {'orient' in preset && <AspectIcon orient={preset.orient} />}
                              </button>
                            ))}
                          </div>
                        )}
                        <button
                          type="button"
                          className={cn(
                            'flex h-8 w-8 items-center justify-center rounded-md transition-colors',
                            aspectMenuOpen
                              ? 'bg-accent text-accent-foreground'
                              : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                          )}
                          onClick={() => setAspectMenuOpen((o) => !o)}
                          title={`Tỷ lệ khung hình · ${aspectLabel}`}
                          aria-label={`Tỷ lệ khung hình · ${aspectLabel}`}
                        >
                          <TabSvg><path d="M6 3H3v3M21 3h-3M3 18v3h3M18 21h3v-3" /><rect x="7" y="7" width="10" height="10" rx="1" /></TabSvg>
                        </button>
                      </div>
                      <div ref={fitMenuRef} className="relative flex items-center gap-0.5">
                        {fitMenuOpen && (
                          <div className="absolute bottom-full right-0 mb-2 w-[120px] rounded-lg border border-border bg-popover py-1 shadow-lg text-popover-foreground text-[13px] z-50">
                            <button
                              type="button"
                              className={cn(
                                'flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent',
                                previewZoom === 'fit' && 'text-primary',
                              )}
                              onClick={() => { setPreviewZoom('fit'); setFitMenuOpen(false) }}
                            >
                              <span className="w-4 shrink-0 text-primary">{previewZoom === 'fit' ? '✓' : ''}</span>
                              Fit
                            </button>
                            <div className="my-1 border-t border-border" />
                            {PREVIEW_ZOOM_PRESETS.map((z) => (
                              <button
                                type="button"
                                className={cn(
                                  'flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent',
                                  previewZoom === z && 'text-primary',
                                )}
                                onClick={() => { setPreviewZoom(z); setFitMenuOpen(false) }}
                              >
                                <span className="w-4 shrink-0 text-primary">{previewZoom === z ? '✓' : ''}</span>
                                {Math.round(z * 100)}%
                              </button>
                            ))}
                          </div>
                        )}
                        <button
                          type="button"
                          className={cn(
                            'flex h-8 items-center gap-1 rounded-md px-2.5 text-xs transition-colors',
                            fitMenuOpen
                              ? 'bg-accent text-accent-foreground'
                              : 'bg-muted/60 text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                          )}
                          onClick={() => { setFitMenuOpen((o) => !o); setAspectMenuOpen(false) }}
                          title="Zoom preview"
                        >
                          {fitMenuLabel}
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
                            <polyline points="6 9 12 15 18 9" />
                          </svg>
                        </button>
                        <div className="w-px h-4 bg-border mx-0.5" />
                        <button
                          type="button"
                          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/60 hover:text-foreground transition-colors"
                          onClick={toggleFullscreen}
                          title="Toàn màn hình"
                        >
                          <TabSvg><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3" /></TabSvg>
                        </button>
                      </div>
                    </div>
                  </div>
                   </div>
                    , previewPortalEl)}

                    {panelId === 'properties' && (
                    <SortablePanel
                      {...PANEL_SIZES.properties}
                      defaultSize={layoutPreset === 'vertical' ? 50 : PANEL_SIZES.properties.defaultSize}
                      maxSize={layoutPreset === 'vertical' ? 85 : PANEL_SIZES.properties.maxSize}
                      id="properties"
                    >
                      <EditorPropertiesPanel
                        effectivePropTab={effectivePropTab}
                        setPropTab={setPropTab}
                        setTool={setTool}
                        busy={busy}
                        segments={segments}
                        settings={settings}
                        onSettings={onSettings}
                        voices={voices}
                        selected={selected}
                        selectedOverlay={selectedOverlay}
                        bboxSeg={bboxSeg}
                        isOverlaySeg={isOverlaySeg}
                        dubOn={dubOn}
                        timelineDuration={timelineDuration}
                        playheadSec={time}
                        sourceWidth={sourceWidth}
                        sourceHeight={sourceHeight}
                        activeCaptionPx={activeCaptionPx}
                        fontSizeDraft={fontSizeDraft}
                        setFontSizeDraft={setFontSizeDraft}
                        fontSizeOptions={fontSizeOptions}
                        applyAllLaneLabel={applyAllLaneLabel}
                        showCoverBlur={showCoverBlur}
                        editSegment={editSegment}
                        editOverlay={editOverlay}
                        onOverlayDelete={onOverlayDelete}
                        setSelectedOverlayId={setSelectedOverlayId}
                        onSegmentsReplace={onSegmentsReplace}
                        pushHistory={pushHistory}
                        ttsBusy={ttsBusy}
                        ttsError={ttsError}
                        previewTts={previewTts}
                        playSegmentDub={playSegmentDub}
                        applyFontFamily={applyFontFamily}
                        applyFontSize={applyFontSize}
                        applyCaptionColor={applyCaptionColor}
                        applyCaptionModeAll={applyCaptionModeAll}
                        speedStatus={speedStatus}
                        speedDraft={speedDraft}
                        setSpeedDraft={setSpeedDraft}
                        speedBusy={speedBusy}
                        speedCancelling={speedCancelling}
                        speedError={speedError}
                        setSpeedError={setSpeedError}
                        appliedSpeedX={appliedSpeedX}
                        hasBakedSpeed={hasBakedSpeed}
                        bakedSpeed={bakedSpeed}
                        applyVideoSpeed={applyVideoSpeed}
                        cancelVideoSpeed={cancelVideoSpeed}
                        wantNoVocals={wantNoVocals}
                        stemStatus={stemStatus}
                        stemProgress={stemProgress}
                        stemError={stemError}
                        setStemRetry={setStemRetry}
                        globalVoice={globalVoice}
                        setGlobalVoice={setGlobalVoice}
                        globalTtsVolume={globalTtsVolume}
                        setGlobalTtsVolume={setGlobalTtsVolume}
                        globalTtsSpeed={globalTtsSpeed}
                        setGlobalTtsSpeed={setGlobalTtsSpeed}
                        onDub={onDub}
                        jobStep={jobStep}
                        jobProgress={jobProgress}
                        coverMaskStyle={coverMaskStyle}
                        coverMaskColor={coverMaskColor}
                        coverMaskOpacity={coverMaskOpacity}
                        selectedBox={selectedBox}
                        commitCoverBox={commitCoverBox}
                        stretchCoverFullWidth={stretchCoverFullWidth}
                        applyCoverMaskToAll={applyCoverMaskToAll}
                        resetOcrRegion={resetOcrRegion}
                        logoDraft={logoDraft}
                        setLogoDraft={setLogoDraft}
                        fitTextLogo={fitTextLogo}
                        logoError={logoError}
                        logoApplying={logoApplying}
                        logoToggleDisabled={logoToggleDisabled}
                        logoToggleRemoves={logoToggleRemoves}
                        unapplyLogo={unapplyLogo}
                        applyLogoDraft={applyLogoDraft}
                        appliedLogo={appliedLogo}
                        editLogo={editLogo}
                      />
                    </SortablePanel>
                    )}
                  </React.Fragment>
                ))}
              </ResizablePanelGroup>
              </SortableContext>
            </DndContext>
          </ResizablePanel>

          <ResizableHandle />

          {/* ── BOTTOM: Timeline (CapCut — rộng, track cao) ── */}
          <ResizablePanel id="timeline" defaultSize={38} minSize={10} maxSize={80} className="min-h-0 pb-2 pt-0.5">
            <div
              className="panel bg-background h-full flex flex-col rounded-sm border border-border overflow-hidden"
            >

              {/* Timeline toolbar — bản gốc (trước chỉnh CapCut icon) */}
              <div className="flex items-center justify-between h-10 border-b border-border shrink-0 px-2.5">
                <div className="flex items-center gap-0.5">
                  <TlButton
                    title={canUndo ? 'Hoàn tác (Ctrl+Z) — gồm tốc độ bake' : 'Hoàn tác (Ctrl+Z)'}
                    disabled={!canUndo}
                    onClick={undoEdit}
                  >
                    <TabSvg><path d="M3 7v6h6" /><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6.7 2.9L3 13" /></TabSvg>
                  </TlButton>
                  <TlButton
                    title={canRedo ? 'Làm lại (Ctrl+Shift+Z / Ctrl+Y)' : 'Làm lại (Ctrl+Shift+Z)'}
                    disabled={!canRedo}
                    onClick={redoEdit}
                  >
                    <TabSvg><path d="M21 7v6h-6" /><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6.7 2.9L21 13" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton
                    title={canSplit ? 'Split tại playhead' : (splitDisabledReason || 'Split')}
                    disabled={!canSplit}
                    onClick={splitAtPlayhead}
                  >
                    <TabSvg>
                      <path d="M8 4v16" /><path d="M16 4v16" />
                      <path d="M4 8h4" /><path d="M4 16h4" />
                      <path d="M16 8h4" /><path d="M16 16h4" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Xóa trái playhead (trim left)" disabled={!canTrimLeft} onClick={trimLeftToPlayhead}>
                    <TabSvg>
                      <path d="M12 4v16" /><path d="M12 8h6" /><path d="M12 16h6" />
                      <path d="M4 6l4 6-4 6" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Xóa phải playhead (trim right)" disabled={!canTrimRight} onClick={trimRightToPlayhead}>
                    <TabSvg>
                      <path d="M12 4v16" /><path d="M6 8h6" /><path d="M6 16h6" />
                      <path d="M20 6l-4 6 4 6" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Xóa clip đã chọn (Del)" disabled={!canDeleteClip} onClick={deleteSelectedClip}>
                    <TabSvg><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6M14 11v6" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton title="Nhân đôi clip" disabled={!canDuplicate} onClick={duplicateClip}>
                    <TabSvg><rect x="8" y="8" width="12" height="12" rx="1" /><path d="M4 16V5a1 1 0 0 1 1-1h11" /></TabSvg>
                  </TlButton>
                  <TlButton title="Nam châm track chính — tự đóng khoảng trống khi xóa (T)" active={mainTrackMagnet} onClick={() => setMainTrackMagnet((value) => !value)}>
                    <TabSvg>
                      <path d="M5 4v7a7 7 0 0 0 14 0V4" />
                      <path d="M5 8h4M15 8h4" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Tự động bắt dính mép clip và playhead (Y)" active={autoSnapping} onClick={() => setAutoSnapping((value) => !value)}>
                    <TabSvg>
                      <path d="M12 3v18" />
                      <path d="M3 8h5l-2-2M8 8 6 10" />
                      <path d="M21 16h-5l2-2M16 16l2 2" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Liên kết Video với Âm gốc" active={mediaLinked} onClick={() => setMediaLinked((value) => !value)}>
                    <TabSvg>
                      <path d="M10 13a5 5 0 0 0 7.54.54l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15" />
                      <path d="M14 11a5 5 0 0 0-7.54-.54l-2 2a5 5 0 0 0 7.07 7.07l1.14-1.14" />
                    </TabSvg>
                  </TlButton>
                  <TlButton title="Tách âm thanh → Xóa lời" disabled={busy} onClick={extractAudioFromVideo}>
                    <TabSvg>
                      <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
                      <path d="M3 3l18 18" />
                    </TabSvg>
                  </TlButton>
                  <TlButton
                    title={bookmarkActive ? 'Xóa bookmark tại playhead' : 'Thêm bookmark'}
                    active={bookmarkActive}
                    onClick={toggleBookmarkAtPlayhead}
                  >
                    <TabSvg><path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" /></TabSvg>
                  </TlButton>
                  <div className="w-px h-5 bg-border mx-0.5" />
                  <TlButton title="Thêm text overlay tại playhead" onClick={() => addTextOverlay()}>
                    <TabSvg><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></TabSvg>
                  </TlButton>
                  <TlButton
                    title={'Phím tắt:\nCtrl+Z — Hoàn tác · Space — Play\nCtrl+G — Group · Alt+G — Ghép\nDelete — Xóa'}
                  >
                    <TabSvg><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" /></TabSvg>
                  </TlButton>
                </div>

                <div className="flex items-center gap-1">
                  <TlButton title="Fit 80% ngang" onClick={zoomToFit}>
                    <TabSvg><path d="M8 3H5a2 2 0 0 0-2 2v3" /><path d="M16 3h3a2 2 0 0 1 2 2v3" /><path d="M8 21H5a2 2 0 0 1-2-2v-3" /><path d="M16 21h3a2 2 0 0 0 2-2v-3" /></TabSvg>
                  </TlButton>
                  <TlButton title="Thu nhỏ (tối thiểu 30% khung)" onClick={() => setZoomManual((z) => +(z / 1.5).toFixed(4))}>
                    <TabSvg><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="8" y1="11" x2="14" y2="11" /></TabSvg>
                  </TlButton>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    step={0.25}
                    value={Math.max(0, Math.min(100, zoomSliderValue))}
                    className="w-28 accent-primary"
                    onChange={(e) => setZoomManual(zoomFromSlider(Number(e.target.value)))}
                    title="Trái = 30% khung · Fit = 80% · Phải = phóng to"
                  />
                  <TlButton title="Phóng to" onClick={() => setZoomManual((z) => +(z * 1.5).toFixed(4))}>
                    <TabSvg><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="11" y1="8" x2="11" y2="14" /><line x1="8" y1="11" x2="14" y2="11" /></TabSvg>
                  </TlButton>
                </div>
              </div>

              {/* Timeline body: labels + tracks */}
              <div className="flex flex-1 min-h-0 overflow-hidden">

                {/* Track labels — spacer + rows; scroll Y khớp tracks */}
                <div className="w-[168px] shrink-0 flex flex-col border-r border-border bg-muted/20 min-h-0">
                  <div className="h-7 shrink-0 border-b border-border bg-background/70" />
                  <div
                    ref={labelsScrollRef}
                    className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                    onScroll={syncLabelsY}
                  >
                  <div className="pb-16">
                  {(
                    [
                      {
                        id: 'video' as const,
                        h: 'h-[72px]',
                        label: 'Video',
                        icon: (
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0" aria-hidden>
                            <polygon points="5 3 19 12 5 21 5 3" />
                          </svg>
                        ),
                        mute: false,
                        hide: true,
                        lock: true,
                        focus: 'video' as const,
                      },
                      ...captionLanes.map((lane) => ({
                        id: 'caption' as const,
                        h: 'h-10',
                        label: lane.label,
                        icon: <span className="text-xs leading-none shrink-0">◈</span>,
                        mute: false,
                        hide: true,
                        lock: true,
                        focus: 'caption' as const,
                        laneKey: lane.key,
                      })),
                      {
                        id: 'dub' as const,
                        h: 'h-10',
                        label: 'Lồng tiếng',
                        icon: <IconHeadphones size={13} className="shrink-0" />,
                        mute: true,
                        hide: true,
                        lock: true,
                        focus: 'dub' as const,
                      },
                      {
                        id: 'bg' as const,
                        h: 'h-10',
                        label: 'Âm gốc',
                        icon: (
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0" aria-hidden>
                            <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
                          </svg>
                        ),
                        mute: true,
                        hide: true,
                        lock: false,
                        focus: 'bg' as const,
                      },
                      {
                        id: 'watermark' as const,
                        h: 'h-10',
                        label: 'Watermark',
                        icon: <span className="text-xs leading-none shrink-0">◈</span>,
                        mute: false,
                        hide: true,
                        lock: false,
                        focus: 'watermark' as const,
                      },
                      {
                        id: 'text' as const,
                        h: 'h-10',
                        label: 'Text',
                        icon: <span className="text-xs font-semibold leading-none shrink-0">T</span>,
                        mute: false,
                        hide: true,
                        lock: true,
                        focus: 'text' as const,
                      },
                    ] as const
                  ).map((row, rowIdx) => {
                    // Compound: ẩn nhãn track Caption / Lồng tiếng / Âm gốc (gộp lên Video)
                    if (
                      compoundMode
                      && (row.id === 'caption' || row.id === 'dub' || row.id === 'bg' || row.id === 'watermark')
                    ) {
                      return null
                    }
                    // Chưa lồng tiếng: ẩn tab Lồng tiếng
                    if (row.id === 'dub' && !showDubTrack) return null
                    const muted =
                      row.id === 'bg'
                        ? settings.processOriginalAudio && settings.originalAudioMode === 'mute'
                        : trackMute[row.id]
                    return (
                      <div
                        key={`${row.id}-${'laneKey' in row ? row.laneKey : rowIdx}`}
                        className={cn(
                          row.h,
                          'box-border flex items-center gap-1 px-2 border-b border-border/80 shrink-0 cursor-pointer',
                          trackHidden[row.id] && 'opacity-50',
                          trackFocus === row.focus && 'bg-primary/10',
                        )}
                        onClick={() => {
                          if (row.id === 'video') focusVideo()
                          else if (row.id === 'bg') focusBg()
                          else if (row.id === 'caption') {
                            const laneKey = 'laneKey' in row ? row.laneKey : 'horizontal'
                            const under = segments.find(
                              (s) => captionLaneOf(s, sourceHeight, sourceWidth) === laneKey && time >= s.start && time < s.end,
                            )
                            const hit = under ?? segments.find((s) => captionLaneOf(s, sourceHeight, sourceWidth) === laneKey)
                            if (hit) focusCaption(hit)
                            else setTrackFocus('caption')
                          }
                          else if (row.id === 'dub') {
                            // Chỉ focus track — không gen lại TTS (gen qua nút panel / bar trống)
                            setTrackFocus('dub')
                            setPropTab('audio')
                            if (selected && segmentHasDub(selected)) focusDub(selected, { keepMulti: true })
                          }
                          else if (row.id === 'text' && selectedOverlay) focusText(selectedOverlay.id)
                          else setTrackFocus(row.focus)
                        }}
                        onContextMenu={(e) => openCtxMenu({ kind: 'track', track: row.id, x: e.clientX, y: e.clientY }, e)}
                      >
                        <span className="text-muted-foreground shrink-0 w-4 flex justify-center">{row.icon}</span>
                        <span className="text-[11px] text-muted-foreground truncate flex-1 min-w-0">{row.label}</span>
                        {row.mute && (
                          <TrackCtrl
                            title={muted ? 'Bật tiếng' : 'Tắt tiếng'}
                            active={muted}
                            onClick={() => {
                              if (row.id === 'bg') {
                                if (muted) {
                                  onSettings({
                                    ...settings,
                                    processOriginalAudio: false,
                                    originalAudioMode: 'original',
                                  })
                                } else {
                                  onSettings({
                                    ...settings,
                                    processOriginalAudio: true,
                                    originalAudioMode: 'mute',
                                  })
                                }
                              } else {
                                toggleTrackFlag(setTrackMute, row.id)
                              }
                            }}
                          >
                            {muted ? (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><line x1="23" y1="9" x2="17" y2="15" /><line x1="17" y1="9" x2="23" y2="15" /></svg>
                            ) : (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" /><path d="M15.54 8.46a5 5 0 0 1 0 7.07" /></svg>
                            )}
                          </TrackCtrl>
                        )}
                        {row.hide && (
                          <TrackCtrl
                            title={trackHidden[row.id] ? 'Bỏ làm mờ track' : 'Làm mờ track'}
                            active={trackHidden[row.id]}
                            onClick={() => toggleTrackFlag(setTrackHidden, row.id)}
                          >
                            {trackHidden[row.id] ? (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" /><line x1="1" y1="1" x2="23" y2="23" /></svg>
                            ) : (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
                            )}
                          </TrackCtrl>
                        )}
                        {row.lock && (
                          <TrackCtrl
                            title={trackLocked[row.id] ? 'Mở khóa' : 'Khóa'}
                            active={trackLocked[row.id]}
                            onClick={() => toggleTrackFlag(setTrackLocked, row.id)}
                          >
                            {trackLocked[row.id] ? (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
                            ) : (
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 9.9-1" /></svg>
                            )}
                          </TrackCtrl>
                        )}
                      </div>
                    )
                  })}
                  </div>
                  </div>
                </div>

                {/* Ruler + tracks */}
                <div className="flex flex-col flex-1 min-w-0 relative overflow-hidden" ref={tracksColRef}>

                  {/* Ruler */}
                  <div className="h-7 overflow-hidden shrink-0 border-b border-border bg-background/70" ref={rulerScrollRef}>
                    <div
                      className="relative h-full cursor-crosshair select-none"
                      style={{ width: trackWidth }}
                      onPointerDown={beginScrub}
                    >
                      {ticks.map((tick) => (
                        <React.Fragment key={tick}>
                          <span
                            className="absolute bottom-0 w-px h-2 bg-border pointer-events-none"
                            style={{ left: tick * pxPerSec }}
                          />
                          <span
                            className="absolute top-1 text-[10px] text-muted-foreground translate-x-[-50%] pointer-events-none whitespace-nowrap tabular-nums"
                            style={{ left: tick * pxPerSec }}
                          >
                            {formatTime(tick)}
                          </span>
                        </React.Fragment>
                      ))}
                      {bookmarks.map((mark) => (
                        <button
                          key={mark}
                          type="button"
                          title={`Bookmark ${formatTime(mark)}`}
                          className="absolute top-0 z-[2] w-0 h-0 border-l-[5px] border-r-[5px] border-t-[8px] border-l-transparent border-r-transparent border-t-sky-400 -translate-x-1/2 hover:border-t-sky-300"
                          style={{ left: mark * pxPerSec }}
                          onPointerDown={(e) => {
                            e.stopPropagation()
                            e.preventDefault()
                            seekPlayhead(mark)
                          }}
                        />
                      ))}
                    </div>
                  </div>

                  {/* Master scroll area — nền tối + khoảng trống dưới track */}
                  <div
                    className="flex-1 overflow-x-auto overflow-y-auto scrollbar-thin bg-black/25"
                    ref={tracksScrollRef}
                    onScroll={syncFollowers}
                    onPointerDown={(e) => {
                      // Kéo trên vùng trống (không trúng clip) → marquee chọn
                      if ((e.target as HTMLElement).closest(
                        '[data-caption-clip],[data-media-clip],[data-dub-clip],[data-text-clip]',
                      )) return
                      beginMarqueeSelect(e)
                    }}
                    onContextMenu={(e) => {
                      // Chuột phải vùng trống khi đã multi-select → menu xử lý cả nhóm
                      const multi =
                        selectedIds.length >= 2
                        || selectedDubIds.length >= 2
                        || (selectedIds.length + selectedDubIds.length >= 2)
                      if (!multi) return
                      const id =
                        selectedId
                        || selectedIds[0]
                        || selectedDubIds[0]
                        || null
                      if (!id) return
                      openCtxMenu(
                        { kind: 'segment', segId: id, x: e.clientX, y: e.clientY },
                        e,
                      )
                    }}
                  >
                    <div
                      className="flex flex-col min-h-full pb-16 relative overflow-x-hidden"
                      style={{ width: trackWidth }}
                    >
                      {marquee && (
                        <div
                          className="pointer-events-none absolute z-[40] border border-sky-400 bg-sky-400/15 rounded-sm"
                          style={{
                            left: Math.min(marquee.x0, marquee.x1),
                            top: Math.min(marquee.y0, marquee.y1),
                            width: Math.max(1, Math.abs(marquee.x1 - marquee.x0)),
                            height: Math.max(1, Math.abs(marquee.y1 - marquee.y0)),
                          }}
                        />
                      )}

                      {/* Video track — clip riêng (split độc lập) */}
                      <div
                        ref={trackRef}
                        className={cn(
                          'relative h-[72px] box-border border-b border-border/80 bg-black/50 overflow-hidden',
                          trackHidden.video && 'opacity-30',
                        )}
                        onPointerDown={(e) => {
                          if ((e.target as HTMLElement).closest('[data-media-clip]')) return
                          focusVideo()
                          beginScrub(e)
                        }}
                        onContextMenu={(e) => openCtxMenu({ kind: 'track', track: 'video', x: e.clientX, y: e.clientY }, e)}
                      >
                        {/* Filmstrip theo từng clip — đoạn đã xóa = lỗ trống, không vẽ full bar */}
                        {videoUrl && videoClips.map((clip) => {
                          const display = groupDraft?.[clip.id]
                            ? { ...clip, ...groupDraft[clip.id] }
                            : draft?.id === clip.id ? { ...clip, ...draft } : clip
                          const w = Math.max(2, (display.end - display.start) * pxPerSec)
                          const isSelected =
                            (trackFocus === 'video' && selectedMediaId === clip.id)
                            || selectedMediaIds.includes(clip.id)
                          return (
                            <button
                              key={clip.id}
                              type="button"
                              data-media-clip="video"
                              data-clip-id={clip.id}
                              title={`Video ${formatTime(display.start)}–${formatTime(display.end)}`}
                              className={cn(
                                'absolute top-2 h-[calc(100%-16px)] rounded-md border-0 cursor-pointer z-[1] overflow-hidden p-0',
                                isSelected
                                  ? 'ring-[1.5px] ring-primary shadow-sm'
                                  : 'ring-1 ring-white/30 hover:ring-white/50',
                              )}
                              style={{
                                left: display.start * pxPerSec,
                                width: w,
                              }}
                              onPointerDown={(e) => {
                                if (e.button !== 0) return
                                beginMediaDrag(e, 'video', clip, 'move')
                              }}
                              onClick={(e) => {
                                e.stopPropagation()
                                focusVideo(clip.id)
                                selectClipKeepPlayhead(display.start, display.end)
                              }}
                            >
                              <span
                                className="absolute inset-y-0 left-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                                onPointerDown={(e) => beginMediaDrag(e, 'video', clip, 'start')}
                              />
                              <TimelineFilmstrip
                                videoUrl={videoUrl}
                                duration={videoSpan}
                                widthPx={w}
                                heightPx={56}
                                className="absolute inset-0 pointer-events-none"
                                startSec={display.start}
                                endSec={display.end}
                              />
                              <span
                                className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                                onPointerDown={(e) => beginMediaDrag(e, 'video', clip, 'end')}
                              />
                            </button>
                          )
                        })}
                        {/* Compound shell trên Video (CapCut Alt+G — caption/TTS ẩn) */}
                        {compoundShells.map((shell) => {
                          const n = shell.compoundChildren?.length || 0
                          const isSelected =
                            trackFocus === 'video' &&
                            (selectedId === shell.id || selectedIds.includes(shell.id))
                          return (
                            <button
                              key={shell.id}
                              type="button"
                              data-compound-shell=""
                              data-seg-id={shell.id}
                              title={`Compound ×${n} · ${formatTime(shell.start)}–${formatTime(shell.end)} · tháo: Ctrl+Shift+G`}
                              className={cn(
                                'absolute top-2 h-[calc(100%-16px)] rounded-md border-0 cursor-pointer z-[2] text-[10px] text-white/95 px-1.5 flex items-center justify-center overflow-hidden',
                                isSelected
                                  ? 'ring-[1.5px] ring-violet-300 bg-violet-600/55'
                                  : 'ring-1 ring-violet-400/70 bg-violet-700/40 hover:bg-violet-600/50',
                              )}
                              style={{
                                left: shell.start * pxPerSec,
                                width: Math.max(2, (shell.end - shell.start) * pxPerSec),
                                boxSizing: 'border-box',
                              }}
                              onPointerDown={(e) => e.stopPropagation()}
                              onClick={(e) => {
                                e.stopPropagation()
                                setSelectedId(shell.id)
                                setSelectedIds([shell.id])
                                setSelectedMediaIds([])
                                setSelectedDubIds([])
                                setTrackFocus('video')
                                selectClipKeepPlayhead(shell.start, shell.end)
                              }}
                              onContextMenu={(e) => {
                                setSelectedId(shell.id)
                                setSelectedIds([shell.id])
                                setTrackFocus('video')
                                openCtxMenu({ kind: 'segment', segId: shell.id, x: e.clientX, y: e.clientY }, e)
                              }}
                            >
                              <span className="truncate pointer-events-none">×{n}</span>
                            </button>
                          )
                        })}
                      </div>

                      {/* Caption lanes — ẩn khi compound (gộp lên Video, giống CapCut) */}
                      {!compoundMode && captionLanes.map((lane) => (
                      <div
                        key={lane.key}
                        className={cn('relative h-10 box-border border-b border-border/80 overflow-hidden', trackHidden.caption && 'opacity-30')}
                        style={{ backgroundColor: 'var(--background)' }}
                        onPointerDown={(e) => {
                          if ((e.target as HTMLElement).closest('[data-caption-clip]')) return
                          setTrackFocus('caption')
                          beginMarqueeSelect(e)
                        }}
                      >
                        {timelineLayoutSegs.filter((seg) => captionLaneOf(seg, sourceHeight, sourceWidth) === lane.key).map((seg) => {
                          const gd = groupDraft?.[seg.id]
                          const display = gd
                            ? { ...seg, ...gd }
                            : draft?.id === seg.id
                              ? { ...seg, ...draft }
                              : seg
                          const inGroup = selectedIds.includes(seg.id)
                          const linked = Boolean(seg.groupId)
                          const isSelected =
                            trackFocus === 'caption' && (seg.id === selected?.id || inGroup)
                          return (
                            <button
                              key={seg.id}
                              type="button"
                              data-caption-clip=""
                              data-seg-id={seg.id}
                              title={`${formatTime(display.start)}–${formatTime(display.end)}${seg.isCompound ? ` · Compound ×${seg.compoundChildren?.length || 0}` : ''}${linked ? ' · Group' : ''}${inGroup && selectedIds.length > 1 ? ` · chọn ${selectedIds.length}` : ''}`}
                              className={cn(
                                'absolute top-1.5 h-[calc(100%-12px)] rounded-md text-[11px] text-white whitespace-nowrap overflow-hidden px-2 flex items-center justify-center text-center cursor-pointer border-0 transition-opacity hover:opacity-90',
                                isSelected && 'ring-[1.5px] ring-primary',
                                inGroup && selectedIds.length > 1 && 'ring-[1.5px] ring-sky-300',
                                linked && 'outline outline-1 outline-offset-[-1px] outline-white/50',
                                seg.isCompound && 'ring-[1.5px] ring-violet-400 bg-violet-600/90',
                                trackLocked.caption && 'cursor-not-allowed',
                              )}
                              style={{
                                left: display.start * pxPerSec,
                                width: Math.max(2, (display.end - display.start) * pxPerSec),
                                boxSizing: 'border-box',
                                background: isSelected ? lane.selected : lane.color,
                              }}
                              onClick={(e) => {
                                e.stopPropagation()
                                if (e.shiftKey) focusCaption(seg, { range: true })
                                else if (e.ctrlKey || e.metaKey) focusCaption(seg, { additive: true })
                                else {
                                  focusCaption(seg)
                                  selectClipKeepPlayhead(display.start, display.end)
                                }
                              }}
                              onContextMenu={(e) => {
                                // Giữ multi-select khi RMB vào clip đã chọn; chỉ single nếu click ngoài selection
                                if (!selectedIds.includes(seg.id) && !selectedDubIds.includes(seg.id)) {
                                  focusCaption(seg)
                                } else {
                                  setSelectedId(seg.id)
                                  setTrackFocus('caption')
                                  // Không xóa selectedIds / selectedDubIds
                                }
                                openCtxMenu({ kind: 'segment', segId: seg.id, x: e.clientX, y: e.clientY }, e)
                              }}
                              onPointerDown={(e) => {
                                if (e.button !== 0) return
                                // Ctrl/Shift click: chỉ chọn, không kéo ngay
                                if (e.ctrlKey || e.metaKey || e.shiftKey) return
                                beginDrag(e, seg, 'move')
                              }}
                            >
                              <span
                                className="absolute inset-y-0 left-0 w-2.5 cursor-ew-resize rounded-l-md hover:bg-white/25 transition-colors z-10"
                                onPointerDown={(e) => {
                                  e.stopPropagation()
                                  beginDrag(e, seg, 'start')
                                }}
                              />
                              <span className="truncate relative z-[1] pointer-events-none">{seg.translation || lane.label}</span>
                              <span
                                className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize rounded-r-md hover:bg-white/25 transition-colors z-10"
                                onPointerDown={(e) => {
                                  e.stopPropagation()
                                  beginDrag(e, seg, 'end')
                                }}
                              />
                            </button>
                          )
                        })}
                      </div>
                      ))}

                      {/* Dub / TTS track — chỉ khi đã có file TTS (chưa xong dub → không hiện) */}
                      {showDubTrack && (
                      <div className={cn('relative h-10 box-border border-b border-border/80 overflow-hidden', trackHidden.dub && 'opacity-30')} style={{ backgroundColor: 'var(--background)' }}>
                        {(() => {
                          const dubs = segments.filter(
                            (seg) => !seg.isCompound && segmentHasDub(seg) && seg.audioUrl,
                          )
                          return dubs.map((seg) => {
                            const clipSec = dubClipSeconds(
                              seg,
                              segments,
                              previewVideoRate(
                                settings.matchDuration,
                                bakedPreferVideo,
                                seg.videoSpeed,
                                bakedSpeed,
                                hasBakedSpeed,
                              ),
                              bakedSpeed,
                            )
                            const isSelected =
                              (trackFocus === 'dub' && seg.id === selected?.id)
                              || selectedDubIds.includes(seg.id)
                            return (
                              <button
                                key={seg.id}
                                type="button"
                                data-dub-clip=""
                                data-seg-id={seg.id}
                                title={`TTS ${(seg.audioDuration ?? 0).toFixed(2)}s`}
                                className={cn(
                                  'absolute top-1.5 h-[calc(100%-12px)] rounded-md text-[11px] text-white whitespace-nowrap overflow-hidden px-2 flex items-center cursor-pointer border-0 transition-opacity hover:opacity-90',
                                  isSelected && 'ring-[1.5px] ring-amber-200',
                                )}
                                style={{
                                  left: seg.start * pxPerSec,
                                  width: Math.max(
                                    2,
                                    Math.min(clipSec, Math.max(0.05, timelineDuration - seg.start)) * pxPerSec,
                                  ),
                                  boxSizing: 'border-box',
                                  background: isSelected ? '#c2780a' : '#E8A045',
                                }}
                                onClick={() => {
                                  focusDub(seg)
                                  selectClipKeepPlayhead(seg.start, seg.end)
                                }}
                                 onContextMenu={(e) => {
                                   // Multi: RMB giữ selection; single: focus clip
                                   if (
                                     selectedDubIds.includes(seg.id)
                                     || selectedIds.includes(seg.id)
                                   ) {
                                     setSelectedId(seg.id)
                                     setTrackFocus('dub')
                                   } else {
                                     focusDub(seg)
                                   }
                                   // Có caption multi → menu segment (group/compound)
                                   if (selectedIds.length >= 2 || selectedDubIds.length >= 2) {
                                     openCtxMenu(
                                       { kind: 'segment', segId: selectedId || selectedIds[0] || seg.id, x: e.clientX, y: e.clientY },
                                       e,
                                     )
                                   } else {
                                     openCtxMenu({ kind: 'dub', segId: seg.id, x: e.clientX, y: e.clientY }, e)
                                   }
                                 }}
                               >
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
                                       if (!historyQuietRef.current) pushHistory()
                                       const nextSegs = segments.map(s => {
                                         if (s.voice && s.voice !== 'none') {
                                           return { ...s, ttsVolume: v }
                                         }
                                         return s
                                       })
                                       void onSegmentsReplace(nextSegs)
                                     }
                                   }}
                                 />
                               </button>
                             )
                           })
                         })()}
                       </div>
                      )}

                       {/* Âm gốc / nền — ẩn khi compound (gộp lên Video) */}
                       {!compoundMode && (
                       <div className={cn('relative h-10 box-border border-b border-border/80 overflow-hidden', trackHidden.bg && 'opacity-30')} style={{ backgroundColor: 'var(--background)' }}>
                         {(() => {
                           const on = settings.processOriginalAudio
                           const mode = settings.originalAudioMode
                           let baseLabel = workClipSec > 0 ? `Âm gốc (${Number(workClipSec).toFixed(1)}s)` : 'Âm gốc'
                          let bg = '#5B8DEF'
                          const stemPct = Math.max(0, Math.min(100, Math.round(stemProgress || 0)))
                          const stemLoading =
                            on && mode === 'no_vocals' && (stemStatus === 'loading' || stemStatus === 'off')
                          if (on && mode === 'no_vocals') {
                            if (stemLoading) {
                              baseLabel =
                                stemPct > 0
                                  ? `Xóa lời… ${stemPct}%`
                                  : 'Xóa lời… đang tách'
                              bg = '#7a8eb0'
                            } else if (stemStatus === 'error') {
                              baseLabel = 'Xóa lời — lỗi tách (bấm Âm thanh → Thử lại)'
                              bg = '#c44'
                            } else if (stemStatus === 'ready') {
                              baseLabel = `Xóa lời · nền ${Math.max(0, Math.min(200, settings.originalAudioVolume ?? 100))}%`
                              bg = '#3D7AE5'
                            } else {
                              baseLabel = 'Xóa lời… 1%'
                              bg = '#7a8eb0'
                            }
                          } else if (on && mode === 'vocals') {
                            baseLabel = 'Chỉ giữ lời (khi xuất)'
                            bg = '#6B5B95'
                          } else if (on && mode === 'mute') {
                            baseLabel = 'Tắt âm gốc'
                            bg = '#666'
                          }
                          return bgClips.map((clip) => {
                            const display = groupDraft?.[clip.id]
                              ? { ...clip, ...groupDraft[clip.id] }
                              : draft?.id === clip.id ? { ...clip, ...draft } : clip
                            const isSelected = (trackFocus === 'bg' && selectedMediaId === clip.id) || selectedMediaIds.includes(clip.id)
                            const fillPct = stemLoading ? Math.max(2, Math.min(98, stemPct || 1)) : 100
                            return (
                              <button
                                key={clip.id}
                                type="button"
                                data-media-clip="bg"
                                data-clip-id={clip.id}
                                title={`${baseLabel} · ${formatTime(display.start)}–${formatTime(display.end)}`}
                                className={cn(
                                  'absolute top-1.5 h-[calc(100%-12px)] rounded-md text-[11px] text-white whitespace-nowrap overflow-hidden px-2 flex items-center cursor-pointer border-0 hover:opacity-90',
                                  isSelected && 'ring-[1.5px] ring-sky-300',
                                  stemLoading && 'cursor-wait',
                                )}
                                style={{
                                  left: display.start * pxPerSec,
                                  width: Math.max(2, (display.end - display.start) * pxPerSec),
                                  boxSizing: 'border-box',
                                  background: stemLoading
                                    ? `linear-gradient(90deg, #3D7AE5 ${fillPct}%, #7a8eb0 ${fillPct}%)`
                                    : bg,
                                  opacity: on && mode === 'mute' ? 0.45 : 0.92,
                                }}
                                onPointerDown={(e) => {
                                  if (e.button !== 0 || stemLoading) return
                                  beginMediaDrag(e, 'bg', clip, 'move')
                                }}
                                onClick={() => {
                                  focusBg(clip.id)
                                  selectClipKeepPlayhead(display.start, display.end)
                                }}
                                onContextMenu={(e) => {
                                  focusBg(clip.id)
                                  openCtxMenu({ kind: 'bg', x: e.clientX, y: e.clientY }, e)
                                }}
                              >
                                <AudioWaveformBg />
                                <span
                                  className="absolute inset-y-0 left-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                                  onPointerDown={(e) => beginMediaDrag(e, 'bg', clip, 'start')}
                                />
                                <span className="truncate pointer-events-none relative z-10">{baseLabel}</span>
                                <VolumeSlider
                                  initialVolume={settings.originalAudioVolume ?? 100}
                                  maxVolume={200}
                                  onChangeEnd={(v) => {
                                    if (v !== (settings.originalAudioVolume ?? 100)) {
                                      if (!historyQuietRef.current) pushHistory()
                                      onSettings({ ...settings, originalAudioVolume: v })
                                    }
                                  }}
                                />
                                <span
                                  className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                                  onPointerDown={(e) => beginMediaDrag(e, 'bg', clip, 'end')}
                                />
                              </button>
                            )
                             })
                           })()}
                         </div>
                       )}

                       {/* Watermark tĩnh là effect clip thực: trim/kéo/resize như Caption. */}
                       {!compoundMode && (
                       <div className={cn('relative h-10 box-border border-b border-border/80 overflow-hidden', trackHidden.watermark && 'opacity-30')} style={{ backgroundColor: 'var(--background)' }}>
                         {overlays.filter(isWatermarkOverlay).map((overlay) => {
                           const display = draft?.id === overlay.id ? { ...overlay, ...draft } : overlay
                           return (
                             <button
                               key={overlay.id}
                               type="button"
                               data-text-clip=""
                               data-overlay-id={overlay.id}
                               className={cn(
                                 'absolute top-1.5 h-[calc(100%-12px)] rounded-md border-0 text-[11px] text-white whitespace-nowrap overflow-hidden px-2 cursor-pointer flex items-center transition-opacity hover:opacity-90',
                                 trackFocus === 'watermark' && overlay.id === selectedOverlayId && 'ring-[1.5px] ring-fuchsia-300',
                                 trackLocked.text && 'cursor-not-allowed',
                               )}
                               style={{ left: display.start * pxPerSec, width: Math.max(2, (display.end - display.start) * pxPerSec), boxSizing: 'border-box', background: '#0f766e' }}
                               onPointerDown={(e) => { if (e.button === 0) beginTimelineTextDrag(e, overlay, 'move') }}
                               onClick={() => { focusText(overlay.id); selectClipKeepPlayhead(display.start, display.end) }}
                             >
                               <span className="absolute inset-y-0 left-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10" onPointerDown={(e) => beginTimelineTextDrag(e, overlay, 'start')} />
                               <span className="truncate pointer-events-none">{overlay.watermarkSource || 'AI生成+'}</span>
                               <span className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10" onPointerDown={(e) => beginTimelineTextDrag(e, overlay, 'end')} />
                             </button>
                           )
                         })}
                         {(logoDetection?.tracks || [])
                           .filter((track) => (track.text || '').trim().startsWith('@'))
                           .map((track, index) => {
                           const label = (track.text || '').trim()
                           const start = Math.max(0, Number(track.start || 0))
                           const end = Math.max(start + 0.04, Number(track.end || start + 0.04))
                           return (
                             <div
                               key={`watermark-track-${label}-${start}-${index}`}
                               title={`${label} · động — không che tự động`}
                               className="absolute top-1.5 h-[calc(100%-12px)] rounded-md px-2 text-[10px] text-white whitespace-nowrap overflow-hidden flex items-center"
                               style={{
                                 left: start * pxPerSec,
                                 width: Math.max(3, (end - start) * pxPerSec),
                                 background: '#a16207',
                                 opacity: 0.72,
                               }}
                             >
                               {label}
                             </div>
                           )
                         })}
                       </div>
                       )}

                       {/* Text overlay track */}
                       <div className={cn('relative h-10 box-border border-b border-border/80 overflow-hidden', trackHidden.text && 'opacity-30')} style={{ backgroundColor: 'var(--background)' }}>
                         {overlays.filter((overlay) => !isWatermarkOverlay(overlay)).map((overlay) => {
                           const display = draft?.id === overlay.id ? { ...overlay, ...draft } : overlay
                           return (
                           <button
                             key={overlay.id}
                            type="button"
                            data-text-clip=""
                            data-overlay-id={overlay.id}
                            className={cn(
                              'absolute top-1.5 h-[calc(100%-12px)] rounded-md border-0 text-[11px] text-white whitespace-nowrap overflow-hidden px-2 cursor-pointer flex items-center transition-opacity hover:opacity-90',
                              trackFocus === 'text' && overlay.id === selectedOverlayId && 'ring-[1.5px] ring-yellow-300',
                              trackLocked.text && 'cursor-not-allowed',
                            )}
                            style={{
                               left: display.start * pxPerSec,
                               width: Math.max(2, (display.end - display.start) * pxPerSec),
                              boxSizing: 'border-box',
                              background: trackFocus === 'text' && overlay.id === selectedOverlayId ? '#d97706' : '#E8913A',
                            }}
                             onPointerDown={(e) => {
                               if (e.button !== 0) return
                               beginTimelineTextDrag(e, overlay, 'move')
                             }}
                             onClick={() => {
                               focusText(overlay.id)
                               selectClipKeepPlayhead(display.start, display.end)
                             }}
                            onContextMenu={(e) => {
                              focusText(overlay.id)
                              openCtxMenu({ kind: 'overlay', overlayId: overlay.id, x: e.clientX, y: e.clientY }, e)
                            }}
                           >
                             <span
                               className="absolute inset-y-0 left-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                               onPointerDown={(e) => beginTimelineTextDrag(e, overlay, 'start')}
                             />
                             <span className="truncate pointer-events-none">{overlay.text}</span>
                             <span
                               className="absolute inset-y-0 right-0 w-2.5 cursor-ew-resize hover:bg-white/25 z-10"
                               onPointerDown={(e) => beginTimelineTextDrag(e, overlay, 'end')}
                             />
                           </button>
                           )
                         })}
                      </div>

                    </div>
                  </div>

                  {/* Playhead */}
                  <div
                    className="absolute inset-y-0 w-0 z-30 pointer-events-none"
                    style={{ left: playheadPx }}
                    aria-hidden
                  >
                    <div
                      className="absolute top-0 left-1/2 -translate-x-1/2 cursor-col-resize pointer-events-auto"
                      onPointerDown={(e) => { e.stopPropagation(); beginScrub(e) }}
                    >
                      <div className="w-0 h-0" style={{
                        borderLeft: '5px solid transparent',
                        borderRight: '5px solid transparent',
                        borderTop: '8px solid hsl(200, 90%, 52%)',
                      }} />
                    </div>
                    <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-[1.5px] bg-primary opacity-90" />
                  </div>

                </div>
              </div>
            </div>
          </ResizablePanel>

        </ResizablePanelGroup>
          </ResizablePanel>

          {/* Right col: preview full height — ch\u1ec9 trong 'vertical' mode */}
          {layoutPreset === 'vertical' && (
            <>
              <ResizableHandle />
              <ResizablePanel id="right-col" defaultSize={30} minSize={15} maxSize={60} className="min-h-0 pr-2 pb-2">
                <div
                  ref={(el) => { if (el) setRightColPortalEl(el) }}
                  className="relative h-full w-full"
                />
              </ResizablePanel>
            </>
          )}

        </ResizablePanelGroup>
      </div>

      {ctxMenu && createPortal(
        <div
          ref={ctxMenuRef}
          className="fixed z-[9999] min-w-[220px] max-h-[min(70vh,420px)] overflow-y-auto rounded-md border border-border bg-background text-foreground shadow-xl py-1 text-xs"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          onPointerDown={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onContextMenu={(e) => {
            e.preventDefault()
            e.stopPropagation()
          }}
        >
          {ctxMenu.kind === 'segment' && (() => {
            const seg = segments.find((s) => s.id === ctxMenu.segId)
            if (!seg) return null
            // Snapshot ids lúc mở menu (marquee multi)
            const multiIds = selectionCaptionIds(seg.id, ctxMenu.ids)
            const groupN = multiIds.length
            const multi = groupN >= 2
            const targets = multi
              ? segments.filter((s) => multiIds.includes(s.id))
              : [seg]
            const allDubOn = targets.every((s) => segmentHasDub(s))
            const anyTrans = targets.some((s) => Boolean(s.translation?.trim()))
            const anyLayout = targets.some((s) => s.bbox || s.captionLayout)
            return (
              <>
                {multi && (
                  <div className="px-3 py-1.5 text-[10px] text-muted-foreground border-b border-border/60">
                    Đang chọn {groupN} clip — thao tác áp dụng tất cả
                  </div>
                )}
                {!multi && (
                  <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('caption'); setCtxMenu(null) }}>Mở Phụ đề</CtxItem>
                )}
                {multi && (
                  <>
                    <CtxItem
                      onClick={() => {
                        setSelectedIds(multiIds)
                        setSelectedId(multiIds[0])
                        setSelectedDubIds([])
                        setTrackFocus('caption')
                        setCtxMenu(null)
                        groupSelectedCaptions(multiIds)
                      }}
                    >
                      Group {groupN} clip (Ctrl+G)
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        setSelectedIds(multiIds)
                        setSelectedId(multiIds[0])
                        setSelectedDubIds([])
                        setTrackFocus('caption')
                        setCtxMenu(null)
                        createCompoundFromSelection(multiIds)
                      }}
                    >
                      Ghép {groupN} clip → chỉ video (Alt+G)
                    </CtxItem>
                    <CtxSep />
                  </>
                )}
                {!multi && seg.groupId && !seg.isCompound && (
                  <CtxItem
                    onClick={() => {
                      setCtxMenu(null)
                      ungroupSelectedCaptions()
                    }}
                  >
                    Ungroup (Ctrl+Shift+G)
                  </CtxItem>
                )}
                {!multi && seg.isCompound && (
                  <CtxItem
                    onClick={() => {
                      setCtxMenu(null)
                      uncompoundSelected()
                    }}
                  >
                    Tháo compound (Ctrl+Shift+G)
                  </CtxItem>
                )}
                {!multi && (
                  <>
                    <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('video'); setCtxMenu(null) }}>Mở Video</CtxItem>
                    <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                    <CtxSep />
                  </>
                )}
                <CtxItem
                  disabled={busy}
                  onClick={() => {
                    patchSelectedCaptions(seg.id, (s) => ({
                      ...s,
                      dub: !allDubOn,
                      ...(!allDubOn
                        ? {}
                        : { audioUrl: undefined, audioFile: undefined, audioDuration: undefined }),
                    }), multiIds)
                    setCtxMenu(null)
                  }}
                >
                  {allDubOn
                    ? (multi ? `Tắt lồng tiếng ${groupN} clip` : 'Tắt lồng tiếng')
                    : (multi ? `Bật lồng tiếng ${groupN} clip` : 'Bật lồng tiếng')}
                </CtxItem>
                <CtxItem
                  disabled={busy || ttsBusy || !anyTrans}
                  onClick={() => {
                    setCtxMenu(null)
                    if (multi) {
                      setSelectedIds(multiIds)
                      setSelectedId(multiIds[0])
                      void (async () => {
                        for (const t of targets) {
                          if (!t.translation?.trim()) continue
                          await previewTts(t)
                        }
                      })()
                    } else {
                      void previewTts(seg)
                    }
                  }}
                >
                  {multi ? `Tạo lại TTS ${groupN} clip` : 'Tạo lại TTS'}
                </CtxItem>
                {!multi && (
                  <CtxItem
                    disabled={!seg.audioUrl}
                    onClick={() => { playSegmentDub(seg); setCtxMenu(null) }}
                  >
                    Phát với timeline
                  </CtxItem>
                )}
                <CtxSep />
                {[1, 1.25, 1.5].map((v) => (
                  <CtxItem
                    key={v}
                    onClick={() => {
                      patchSelectedCaptions(seg.id, (s) => ({ ...s, videoSpeed: v }), multiIds)
                      setCtxMenu(null)
                    }}
                  >
                    Tốc độ video {v}×{!multi && (seg.videoSpeed ?? 1) === v ? ' ✓' : ''}
                    {multi ? ` · ${groupN} clip` : ''}
                  </CtxItem>
                ))}
                {anyLayout && (
                  <>
                    <CtxSep />
                    <CtxItem
                      onClick={() => {
                        patchSelectedCaptions(seg.id, (s) => ({
                          ...s,
                          bbox: null,
                          captionLayout: null,
                        }), multiIds)
                        setCtxMenu(null)
                      }}
                    >
                      {multi ? `Reset layout ${groupN} clip` : 'Reset layout caption'}
                    </CtxItem>
                  </>
                )}
                {multi && (
                  <>
                    <CtxSep />
                    <CtxItem
                      disabled={busy}
                      onClick={() => {
                        setCtxMenu(null)
                        const drop = new Set(multiIds)
                        pushHistory()
                        void onSegmentsReplace(
                          reindexSegments(segments.filter((s) => !drop.has(s.id))),
                        )
                        setSelectedIds([])
                        setSelectedId(null)
                        setSelectedDubIds([])
                      }}
                    >
                      Xóa {groupN} clip
                    </CtxItem>
                  </>
                )}
              </>
            )
          })()}

          {ctxMenu.kind === 'dub' && (() => {
            const seg = segments.find((s) => s.id === ctxMenu.segId)
            if (!seg) return null
            return (
              <>
                <CtxItem onClick={() => { setSelectedId(seg.id); setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                <CtxSep />
                {[0, 50, 100, 150].map((v) => (
                  <CtxItem key={v} onClick={() => { editSegment({ ...seg, ttsVolume: v }); setCtxMenu(null) }}>
                    Âm lượng TTS {v === 0 ? 'Tắt' : `${v}%`}{(seg.ttsVolume ?? 100) === v ? ' ✓' : ''}
                  </CtxItem>
                ))}
                <CtxSep />
                {[0.9, 1, 1.15].map((v) => (
                  <CtxItem key={v} onClick={() => { editSegment({ ...seg, ttsSpeed: v }); setCtxMenu(null) }}>
                    Tốc độ TTS {v}×{(seg.ttsSpeed ?? 1) === v ? ' ✓' : ''}
                  </CtxItem>
                ))}
                <CtxSep />
                <CtxItem
                  disabled={!seg.audioUrl}
                  onClick={() => {
                    const href = seg.audioUrl
                      ? `${seg.audioUrl}${seg.audioUrl.includes('?') ? '&' : '?'}download=1`
                      : undefined
                    triggerDownload(href, `${projectId}_${seg.id}_tts.wav`)
                    setCtxMenu(null)
                  }}
                >
                  Tải audio TTS đoạn này
                </CtxItem>
                <CtxItem
                  disabled={busy || ttsBusy || !seg.translation.trim()}
                  onClick={() => { setCtxMenu(null); void previewTts(seg) }}
                >
                  Tạo lại TTS
                </CtxItem>
                <CtxItem
                  onClick={() => {
                    setCtxMenu(null)
                    removeDubClips([seg.id])
                  }}
                >
                  Tắt lồng tiếng đoạn này
                </CtxItem>
              </>
            )
          })()}

          {ctxMenu.kind === 'bg' && (
            <>
              <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
              <CtxSep />
              <CtxItem
                onClick={() => {
                  downloadProjectAudio('original')
                  setCtxMenu(null)
                }}
              >
                Tải audio gốc
              </CtxItem>
              <CtxItem
                disabled={
                  settings.processOriginalAudio
                  && settings.originalAudioMode === 'no_vocals'
                  && stemStatus === 'loading'
                }
                onClick={() => {
                  downloadProjectAudio('no_vocals')
                  setCtxMenu(null)
                }}
              >
                Tải audio đã tách lời (xóa lời)
                {stemStatus === 'loading' ? '…' : ''}
              </CtxItem>
              <CtxItem
                onClick={() => {
                  downloadProjectAudio('vocals')
                  setCtxMenu(null)
                }}
              >
                Tải audio giữ lời
              </CtxItem>
              <CtxSep />
              <CtxItem
                onClick={() => {
                  onSettings({ ...settings, processOriginalAudio: false, originalAudioMode: 'original' })
                  setCtxMenu(null)
                }}
              >
                Tắt lọc âm gốc{!settings.processOriginalAudio ? ' ✓' : ''}
              </CtxItem>
              <CtxItem
                onClick={() => {
                  onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
                  setCtxMenu(null)
                }}
              >
                Xóa lời{settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals' ? ' ✓' : ''}
              </CtxItem>
              <CtxItem
                onClick={() => {
                  onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'vocals' })
                  setCtxMenu(null)
                }}
              >
                Chỉ giữ lời{settings.processOriginalAudio && settings.originalAudioMode === 'vocals' ? ' ✓' : ''}
              </CtxItem>
              <CtxSep />
              {[0, 50, 100].map((v) => (
                <CtxItem
                  key={v}
                  onClick={() => {
                    onSettings({ ...settings, originalAudioVolume: v })
                    setCtxMenu(null)
                  }}
                >
                  Âm lượng nền {v}%{(settings.originalAudioVolume ?? 100) === v ? ' ✓' : ''}
                </CtxItem>
              ))}
              {stemStatus === 'error' && (
                <>
                  <CtxSep />
                  <CtxItem onClick={() => { setStemRetry((n) => n + 1); setCtxMenu(null) }}>Thử tách lại</CtxItem>
                </>
              )}
            </>
          )}

          {ctxMenu.kind === 'track' && (() => {
            const tid = ctxMenu.track
            const muted =
              tid === 'bg'
                ? settings.processOriginalAudio && settings.originalAudioMode === 'mute'
                : trackMute[tid]
            return (
              <>
                {tid === 'video' && (
                  <>
                    <CtxItem onClick={() => { setPropTab('video'); setCtxMenu(null) }}>Mở Video</CtxItem>
                    <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                    <CtxItem
                      onClick={() => {
                        downloadProjectAudio('original')
                        setCtxMenu(null)
                      }}
                    >
                      Tải audio gốc
                    </CtxItem>
                    <CtxSep />
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
                        setPropTab('audio')
                        setCtxMenu(null)
                      }}
                    >
                      Tách âm thanh → Xóa lời
                      {settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals' ? ' ✓' : ''}
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'vocals' })
                        setPropTab('audio')
                        setCtxMenu(null)
                      }}
                    >
                      Tách âm thanh → Chỉ giữ lời
                      {settings.processOriginalAudio && settings.originalAudioMode === 'vocals' ? ' ✓' : ''}
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: false, originalAudioMode: 'original' })
                        setCtxMenu(null)
                      }}
                    >
                      Tắt tách âm{!settings.processOriginalAudio ? ' ✓' : ''}
                    </CtxItem>
                    {stemStatus === 'error' && (
                      <CtxItem onClick={() => { setStemRetry((n) => n + 1); setCtxMenu(null) }}>Thử tách lại</CtxItem>
                    )}
                    <CtxSep />
                  </>
                )}
                {tid === 'bg' && (
                  <>
                    <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                    <CtxItem
                      onClick={() => {
                        downloadProjectAudio('original')
                        setCtxMenu(null)
                      }}
                    >
                      Tải audio gốc
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        downloadProjectAudio('no_vocals')
                        setCtxMenu(null)
                      }}
                    >
                      Tải audio đã tách lời
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        downloadProjectAudio('vocals')
                        setCtxMenu(null)
                      }}
                    >
                      Tải audio giữ lời
                    </CtxItem>
                    <CtxItem
                      onClick={() => {
                        onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'no_vocals' })
                        setCtxMenu(null)
                      }}
                    >
                      Tách âm thanh → Xóa lời
                      {settings.processOriginalAudio && settings.originalAudioMode === 'no_vocals' ? ' ✓' : ''}
                    </CtxItem>
                    <CtxSep />
                  </>
                )}
                {tid === 'caption' && (
                  <CtxItem onClick={() => { setPropTab('caption'); setCtxMenu(null) }}>Mở Phụ đề</CtxItem>
                )}
                {tid === 'dub' && (
                  <CtxItem onClick={() => { setPropTab('audio'); setCtxMenu(null) }}>Mở Âm thanh</CtxItem>
                )}
                {tid === 'text' && (
                  <CtxItem onClick={() => { setPropTab('overlay'); setCtxMenu(null) }}>Mở Text</CtxItem>
                )}
                {(tid === 'caption' || tid === 'dub' || tid === 'text') && <CtxSep />}
                {(tid === 'dub' || tid === 'bg') && (
                  <CtxItem
                    onClick={() => {
                      if (tid === 'bg') {
                        if (muted) {
                          onSettings({ ...settings, processOriginalAudio: false, originalAudioMode: 'original' })
                        } else {
                          onSettings({ ...settings, processOriginalAudio: true, originalAudioMode: 'mute' })
                        }
                      } else {
                        toggleTrackFlag(setTrackMute, tid)
                      }
                      setCtxMenu(null)
                    }}
                  >
                    {muted ? 'Bật tiếng' : 'Tắt tiếng'}
                  </CtxItem>
                )}
                <CtxItem
                  onClick={() => {
                    toggleTrackFlag(setTrackHidden, tid)
                    setCtxMenu(null)
                  }}
                >
                  {trackHidden[tid] ? 'Bỏ làm mờ track' : 'Làm mờ track'}
                </CtxItem>
                {tid !== 'bg' && (
                  <CtxItem
                    onClick={() => {
                      toggleTrackFlag(setTrackLocked, tid)
                      setCtxMenu(null)
                    }}
                  >
                    {trackLocked[tid] ? 'Mở khóa' : 'Khóa track'}
                  </CtxItem>
                )}
              </>
            )
          })()}

          {ctxMenu.kind === 'overlay' && (() => {
            const ov = overlays.find((o) => o.id === ctxMenu.overlayId)
            if (!ov) return null
            return (
              <>
                <CtxItem onClick={() => { setSelectedOverlayId(ov.id); setPropTab('overlay'); setCtxMenu(null) }}>
                  Mở Text
                </CtxItem>
                <CtxSep />
                <CtxItem
                  onClick={() => {
                    onOverlayDelete(ov.id)
                    if (selectedOverlayId === ov.id) setSelectedOverlayId(null)
                    setCtxMenu(null)
                  }}
                >
                  Xóa overlay
                </CtxItem>
              </>
            )
          })()}
        </div>,
        document.body,
      )}
      <ExportModal
        isOpen={isExportModalOpen}
        onClose={() => setIsExportModalOpen(false)}
        onConfirmExport={handleConfirmExport}
        projectTitle={projectId}
        settings={settings}
        videoCoverUrl={videoUrl}
        durationSec={duration}
      />
      <ProgressPopup
        active={speedBusy || Boolean(speedError && speedProgress > 0)}
        running={speedBusy}
        title="Đang áp dụng tốc độ video"
        message={speedMessage || `Đang áp dụng ${formatSpeedX(speedDraft)}…`}
        progress={speedProgress}
        error={speedBusy ? null : speedError}
        minimized={false}
        onMinimize={() => {}}
        onRestore={() => {}}
        onCancel={speedBusy && !speedCancelling ? () => void cancelVideoSpeed() : undefined}
      />
    </div>
  )
}
