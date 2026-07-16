"""FastAPI entry — local-only video clone API."""

from __future__ import annotations

import math
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.responses import FileResponse as StarletteFileResponse

from pipeline import (
    DATA,
    ensure_layout,
    ffprobe_duration,
    find_project_by_fp,
    hardware,
    list_voices,
    load_meta,
    mutate_meta,
    out_final,
    project_dir,
    request_cancel,
    run_dub,
    run_export,
    run_pipeline,
    save_meta,
    set_status,
    tts_cache_key,
    tts_segment,
    video_fingerprint,
)
from pipeline.core.jobs import arm_job
from pipeline.core.media import video_size
from pipeline.export.mux import separate_no_vocals, read_stem_progress


class _VideoFileResponse(StarletteFileResponse):
    """Bỏ Range vượt EOF — tránh 416 khi đổi preview↔full / ghi đè clip."""

    def __init__(self, *args: Any, force_full: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.force_full = force_full

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self.force_full:
            headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"range"]
            scope = {**scope, "headers": headers}
        await super().__call__(scope, receive, send)


def _range_start(range_header: str | None) -> int | None:
    if not range_header or not range_header.lower().startswith("bytes="):
        return None
    part = range_header.split("=", 1)[1].split(",")[0].strip()
    start_s, _, _ = part.partition("-")
    if start_s == "":
        return 0
    try:
        return int(start_s)
    except ValueError:
        return None


def _serve_video_file(path: Path, request: Request) -> StarletteFileResponse:
    if not path.is_file():
        raise HTTPException(404, detail="Không thấy video")
    st = path.stat()
    if st.st_size <= 0:
        raise HTTPException(404, detail="Video chưa sẵn sàng")
    start = _range_start(request.headers.get("range"))
    force_full = start is not None and start >= st.st_size
    return _VideoFileResponse(
        path,
        media_type="video/mp4",
        headers={
            # Project video may be replaced in-place after preview/full rebakes.
            # Never reuse a previously buffered response for the same endpoint.
            "Cache-Control": "private, no-store, max-age=0, must-revalidate",
            "Pragma": "no-cache",
            "ETag": f'"{st.st_mtime_ns:x}-{st.st_size:x}"',
        },
        force_full=force_full,
    )


app = FastAPI(title="Video-Clone Local")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Settings(BaseModel):
    workflow: str | None = None  # legacy UI — bỏ qua
    engine: str = "whisper"
    sourceLang: str = "auto"
    targetLang: str = "vi"
    # google | mymemory | tiktok | ollama | openai | gemini | deepseek | openrouter | grok
    translator: str = "google"
    matchDuration: str = "preferVideo"
    defaultVoice: str = "cc:BV075_streaming:7102355803792740865"
    coverHardsubs: bool = True
    coverMaskStyle: str = "blur"  # blur | solid | mosaic
    coverMaskColor: str = "#4c1d95"
    coverMaskOpacity: int = 40  # 0–100
    burnSubs: bool = True
    captionPlacement: str = "below"  # below | above
    subtitleFontSize: int = 0  # 0 = tự động theo bbox / độ phân giải
    processOriginalAudio: bool = False
    originalAudioMode: str = "original"
    # 0–100: volume track gốc / nền sau lọc
    originalAudioVolume: int = 100
    # 0 = full; >0 = N giây đầu để thử nhanh
    previewSec: int = 0
    # 1–16 luồng OCR/xuất/TTS; 0 = tự động theo tài nguyên rảnh
    workers: int = 0
    # Khớp LivePreviewEditor: original | 16:9 | 9:16 | …
    previewAspectRatio: str = "original"


class SegmentIn(BaseModel):
    id: str
    index: int
    start: float
    end: float
    source: str
    translation: str
    voice: str
    audioUrl: str | None = None
    audioFile: str | None = None
    audioDuration: float | None = None
    # Cửa sổ che chữ gốc (có thể rộng hơn start/end dịch)
    coverStart: float | None = None
    coverEnd: float | None = None
    # horizontal | vertical | label — UI/export burn
    layout: str | None = None
    # False = không TTS (title dọc/nhãn mặc định). True = lồng tiếng.
    dub: bool | None = None
    # Override vùng che chữ (cover box pixel), mode over = đúng khung preview.
    bbox: dict[str, float] | None = None
    # False after a manual editor move/resize; prevents inherited OCR tightening.
    bboxInherited: bool | None = None
    # Tốc độ riêng đoạn video khi xuất.
    videoSpeed: float | None = None
    ttsVolume: float | None = None
    ttsSpeed: float | None = None
    # 0 = theo dự án / tự động; 12–240 px
    fontSize: int | None = None
    # Layout caption từ preview editor — export burn y nguyên.
    captionLayout: dict[str, Any] | None = None


