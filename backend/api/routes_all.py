"""HTTP route handlers — bound to FastAPI app via register(app)."""
from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.responses import FileResponse as StarletteFileResponse

from pipeline import (
    DATA,
    PUBLIC_DATA,
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
from pipeline.export.mux import read_stem_progress, separate_no_vocals
from pipeline.tts import engines_status
from pipeline.tts import voice_store
from pipeline.tts.engines import vieneu as vieneu_engine
from pipeline.tts.voice_store import TTS_OUTPUT, ensure_vieneu_dirs

from fastapi import APIRouter
router = APIRouter()

class _VideoFileResponse(StarletteFileResponse):
    """Bỏ Range vượt EOF — tránh 416 khi đổi preview↔full / ghi đè clip.

    Nuốt CancelledError / disconnect client — tránh log ASGI + WinError 10055
    khi UI abort hàng loạt Range request (poll / đổi URL / pause).
    """

    def __init__(self, *args: Any, force_full: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.force_full = force_full

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self.force_full:
            headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"range"]
            scope = {**scope, "headers": headers}
        try:
            await super().__call__(scope, receive, send)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError, OSError):
            # Client đóng socket giữa chừng (tua / đổi src / reload) — không crash worker
            return


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
            # ETag đổi khi file ghi đè; short cache giảm storm Range trên Windows
            # (no-store + tua liên tục → cạn socket WinError 10055).
            "Cache-Control": "private, max-age=2, must-revalidate",
            "ETag": f'"{st.st_mtime_ns:x}-{st.st_size:x}"',
            "Accept-Ranges": "bytes",
        },
        force_full=force_full,
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
    subtitleFontFamily: str = "system"  # system | bold | rounded | mono
    captionTextColor: str = "#ffffff"
    captionBgStyle: str = "none"  # none | solid | blur | box
    captionBgColor: str = "#000000"
    captionBgOpacity: int = 55  # 0–100
    captionStroke: bool = True
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
    # true = xóa file TTS cache + gen lại (nút Lồng tiếng trong editor)
    forceTts: bool = False


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
    text: str = ""
    x: float
    y: float
    w: float
    h: float
    fontSize: int = 42
    color: str = "#ffffff"
    # text | effect — effect = vùng làm mờ/màu/khối tự do
    kind: str | None = "text"
    maskStyle: str | None = None  # blur | solid | mosaic
    maskColor: str | None = None
    maskOpacity: int | None = None


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


@router.get("/api/hardware")
def api_hardware():
    return hardware()


@router.get("/api/config")
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


@router.post("/api/config")
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


@router.get("/api/voices")
def api_voices(lang: str = "vi"):
    return list_voices(lang)


@router.get("/api/tts/voices/{voice_id}/preview")
def api_tts_voice_preview(voice_id: str):
    path = vieneu_engine.preview_path(voice_id)
    if not path:
        raise HTTPException(404, "Giọng này không có audio mẫu")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/api/tts/status")
def api_tts_status():
    """TTS Studio — engine status (VieNeu / CapCut / EL / system)."""
    try:
        return engines_status()
    except Exception as e:
        raise HTTPException(500, str(e)) from e


class StudioSynthIn(BaseModel):
    text: str = ""
    srtText: str = ""
    voice: str = "system"
    speaker_id: str = ""
    lang: str = "vi"
    speed: float = 1.0
    volume: float = 1.0
    pitch: float = 0.0
    style: str = "tu_nhien"
    matchDuration: str = "none"
    keepTimeline: bool = True
    autoSplit: bool = True
    gapMs: int = 0
    title: str = ""


