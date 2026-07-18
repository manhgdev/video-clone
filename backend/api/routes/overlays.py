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