class ExportPayload(Settings):
    segments: list[SegmentIn] | None = None


class TextOverlayIn(BaseModel):
    id: str
    start: float
    end: float
    text: str
    x: float
    y: float
    w: float
    h: float
    fontSize: int = 42
    color: str = "#ffffff"


def _validate_segment_editor_fields(body: SegmentIn, meta: dict) -> None:
    if body.videoSpeed is not None:
        if not math.isfinite(body.videoSpeed) or not 0.5 <= body.videoSpeed <= 2.0:
            raise HTTPException(422, "videoSpeed phải nằm trong khoảng 0.5–2.0")
    if body.ttsVolume is not None and (not math.isfinite(body.ttsVolume) or not 0 <= body.ttsVolume <= 200):
        raise HTTPException(422, "ttsVolume phải nằm trong khoảng 0–200")
    if body.ttsSpeed is not None and (not math.isfinite(body.ttsSpeed) or not 0.75 <= body.ttsSpeed <= 1.5):
        raise HTTPException(422, "ttsSpeed phải nằm trong khoảng 0.75–1.5")
    if body.fontSize is not None and body.fontSize != 0 and not 12 <= body.fontSize <= 240:
        raise HTTPException(422, "fontSize phải là 0 (tự động) hoặc 12–240 px")
    if body.bbox is None:
        return
    keys = {"x", "y", "w", "h"}
    if set(body.bbox) != keys:
        raise HTTPException(422, "bbox cần đủ x, y, w, h")
    x, y, bw, bh = (float(body.bbox[key]) for key in ("x", "y", "w", "h"))
    if not all(math.isfinite(value) for value in (x, y, bw, bh)) or bw <= 0 or bh <= 0:
        raise HTTPException(422, "bbox không hợp lệ")
    width, height = video_size(Path(meta["videoPath"]))
    if x < 0 or y < 0 or x + bw > width or y + bh > height:
        raise HTTPException(422, "bbox nằm ngoài khung video")
    body.bbox = {"x": x, "y": y, "w": bw, "h": bh}


@app.get("/api/hardware")
def api_hardware():
    return hardware()


@app.get("/api/config")
def api_get_config():
    from pipeline.core.app_config import public_app_config

    return public_app_config()


class CloudBlock(BaseModel):
    apiKey: str | None = None
    baseUrl: str | None = None
    model: str | None = None


class ElevenLabsBlock(BaseModel):
    apiKeys: str | None = None


class TtsBlock(BaseModel):
    elevenlabs: ElevenLabsBlock | None = None


class AppConfigIn(BaseModel):
    cloud: dict[str, CloudBlock] | None = None
    tts: TtsBlock | None = None


@app.post("/api/config")
def api_save_config(body: AppConfigIn):
    from pipeline.core.app_config import public_app_config, save_app_config

    patch: dict = {"cloud": {}}
    if body.cloud:
        for k, v in body.cloud.items():
            patch["cloud"][k] = {
                "apiKey": v.apiKey if v.apiKey is not None else "",
                "baseUrl": v.baseUrl or "",
                "model": v.model or "",
            }
    if body.tts and body.tts.elevenlabs is not None:
        patch["tts"] = {
            "elevenlabs": {
                "apiKeys": body.tts.elevenlabs.apiKeys
                if body.tts.elevenlabs.apiKeys is not None
                else "",
            }
        }
    save_app_config(patch)
    return public_app_config()


@app.get("/api/voices")
def api_voices(lang: str = "vi"):
    return list_voices(lang)


class PreviewTtsIn(BaseModel):
    text: str
    voice: str = "el:pNInz6obpgDQGcFmaJgB"
    lang: str = "vi"


