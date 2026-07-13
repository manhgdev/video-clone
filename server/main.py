"""FastAPI entry — local-only video clone API."""

from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pipeline import (
    DATA,
    ensure_layout,
    ffprobe_duration,
    find_project_by_fp,
    hardware,
    list_voices,
    load_meta,
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
    matchDuration: str = "natural"
    defaultVoice: str = "cc:BV075_streaming:7102355803792740865"
    coverHardsubs: bool = True
    burnSubs: bool = True
    captionPlacement: str = "below"  # below | above
    subtitleFontSize: int = 32
    processOriginalAudio: bool = False
    originalAudioMode: str = "original"
    # 0–100: volume track gốc / nền sau lọc
    originalAudioVolume: int = 100
    # 0 = full; >0 = N giây đầu để thử nhanh
    previewSec: int = 0
    # 1–16 luồng OCR/xuất/TTS
    workers: int = 2


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
    # horizontal | vertical | label — UI/export burn
    layout: str | None = None
    # False = không TTS (title dọc/nhãn mặc định). True = lồng tiếng.
    dub: bool | None = None


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
def api_video(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    return FileResponse(meta["videoPath"])


@app.get("/api/projects/{project_id}/status")
def api_status(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    st = dict(meta.get("status") or {"step": "video", "progress": 0, "message": "", "running": False})
    # UI restore session sau F5 / HMR
    st["duration"] = float(meta.get("duration") or 0)
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


@app.put("/api/projects/{project_id}/segments/{seg_id}")
def api_update_segment(project_id: str, seg_id: str, body: SegmentIn):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    segs = meta.get("segments") or []
    for i, s in enumerate(segs):
        if s["id"] == seg_id:
            # merge: giữ layout/dub/các field cũ nếu client không gửi
            incoming = body.model_dump()
            merged = {**s, **{k: v for k, v in incoming.items() if v is not None}}
            if incoming.get("layout") is None and s.get("layout"):
                merged["layout"] = s["layout"]
            if incoming.get("dub") is None and "dub" in s:
                merged["dub"] = s["dub"]
            segs[i] = merged
            meta["segments"] = segs
            save_meta(project_id, meta)
            return segs[i]
    raise HTTPException(404, "Segment not found")


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
def api_export(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    # UI checkbox phải thắng meta cũ; previewSec ô Preview ≠ độ dài lần dịch
    dumped = settings.model_dump()
    run_preview = max(0, int(meta.get("previewSec") or 0))
    ui_prev = max(0, int(dumped.get("previewSec") or 0))
    if ui_prev <= 0:
        ui_prev = max(0, int((meta.get("settings") or {}).get("previewSec") or 0)) or 20
    dumped["previewSec"] = ui_prev
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
        raise HTTPException(400, "Đang chọn Không dịch")
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


@app.get("/api/health")
def health():
    return {"ok": True, "data": str(DATA)}
