"""Domain API routes."""
from __future__ import annotations

import json
import math
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.deps import (
    AppConfigIn,
    CloneRenameIn,
    CompoundClipIn,
    ExportPayload,
    PreviewTtsIn,
    RebakeSpeedIn,
    RetranslateIn,
    SEG_PRESERVE,
    SegmentIn,
    Settings,
    StudioSynthIn,
    TextOverlayIn,
    VoiceBulkMoveIn,
    VoicePatchIn,
    require_meta,
    validate_overlay,
    validate_segment_editor_fields,
)
from api.job_spawn import spawn
from api.video_serve import serve_video_file
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
from pipeline.core.media import meta_baked_speed, meta_has_user_bake, video_size
from pipeline.export.mux import (
    export_project_audio,
    find_cached_no_vocals,
    read_stem_progress,
    separate_no_vocals,
)
from pipeline.tts import engines_status

router = APIRouter()

# Aliases matching original routes_all names
_spawn = spawn
_serve_video_file = serve_video_file
_validate_overlay = validate_overlay
_validate_segment_editor_fields = validate_segment_editor_fields
_SEG_PRESERVE = SEG_PRESERVE

from pipeline.tts import voice_store
from pipeline.tts.engines import vieneu as vieneu_engine
from pipeline.tts.voice_store import TTS_OUTPUT, ensure_vieneu_dirs


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


def _studio_voice_payload(entry: dict) -> dict:
    eid = str(entry.get("id") or "")
    name = str(entry.get("name") or eid)
    eng = str(entry.get("engine") or "")
    tags = voice_store.normalize_voice_tags(entry.get("tags"))
    language = voice_store.normalize_voice_language(entry.get("language"))
    favorite = bool(entry.get("favorite"))
    if eng == "clone" or eid.startswith("vn:clone:"):
        cid = eid.removeprefix("vn:clone:")
        return {
            "id": f"vn:clone:{cid}",
            "name": f"VieNeu · Clone · {name}",
            "engine": "clone",
            "tags": tags,
            "language": language,
            "favorite": favorite,
        }
    return {
        "id": eid,
        "name": name,
        "engine": "zmai",
        "type": "zmAI",
        "tags": tags,
        "language": language,
        "favorite": favorite,
    }


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
    language_supplied = "language" in body.model_fields_set
    favorite_supplied = "favorite" in body.model_fields_set
    favorite = bool(body.favorite) if favorite_supplied else None
    try:
        tags = voice_store.normalize_voice_tags(body.tags, strict=True) if tags_supplied else None
        language = (
            voice_store.normalize_voice_language(body.language, strict=True)
            if language_supplied
            else None
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if (
        name is None
        and not engine
        and not tags_supplied
        and not language_supplied
        and not favorite_supplied
    ):
        raise HTTPException(400, "Cần name, tags, language, favorite và/hoặc engine")
    if body.name is not None and not name:
        raise HTTPException(400, "Tên giọng không được trống")

    try:
        if engine:
            entry = voice_store.move_voice_engine(vid, engine)
            # Metadata update after moving because the id may change.
            new_id = str(entry["id"])
            if name is not None or tags_supplied or language_supplied or favorite_supplied:
                if new_id.startswith("vn:clone:") or entry.get("engine") == "clone":
                    updated = voice_store.update_cloned(
                        new_id.removeprefix("vn:clone:"),
                        name=name,
                        tags=tags,
                        language=language,
                        favorite=favorite,
                    )
                    if updated:
                        entry = {**updated, "id": f"vn:clone:{updated['id']}", "engine": "clone"}
                else:
                    updated = voice_store.update_reference(
                        new_id,
                        name=name,
                        tags=tags,
                        language=language,
                        favorite=favorite,
                    )
                    if updated:
                        entry = {**updated, "engine": "zmai", "type": "zmAI"}
            return _studio_voice_payload(entry)

        # metadata only
        if vid.startswith("vn:clone:"):
            cid = vid.removeprefix("vn:clone:").strip()
            entry = voice_store.update_cloned(
                cid, name=name, tags=tags, language=language, favorite=favorite
            )
            if not entry:
                raise HTTPException(404, "Không tìm thấy giọng clone")
            return _studio_voice_payload({**entry, "id": f"vn:clone:{entry['id']}", "engine": "clone"})

        entry = voice_store.update_reference(
            vid, name=name, tags=tags, language=language, favorite=favorite
        )
        if entry:
            return _studio_voice_payload({**entry, "engine": "zmai"})
        # fallback: bare clone id
        entry = voice_store.update_cloned(
            vid, name=name, tags=tags, language=language, favorite=favorite
        )
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