class RebakeSpeedIn(BaseModel):
    speed: float = 1.0


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "Thiếu file")
    ext = Path(file.filename).suffix or ".mp4"
    tmp = DATA / f"_upload_{uuid.uuid4().hex}{ext}"
    DATA.mkdir(exist_ok=True)
    with tmp.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    fp = video_fingerprint(tmp)
    existing = find_project_by_fp(fp)
    if existing:
        tmp.unlink(missing_ok=True)
        meta = load_meta(existing)
        ensure_layout(existing)
        set_status(
            existing,
            step=meta.get("status", {}).get("step") or "video",
            progress=100,
            message="Video đã có sẵn (cache)",
            running=False,
            error=None,
        )
        return {
            "projectId": existing,
            "videoUrl": f"/api/projects/{existing}/video",
            "duration": meta.get("duration") or ffprobe_duration(Path(meta["videoPath"])),
            "cached": True,
            "segments": meta.get("segments") or [],
            "settings": meta.get("settings") or {},
        }

    project_id = uuid.uuid4().hex[:12]
    root = ensure_layout(project_id)
    dest = root / f"source{ext}"
    tmp.replace(dest)
    duration = ffprobe_duration(dest)
    save_meta(
        project_id,
        {
            "videoPath": str(dest),
            "duration": duration,
            "sourceFp": fp,
            "segments": [],
            "cache": {},
            "settings": Settings().model_dump(),
            "status": {
                "step": "video",
                "progress": 100,
                "message": "Video sẵn sàng",
                "running": False,
            },
        },
    )
    return {
        "projectId": project_id,
        "videoUrl": f"/api/projects/{project_id}/video",
        "duration": duration,
        "cached": False,
        "segments": [],
    }


@app.get("/api/projects/{project_id}/video")
def api_video(project_id: str, request: Request):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    from pipeline.core.project import resolve_project_video

    return _serve_video_file(resolve_project_video(meta, project_id), request)


@app.post("/api/projects/{project_id}/rebake-speed")
def api_rebake_speed(project_id: str, body: RebakeSpeedIn):
    """Bake tốc độ preview từ file 1× + remap timeline theo tốc độ mới."""
    from pipeline.core.media import (
        clamp_playback_speed,
        ensure_playback_speed,
        meta_baked_speed,
        preview_1x_path,
        remap_timeline_for_speed_change,
        speed_cache_tag,
    )

    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    if (meta.get("status") or {}).get("running"):
        raise HTTPException(409, "Đang có job chạy — đợi xong rồi áp dụng tốc độ")

    speed = clamp_playback_speed(body.speed)
    old = meta_baked_speed(meta)
    remap_timeline_for_speed_change(meta, old, speed)

    base = preview_1x_path(project_id, meta)
    if not base.is_file():
        raise HTTPException(404, "Chưa có preview 1× — chạy Dịch trước")

    cache = ensure_layout(project_id) / "cache"
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    try:
        if abs(speed - 1.0) < 0.001:
            work = base
            meta.pop("bakedPreferVideo", None)
            meta["bakedSpeed"] = 1.0
            meta.pop("workDuration", None)
        else:
            set_status(
                project_id,
                step="video",
                progress=5,
                message=f"Bake tốc độ {speed:.2f}×…",
                running=True,
                error=None,
            )
            tag = speed_cache_tag(speed)
            dest = (
                cache / f"preview_{preview_sec}_{tag}.mp4"
                if preview_sec > 0
                else cache / f"source_{tag}.mp4"
            )
            work = ensure_playback_speed(base, dest, speed, project_id=project_id)
            meta["bakedPreferVideo"] = True
            meta["bakedSpeed"] = speed
            meta["workDuration"] = float(ffprobe_duration(work) or 0)
        meta["workVideo"] = str(work.resolve())
        save_meta(project_id, meta)
    except Exception as e:
        set_status(
            project_id,
            step="video",
            progress=0,
            message="Bake tốc độ thất bại",
            running=False,
            error=str(e)[:500],
        )
        raise HTTPException(500, f"Bake tốc độ thất bại: {e}") from e

    speed_baked = abs(speed - 1.0) > 0.001
    work_clip = float(meta["workDuration"]) if speed_baked else float(preview_sec)
    duration = float(meta.get("workDuration") or ffprobe_duration(work) or meta.get("duration") or 0)
    set_status(
        project_id,
        step="video",
        progress=100,
        message=f"Preview {speed:.2f}× sẵn sàng",
        running=False,
        error=None,
    )
    return {
        "ok": True,
        "bakedSpeed": speed,
        "bakedPreferVideo": speed_baked,
        "workClipSec": work_clip,
        "duration": duration,
        "segments": meta.get("segments") or [],
        "overlays": meta.get("overlays") or [],
        "videoUrl": f"/api/projects/{project_id}/video",
    }