@router.post("/api/tts/studio/synthesize")
def api_tts_studio_synth(body: StudioSynthIn):
    """TTS Studio: text or SRT batch → data/tts_output/{jobId}/."""
    from pipeline.tts.studio import synth_srt_job, synth_text_job

    srt_text = (body.srtText or "").strip()
    text = (body.text or "").strip()
    if not srt_text and not text:
        raise HTTPException(400, "Thiếu nội dung hoặc SRT")
    selected_voice = body.speaker_id or body.voice or "system"
    try:
        if srt_text:
            return synth_srt_job(
                srt_text=srt_text,
                voice=selected_voice,
                lang=body.lang or "vi",
                speed=float(body.speed or 1.0),
                volume=float(body.volume or 1.0),
                pitch=float(body.pitch or 0.0),
                style=body.style or "tu_nhien",
                match_duration=body.matchDuration or "natural",
                keep_timeline=bool(body.keepTimeline),
                title=body.title or "",
                gap_ms=int(body.gapMs or 0),
            )
        return synth_text_job(
            text=text,
            voice=selected_voice,
            lang=body.lang or "vi",
            speed=float(body.speed or 1.0),
            volume=float(body.volume or 1.0),
            pitch=float(body.pitch or 0.0),
            style=body.style or "tu_nhien",
            match_duration=body.matchDuration or "none",
            title=body.title or "",
            auto_split=bool(body.autoSplit),
            gap_ms=int(body.gapMs or 0),
        )
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.get("/api/tts/studio/history")
def api_tts_studio_history():
    from pipeline.tts.studio import list_history

    return list_history(50)


@router.get("/api/tts/studio/jobs/{job_id}/audio.wav")
def api_tts_studio_audio(job_id: str, download: int = 0):
    """Phát inline (mặc định). ?download=1 → tải file."""
    path = TTS_OUTPUT / job_id / "audio.wav"
    if not path.is_file():
        raise HTTPException(404, "Không thấy audio")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{job_id}.wav",
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/api/tts/studio/jobs/{job_id}/audio.mp3")
def api_tts_studio_mp3(job_id: str, download: int = 0):
    from pipeline.tts.studio import ensure_mp3

    try:
        path = ensure_mp3(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Không thấy audio") from None
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=f"{job_id}.mp3",
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/api/tts/studio/jobs/{job_id}/subs.srt")
def api_tts_studio_srt(job_id: str, style: str = "hard"):
    from pipeline.export.srt import SRT_STYLES
    from pipeline.tts.studio import rebuild_srt

    if style not in SRT_STYLES:
        raise HTTPException(400, f"style phải là một trong: {', '.join(SRT_STYLES)}")
    if style == "hard":
        path = TTS_OUTPUT / job_id / "subs.srt"
        if not path.is_file():
            try:
                from pipeline.tts.studio import ensure_zip
                ensure_zip(job_id)
            except Exception:
                pass
            path = TTS_OUTPUT / job_id / "subs.srt"
    else:
        try:
            path = rebuild_srt(job_id, style)
        except FileNotFoundError:
            raise HTTPException(404, "Không thấy job") from None
    if not path.is_file():
        raise HTTPException(404, "Không thấy SRT")
    return FileResponse(path, media_type="application/x-subrip", filename=f"{job_id}.srt")


@router.get("/api/tts/studio/jobs/{job_id}/bundle.zip")
def api_tts_studio_zip(job_id: str, style: str = "hard"):
    from pipeline.export.srt import SRT_STYLES
    from pipeline.tts.studio import ensure_zip

    if style not in SRT_STYLES:
        raise HTTPException(400, f"style phải là một trong: {', '.join(SRT_STYLES)}")
    try:
        path = ensure_zip(job_id, srt_style=style)
    except FileNotFoundError:
        raise HTTPException(404, "Không thấy job") from None
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    return FileResponse(path, media_type="application/zip", filename=f"{job_id}.zip")


@router.post("/api/tts/studio/jobs/{job_id}/cancel")
def api_tts_studio_cancel(job_id: str):
    from pipeline.tts.studio import request_cancel

    ok = request_cancel(job_id)
    return {"ok": True, "cancelled": ok}


@router.delete("/api/tts/studio/jobs/{job_id}")
def api_tts_studio_delete(job_id: str):
    path = TTS_OUTPUT / job_id
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    return {"ok": True}


@router.post("/api/tts/studio/clone")
async def api_tts_studio_clone(
    name: str = "",
    transcript: str = "",
    tags: str = "[]",
    file: UploadFile = File(...),
):
    """Clone giọng VieNeu từ file ref (3–8s)."""
    if not name.strip():
        raise HTTPException(400, "Thiếu tên giọng")
    try:
        parsed_tags = json.loads(tags)
        clean_tags = voice_store.normalize_voice_tags(parsed_tags, strict=True)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    ensure_vieneu_dirs()
    ext = Path(file.filename or "ref.wav").suffix or ".wav"
    tmp = DATA / f"_clone_{uuid.uuid4().hex}{ext}"
    try:
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        voice = vieneu_engine.clone_voice(
            name.strip(),
            tmp,
            transcript=(transcript or "").strip(),
            tags=clean_tags,
        )
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)
    return voice


