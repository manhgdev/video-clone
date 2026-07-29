"""HTTP routes — domain modules under api.routes.*."""
from __future__ import annotations

from fastapi import APIRouter

from api.routes import audio
from api.routes import download
from api.routes import jobs
from api.routes import overlays
from api.routes import projects
from api.routes import rendered
from api.routes import segments
from api.routes import system
from api.routes import cleaner
from api.routes import srt_image
from api.routes import tts_preview
from api.routes import tts_studio
from api.routes import tts_voices

router = APIRouter()
router.include_router(audio.router)
router.include_router(download.router)
router.include_router(jobs.router)
router.include_router(overlays.router)
router.include_router(projects.router)
router.include_router(rendered.router)
router.include_router(segments.router)
router.include_router(system.router)
router.include_router(cleaner.router)
router.include_router(srt_image.router)
router.include_router(tts_preview.router)
router.include_router(tts_studio.router)
router.include_router(tts_voices.router)

# Legacy schema re-exports
from api.deps import ExportPayload, SegmentIn, Settings, TextOverlayIn  # noqa: E402,F401