@app.get("/api/projects/{project_id}/status")
def api_status(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    st = dict(meta.get("status") or {"step": "video", "progress": 0, "message": "", "running": False})
    # UI restore session sau F5 / HMR
    st["duration"] = float(meta.get("duration") or 0)
    # Độ dài clip lần dịch gần nhất (0 = full) — editor/xuất làm việc trong cửa sổ này
    from pipeline.core.media import meta_baked_speed

    baked_speed = meta_baked_speed(meta)
    speed_baked = abs(baked_speed - 1.0) > 0.001
    if speed_baked and float(meta.get("workDuration") or 0) > 0:
        st["workClipSec"] = float(meta["workDuration"])
        st["duration"] = float(meta["workDuration"])
    else:
        st["workClipSec"] = max(0, int(meta.get("previewSec") or 0))
    st["bakedPreferVideo"] = speed_baked or bool(meta.get("bakedPreferVideo"))
    st["bakedSpeed"] = baked_speed
    if meta.get("settings"):
        st["settings"] = meta["settings"]
    if meta.get("outputRel"):
        st["outputRel"] = st.get("outputRel") or meta.get("outputRel")
    return st


@app.get("/api/projects/{project_id}/segments")
def api_segments(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    return meta.get("segments") or []


def _validate_overlay(body: TextOverlayIn, meta: dict) -> None:
    width, height = video_size(Path(meta["videoPath"]))
    values = (body.start, body.end, body.x, body.y, body.w, body.h)
    if not all(math.isfinite(value) for value in values) or body.end <= body.start:
        raise HTTPException(422, "Thời gian text không hợp lệ")
    if body.x < 0 or body.y < 0 or body.w <= 0 or body.h <= 0 or body.x + body.w > width or body.y + body.h > height:
        raise HTTPException(422, "Text nằm ngoài khung video")
    if not 12 <= body.fontSize <= 240:
        raise HTTPException(422, "Cỡ chữ phải nằm trong khoảng 12–240")


@app.get("/api/projects/{project_id}/overlays")
def api_overlays(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    return meta.get("overlays") or []


@app.post("/api/projects/{project_id}/overlays")
def api_create_overlay(project_id: str, body: TextOverlayIn):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    _validate_overlay(body, meta)
    overlays = meta.get("overlays") or []
    if any(item.get("id") == body.id for item in overlays):
        raise HTTPException(409, "Text overlay đã tồn tại")
    item = body.model_dump()
    overlays.append(item)
    meta["overlays"] = overlays
    save_meta(project_id, meta)
    return item


@app.put("/api/projects/{project_id}/overlays")
def api_replace_overlays(project_id: str, body: list[TextOverlayIn]):
    """Thay cả list overlay (undo/redo)."""

    def apply(meta: dict) -> list[dict]:
        if not meta:
            raise HTTPException(404)
        for item in body:
            _validate_overlay(item, meta)
        out = [item.model_dump() for item in body]
        meta["overlays"] = out
        return out

    return mutate_meta(project_id, apply)


@app.put("/api/projects/{project_id}/overlays/{overlay_id}")
def api_update_overlay(project_id: str, overlay_id: str, body: TextOverlayIn):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    _validate_overlay(body, meta)
    overlays = meta.get("overlays") or []
    for i, item in enumerate(overlays):
        if item.get("id") == overlay_id:
            overlays[i] = body.model_dump()
            meta["overlays"] = overlays
            save_meta(project_id, meta)
            return overlays[i]
    raise HTTPException(404, "Text overlay không tồn tại")


@app.delete("/api/projects/{project_id}/overlays/{overlay_id}")
def api_delete_overlay(project_id: str, overlay_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    overlays = [item for item in (meta.get("overlays") or []) if item.get("id") != overlay_id]
    meta["overlays"] = overlays
    save_meta(project_id, meta)
    return {"ok": True}


@app.put("/api/projects/{project_id}/segments/{seg_id}")
def api_update_segment(project_id: str, seg_id: str, body: SegmentIn):
    def apply(meta: dict) -> dict:
        if not meta:
            raise HTTPException(404)
        _validate_segment_editor_fields(body, meta)
        segs = meta.get("segments") or []
        for i, s in enumerate(segs):
            if s["id"] == seg_id:
                incoming = body.model_dump()
                merged = {**s, **{k: v for k, v in incoming.items() if v is not None}}
                if "bbox" in body.model_fields_set and body.bbox is None:
                    merged.pop("bbox", None)
                if "captionLayout" in body.model_fields_set and body.captionLayout is None:
                    merged.pop("captionLayout", None)
                if incoming.get("layout") is None and s.get("layout"):
                    merged["layout"] = s["layout"]
                if incoming.get("dub") is None and "dub" in s:
                    merged["dub"] = s["dub"]
                segs[i] = merged
                meta["segments"] = segs
                return merged
        raise HTTPException(404, "Segment not found")

    return mutate_meta(project_id, apply)


@app.put("/api/projects/{project_id}/segments")
def api_replace_segments(project_id: str, body: list[SegmentIn]):
    """Thay cả list segment (split / duplicate / delete từ editor)."""

    def apply(meta: dict) -> list[dict]:
        if not meta:
            raise HTTPException(404)
        for item in body:
            _validate_segment_editor_fields(item, meta)
            if not math.isfinite(item.start) or not math.isfinite(item.end) or item.end <= item.start:
                raise HTTPException(422, "Thời gian segment không hợp lệ")
        ordered = sorted(body, key=lambda s: (s.start, s.end, s.id))
        out: list[dict] = []
        for i, item in enumerate(ordered):
            dumped = item.model_dump()
            dumped["index"] = i
            out.append(dumped)
        meta["segments"] = out
        return out

    return mutate_meta(project_id, apply)


def _spawn(fn, *args):
    def wrap():
        try:
            fn(*args)
        except Exception:
            pass  # status already set in pipeline

    threading.Thread(target=wrap, daemon=True).start()


@app.post("/api/projects/{project_id}/run")
def api_run(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    # Lưu ngay (giống dub) — tránh mở editor / restore session mất processOriginalAudio
    meta["settings"] = settings.model_dump()
    save_meta(project_id, meta)
    arm_job(project_id)
    set_status(project_id, step="asr", progress=1, message="Queued…", running=True, error=None)
    _spawn(run_pipeline, project_id, settings.model_dump())
    return {"ok": True}


@app.post("/api/projects/{project_id}/dub")
def api_dub(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    old_default = (meta.get("settings") or {}).get("defaultVoice") or ""
    meta["settings"] = settings.model_dump()
    default = settings.defaultVoice
    segs = meta.get("segments") or []
    uniq = {(s.get("voice") or "").strip() for s in segs}
    uniq.discard("")
    uniq.discard("system")
    # đồng bộ default: inherit cũ, hoặc cả loạt cùng 1 giọng ≠ default (vd. Adam còn sót)
    batch = len(uniq) <= 1 and (not uniq or default not in uniq)
    for seg in segs:
        v = (seg.get("voice") or "").strip()
        if batch or not v or v == "system" or v == old_default:
            seg["voice"] = default
    save_meta(project_id, meta)
    arm_job(project_id)
    set_status(project_id, step="dub", progress=1, message="Queued…", running=True, error=None)
    _spawn(run_dub, project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/cancel")
def api_cancel(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    st = meta.get("status") or {}
    cur_step = str(st.get("step") or "video")
    # Luôn set cancel flag (kể cả Queued trước begin_job)
    request_cancel(project_id)
    if not st.get("running"):
        # UI có thể đang optimistic running — vẫn ghi cancelled
        set_status(
            project_id,
            step=cur_step if cur_step in ("asr", "translate", "dub", "export") else "video",
            progress=0,
            message="Đã huỷ",
            running=False,
            error="cancelled",
        )
        return {"ok": True}
    msg = {
        "dub": "Đã huỷ lồng tiếng",
        "export": "Đã huỷ xuất bản",
        "asr": "Đã huỷ",
        "translate": "Đã huỷ",
    }.get(cur_step, "Đã huỷ")
    set_status(
        project_id,
        step=cur_step if cur_step in ("asr", "translate", "dub", "export") else "video",
        progress=0,
        message=msg,
        running=False,
        error="cancelled",
    )
    return {"ok": True}


@app.post("/api/projects/{project_id}/settings")
def api_save_settings(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    old_default = (meta.get("settings") or {}).get("defaultVoice") or ""
    meta["settings"] = settings.model_dump()
    new_default = settings.defaultVoice or ""
    # đổi giọng mặc định → đồng bộ đoạn đang inherit (system / default cũ)
    if new_default and new_default != old_default:
        for seg in meta.get("segments") or []:
            seg["voice"] = new_default
    save_meta(project_id, meta)
    return {"ok": True, "settings": meta["settings"]}


@app.post("/api/projects/{project_id}/export")
def api_export(project_id: str, payload: ExportPayload):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    # UI checkbox phải thắng meta cũ; previewSec ô Preview ≠ độ dài lần dịch
    dumped = payload.model_dump(exclude={"segments"}, exclude_none=False)
    if payload.segments is not None:
        # List từ editor = source of truth (không merge giữ meta cũ → lệch WYSIWYG)
        ordered = sorted(payload.segments, key=lambda s: (s.start, s.end, s.id))
        out: list[dict] = []
        for i, item in enumerate(ordered):
            d = item.model_dump(exclude_none=False)
            if d.get("bbox") is None:
                d.pop("bbox", None)
            if d.get("captionLayout") is None:
                d.pop("captionLayout", None)
            d["index"] = i
            out.append(d)
        meta["segments"] = out
    run_preview = max(0, int(meta.get("previewSec") or 0))
    ui_prev = max(0, int(dumped.get("previewSec") or 0))
    if ui_prev <= 0:
        ui_prev = max(0, int((meta.get("settings") or {}).get("previewSec") or 0)) or 20
    dumped["previewSec"] = ui_prev
    # Settings editor thắng hoàn toàn (mask/font/cover/burn…)
    meta["settings"] = dumped
    # xuất theo clip lần dịch gần nhất (0 = full), không theo ô Preview
    meta["previewSec"] = run_preview
    save_meta(project_id, meta)
    hint = f"preview {run_preview}s" if run_preview > 0 else "full"
    arm_job(project_id)
    set_status(
        project_id,
        step="export",
        progress=1,
        message=f"Queued ({hint})…",
        running=True,
        error=None,
    )
    _spawn(run_export, project_id)
    return {
        "ok": True,
        "url": f"/api/projects/{project_id}/output",
        "path": f"server/data/{project_id}/out/final.mp4",
        "exports": f"server/data/exports/{project_id}.mp4",
    }


@app.get("/api/projects/{project_id}/output")
def api_output(project_id: str, download: bool = False):
    path = out_final(project_id)
    if not path.exists():
        legacy = project_dir(project_id) / "output.mp4"
        easy = DATA / "exports" / f"{project_id}.mp4"
        if legacy.exists():
            path = legacy
        elif easy.exists():
            path = easy
        else:
            raise HTTPException(404)
    name = f"video-clone-{project_id}.mp4"
    # download=1 → attachment; mặc định inline để trình duyệt phát được
    if download:
        return FileResponse(path, filename=name, media_type="video/mp4")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@app.post("/api/projects/{project_id}/reveal-output")
def api_reveal_output(project_id: str):
    """Mở Finder/Explorer tại file đã xuất (local app)."""
    import platform
    import subprocess

    path = out_final(project_id)
    if not path.exists():
        easy = DATA / "exports" / f"{project_id}.mp4"
        path = easy if easy.exists() else path
    if not path.exists():
        raise HTTPException(404, "Chưa có file xuất")
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as e:
        raise HTTPException(500, str(e)) from e
    return {"ok": True, "path": str(path.resolve())}


@app.post("/api/projects/{project_id}/segments/{seg_id}/preview-tts")
def api_preview_tts(project_id: str, seg_id: str, body: PreviewTtsIn):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "Thiếu nội dung để đọc")
    settings = meta.get("settings") or {}
    lang = body.lang or settings.get("targetLang") or "vi"
    root = ensure_layout(project_id)
    key = tts_cache_key(text, body.voice or "system", lang, "none")
    name = f"{key}.wav"
    wav = root / "tts" / name
    try:
        if wav.exists():
            dur = ffprobe_duration(wav)
        else:
            dur = tts_segment(text, body.voice or "system", wav, None, "none", lang=lang)
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    return {
        "audioUrl": f"/api/projects/{project_id}/tts/{name}?t={int(dur * 1000)}",
        "duration": dur,
    }


class RetranslateIn(BaseModel):
    text: str = ""
    sourceLang: str | None = None
    targetLang: str | None = None
    translator: str | None = None


@app.post("/api/projects/{project_id}/segments/{seg_id}/retranslate")
def api_retranslate(project_id: str, seg_id: str, body: RetranslateIn):
    """Dịch lại 1 đoạn (không chạy pipeline full)."""
    from pipeline.translate import translate_segments

    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    segs = meta.get("segments") or []
    seg = next((s for s in segs if s.get("id") == seg_id), None)
    if not seg:
        raise HTTPException(404, "Segment not found")
    settings = meta.get("settings") or {}
    source = (body.text or seg.get("source") or "").strip()
    if not source:
        raise HTTPException(400, "Thiếu chữ nguồn")
    target = body.targetLang or settings.get("targetLang") or "vi"
    if target in ("none", "off", "source", ""):
        # Giữ nguyên chữ nguồn — không gọi máy dịch
        tr = source
        seg["translation"] = tr
        seg.pop("audioFile", None)
        seg.pop("audioUrl", None)
        seg.pop("audioDuration", None)
        meta["segments"] = segs
        save_meta(project_id, meta)
        return {"translation": tr, "segment": seg}
    src_lang = body.sourceLang or settings.get("sourceLang") or "auto"
    eng = body.translator or settings.get("translator") or "google"
    try:
        out = translate_segments(
            [source],
            target,
            project_id=None,
            source_lang=src_lang,
            translator=str(eng),
            workers=1,
        )
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    tr = (out[0] if out else "").strip() or source
    seg["translation"] = tr
    # invalidate TTS cache fields — user nghe lại sẽ gen mới
    seg.pop("audioFile", None)
    seg.pop("audioUrl", None)
    seg.pop("audioDuration", None)
    meta["segments"] = segs
    save_meta(project_id, meta)
    return {"translation": tr, "segment": seg}


@app.get("/api/projects/{project_id}/tts/{name}")
def api_tts(project_id: str, name: str):
    path = ensure_layout(project_id) / "tts" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/projects/{project_id}/audio/no-vocals")
def api_prepare_no_vocals(project_id: str):
    """Tách stem xóa lời (Demucs) — cache dùng chung preview + export."""
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    video = Path(meta["videoPath"])
    if not video.is_file():
        raise HTTPException(404, "Thiếu video nguồn")
    try:
        path = separate_no_vocals(project_id, video, report=False)
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    name = path.name
    return {"audioUrl": f"/api/projects/{project_id}/cache/{name}", "file": name}


@app.get("/api/projects/{project_id}/audio/no-vocals/progress")
def api_no_vocals_progress(project_id: str):
    """Tiến độ tách stem — poll song song lúc POST /audio/no-vocals đang chạy."""
    if not load_meta(project_id):
        raise HTTPException(404)
    return read_stem_progress(project_id)


@app.get("/api/projects/{project_id}/cache/{name}")
def api_cache_file(project_id: str, name: str):
    if not re.fullmatch(r"no_vocals_[a-f0-9]+\.wav", name):
        raise HTTPException(400, "Tên file không hợp lệ")
    path = ensure_layout(project_id) / "cache" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/health")
def health():
    return {"ok": True, "data": str(DATA)}


@app.get("/api/system/checks")
def api_system_checks():
    """Dependency checklist cho tab Thiết lập / first-run."""
    from pipeline.core.system_check import system_checks

    return system_checks()


@app.post("/api/system/install/ocr_cuda")
def api_install_ocr_cuda():
    """Install ONNX Runtime GPU into the backend's Python environment."""
    from pipeline.core.system_check import install_ocr_cuda

    try:
        return install_ocr_cuda()
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@app.post("/api/system/install/demucs_cuda")
def api_install_demucs_cuda():
    """Cài Demucs + PyTorch CUDA (server/.venv-demucs) — tách lời nhanh."""
    from pipeline.core.system_check import install_demucs_cuda

    try:
        return install_demucs_cuda()
    except Exception as e:
        raise HTTPException(500, str(e)) from e