class CloneRenameIn(BaseModel):
    name: str = ""


class VoicePatchIn(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    # zmai | clone — chuyển bucket (chỉ local VieNeu ref)
    engine: str | None = None


class VoiceBulkMoveIn(BaseModel):
    voiceIds: list[str]
    target: str


def _studio_voice_payload(entry: dict) -> dict:
    eid = str(entry.get("id") or "")
    name = str(entry.get("name") or eid)
    eng = str(entry.get("engine") or "")
    tags = voice_store.normalize_voice_tags(entry.get("tags"))
    if eng == "clone" or eid.startswith("vn:clone:"):
        cid = eid.removeprefix("vn:clone:")
        return {
            "id": f"vn:clone:{cid}",
            "name": f"VieNeu · Clone · {name}",
            "engine": "clone",
            "tags": tags,
        }
    return {"id": eid, "name": name, "engine": "zmai", "type": "zmAI", "tags": tags}


@router.post("/api/tts/studio/voices/bulk-move")
def api_tts_studio_voices_bulk_move(body: VoiceBulkMoveIn):
    """Chuyển nhiều giọng zmAI ↔ clone trong một request."""
    from pipeline.tts import voice_store

    target = (body.target or "").strip().lower()
    if target not in ("zmai", "clone"):
        raise HTTPException(400, "target phải là 'zmai' hoặc 'clone'")
    if not body.voiceIds:
        raise HTTPException(400, "Cần ít nhất một voiceId")
    if len(body.voiceIds) > 200:
        raise HTTPException(400, "Tối đa 200 voiceId mỗi lần")

    voice_ids = [voice_id.strip() for voice_id in body.voiceIds]
    if any(not voice_id for voice_id in voice_ids):
        raise HTTPException(400, "voiceId không được trống")
    # Không chạy cùng một phép chuyển hai lần nếu client gửi ID trùng.
    voice_ids = list(dict.fromkeys(voice_ids))
    result = voice_store.move_voice_engines(voice_ids, target)
    return {
        "target": target,
        "successes": [
            {"voiceId": item["voiceId"], "voice": _studio_voice_payload(item["voice"])}
            for item in result["successes"]
        ],
        "failures": result["failures"],
    }


@router.patch("/api/tts/studio/clone/{voice_id}")
def api_tts_studio_clone_rename(voice_id: str, body: CloneRenameIn):
    from pipeline.tts import voice_store

    cid = voice_id.removeprefix("vn:clone:").strip()
    if not cid:
        raise HTTPException(400, "Thiếu id giọng")
    entry = voice_store.rename_cloned(cid, (body.name or "").strip())
    if not entry:
        raise HTTPException(404, "Không tìm thấy giọng clone")
    return {
        "id": f"vn:clone:{entry['id']}",
        "name": f"VieNeu · Clone · {entry['name']}",
    }


@router.delete("/api/tts/studio/clone/{voice_id}")
def api_tts_studio_clone_delete(voice_id: str):
    from pipeline.tts import voice_store

    cid = voice_id.removeprefix("vn:clone:").strip()
    if not cid:
        raise HTTPException(400, "Thiếu id giọng")
    if not voice_store.remove_cloned(cid):
        raise HTTPException(404, "Không tìm thấy giọng clone")
    return {"ok": True}


@router.patch("/api/tts/studio/voices/{voice_id:path}")
def api_tts_studio_voice_patch(voice_id: str, body: VoicePatchIn):
    """Đổi tên và/hoặc chuyển engine (zmAI ↔ clone)."""
    from pipeline.tts import voice_store

    vid = (voice_id or "").strip()
    if not vid:
        raise HTTPException(400, "Thiếu id giọng")

    name = (body.name or "").strip() if body.name is not None else None
    engine = (body.engine or "").strip().lower() if body.engine is not None else None
    tags_supplied = "tags" in body.model_fields_set
    try:
        tags = voice_store.normalize_voice_tags(body.tags, strict=True) if tags_supplied else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if name is None and not engine and not tags_supplied:
        raise HTTPException(400, "Cần name, tags và/hoặc engine")
    if body.name is not None and not name:
        raise HTTPException(400, "Tên giọng không được trống")

    try:
        if engine:
            entry = voice_store.move_voice_engine(vid, engine)
            # Metadata update after moving because the id may change.
            new_id = str(entry["id"])
            if name is not None or tags_supplied:
                if new_id.startswith("vn:clone:") or entry.get("engine") == "clone":
                    updated = voice_store.update_cloned(
                        new_id.removeprefix("vn:clone:"),
                        name=name,
                        tags=tags,
                    )
                    if updated:
                        entry = {**updated, "id": f"vn:clone:{updated['id']}", "engine": "clone"}
                else:
                    updated = voice_store.update_reference(new_id, name=name, tags=tags)
                    if updated:
                        entry = {**updated, "engine": "zmai", "type": "zmAI"}
            return _studio_voice_payload(entry)

        # metadata only
        if vid.startswith("vn:clone:"):
            cid = vid.removeprefix("vn:clone:").strip()
            entry = voice_store.update_cloned(cid, name=name, tags=tags)
            if not entry:
                raise HTTPException(404, "Không tìm thấy giọng clone")
            return _studio_voice_payload({**entry, "id": f"vn:clone:{entry['id']}", "engine": "clone"})

        entry = voice_store.update_reference(vid, name=name, tags=tags)
        if entry:
            return _studio_voice_payload({**entry, "engine": "zmai"})
        # fallback: bare clone id
        entry = voice_store.update_cloned(vid, name=name, tags=tags)
        if not entry:
            raise HTTPException(404, "Không tìm thấy giọng")
        return _studio_voice_payload({**entry, "id": f"vn:clone:{entry['id']}", "engine": "clone"})
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.delete("/api/tts/studio/voices/{voice_id:path}")
def api_tts_studio_voice_delete(voice_id: str):
    """Xóa giọng clone (hard) hoặc ẩn giọng zmAI (soft)."""
    from pipeline.tts import voice_store

    vid = (voice_id or "").strip()
    if not vid:
        raise HTTPException(400, "Thiếu id giọng")
    if vid.startswith("vn:clone:"):
        cid = vid.removeprefix("vn:clone:").strip()
        if not voice_store.remove_cloned(cid):
            raise HTTPException(404, "Không tìm thấy giọng clone")
        return {"ok": True}
    if voice_store.remove_reference(vid):
        return {"ok": True}
    if voice_store.remove_cloned(vid):
        return {"ok": True}
    raise HTTPException(404, "Không tìm thấy giọng")


class PreviewTtsIn(BaseModel):
    text: str
    voice: str = "el:pNInz6obpgDQGcFmaJgB"
    lang: str = "vi"


class RebakeSpeedIn(BaseModel):
    speed: float = 1.0


@router.post("/api/upload")
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


@router.get("/api/projects/{project_id}/video")
def api_video(project_id: str, request: Request):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    from pipeline.core.project import resolve_project_video

    return _serve_video_file(resolve_project_video(meta, project_id), request)


@router.post("/api/projects/{project_id}/rebake-speed")
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


@router.get("/api/projects/{project_id}/status")
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


@router.get("/api/projects/{project_id}/segments")
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


@router.get("/api/projects/{project_id}/overlays")
def api_overlays(project_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    return meta.get("overlays") or []


@router.post("/api/projects/{project_id}/overlays")
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


@router.put("/api/projects/{project_id}/overlays")
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


@router.put("/api/projects/{project_id}/overlays/{overlay_id}")
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


@router.delete("/api/projects/{project_id}/overlays/{overlay_id}")
def api_delete_overlay(project_id: str, overlay_id: str):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    overlays = [item for item in (meta.get("overlays") or []) if item.get("id") != overlay_id]
    meta["overlays"] = overlays
    save_meta(project_id, meta)
    return {"ok": True}


@router.put("/api/projects/{project_id}/segments/{seg_id}")
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


@router.put("/api/projects/{project_id}/segments")
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
            # exclude_none: tránh dub=null (Pydantic optional) → backend bỏ hết TTS
            dumped = item.model_dump(exclude_none=True)
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


@router.post("/api/projects/{project_id}/run")
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


@router.post("/api/projects/{project_id}/dub")
def api_dub(project_id: str, settings: Settings):
    meta = load_meta(project_id)
    if not meta:
        raise HTTPException(404)
    old_default = (meta.get("settings") or {}).get("defaultVoice") or ""
    force_tts = bool(settings.forceTts)
    dumped = settings.model_dump()
    dumped.pop("forceTts", None)
    meta["settings"] = dumped
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
        if force_tts:
            # Xóa trỏ cache — run_dub gen lại; file .wav xóa trong run_dub
            seg.pop("audioFile", None)
            seg.pop("audioUrl", None)
            seg.pop("audioDuration", None)
            seg.pop("videoSpeed", None)
    if force_tts:
        meta["forceTts"] = True
    save_meta(project_id, meta)
    arm_job(project_id)
    set_status(
        project_id,
        step="dub",
        progress=1,
        message="Queued… (gen lại TTS)" if force_tts else "Queued…",
        running=True,
        error=None,
    )
    _spawn(run_dub, project_id)
    return {"ok": True}


@router.post("/api/projects/{project_id}/cancel")
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


@router.post("/api/projects/{project_id}/settings")
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


@router.post("/api/projects/{project_id}/export")
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
        "path": f"backend/public/{project_id}/out/final.mp4",
        "exports": f"backend/public/exports/{project_id}.mp4",
    }


@router.get("/api/projects/{project_id}/output")
def api_output(project_id: str, download: bool = False):
    path = out_final(project_id)
    if not path.exists():
        legacy = project_dir(project_id) / "output.mp4"
        easy = PUBLIC_DATA / "exports" / f"{project_id}.mp4"
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


@router.post("/api/projects/{project_id}/reveal-output")
def api_reveal_output(project_id: str):
    """Mở Finder/Explorer tại file đã xuất (local app)."""
    import platform
    import subprocess

    path = out_final(project_id)
    if not path.exists():
        easy = PUBLIC_DATA / "exports" / f"{project_id}.mp4"
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


@router.post("/api/projects/{project_id}/segments/{seg_id}/preview-tts")
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


@router.post("/api/projects/{project_id}/segments/{seg_id}/retranslate")
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


@router.get("/api/projects/{project_id}/tts/{name}")
def api_tts(project_id: str, name: str):
    path = ensure_layout(project_id) / "tts" / name
    if not path.exists():
        raise HTTPException(404)
    st = path.stat()
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={
            "Cache-Control": "private, max-age=60",
            "ETag": f'"{st.st_mtime_ns:x}-{st.st_size:x}"',
        },
    )


@router.post("/api/projects/{project_id}/audio/no-vocals")
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


@router.get("/api/projects/{project_id}/audio/no-vocals/progress")
def api_no_vocals_progress(project_id: str):
    """Tiến độ tách stem — poll song song lúc POST /audio/no-vocals đang chạy."""
    if not load_meta(project_id):
        raise HTTPException(404)
    return read_stem_progress(project_id)


@router.get("/api/projects/{project_id}/cache/{name}")
def api_cache_file(project_id: str, name: str):
    if not re.fullmatch(r"no_vocals_[a-f0-9]+\.wav", name):
        raise HTTPException(400, "Tên file không hợp lệ")
    path = ensure_layout(project_id) / "cache" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@router.get("/api/health")
def health():
    return {"ok": True, "data": str(DATA)}


@router.get("/api/system/checks")
def api_system_checks():
    """Dependency checklist cho tab Thiết lập / first-run."""
    from pipeline.core.system_check import system_checks

    try:
        return system_checks()
    except Exception as e:
        # Không để exception Python kéo sập UI; native crash vẫn chỉ tránh bằng check nhẹ.
        raise HTTPException(500, f"system checks failed: {e}") from e


@router.post("/api/system/install/ocr_cuda")
def api_install_ocr_cuda():
    """Install ONNX Runtime GPU into the backend's Python environment."""
    from pipeline.core.system_check import install_ocr_cuda

    try:
        return install_ocr_cuda()
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.post("/api/system/install/demucs_cuda")
def api_install_demucs_cuda():
    """Cài Demucs + PyTorch CUDA (backend/.venv-demucs) — tách lời nhanh."""
    from pipeline.core.system_check import install_demucs_cuda

    try:
        return install_demucs_cuda()
    except Exception as e:
        raise HTTPException(500, str(e)) from e


