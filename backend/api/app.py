"""FastAPI application factory."""
from __future__ import annotations

import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes_all import router
from pipeline.core.cleanup import run_public_cleanup_periodically
from pipeline.core.config import PUBLIC_DATA


def create_app() -> FastAPI:
    # Windows: hide subprocess console windows (cheap)
    try:
        from pipeline.core.winproc import apply_subprocess_no_window

        apply_subprocess_no_window()
    except Exception:
        pass
    # ponytail: do NOT import torch here — blocks worker 10–40s on Windows;
    # cuDNN path fix runs in warm-models thread after listen.

    app = FastAPI(title="Video-Clone Local")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def api_health() -> dict[str, str]:
        """Cheap readiness — dev.mjs / proxies; no torch / model load."""
        return {"ok": "1", "status": "up"}

    app.include_router(router)
    app.mount("/data", StaticFiles(directory=str(PUBLIC_DATA)), name="public-data")

    @app.on_event("startup")
    def _warm_models() -> None:
        threading.Thread(
            target=run_public_cleanup_periodically,
            name="cleanup-public",
            daemon=True,
        ).start()

        def _run() -> None:
            try:
                from pipeline.core.cuda_dll import prefer_torch_cudnn

                prefer_torch_cudnn()
            except Exception:
                pass
            # VieNeu trước Whisper: Torch CUDA context ổn trước khi ctranslate2 vào
            try:
                from pipeline.tts.engines import vieneu as vieneu_engine

                if vieneu_engine.available():
                    vieneu_engine.warm()
            except Exception:
                pass
            try:
                from pipeline.asr import warm_whisper

                warm_whisper(0)
            except Exception:
                pass

        threading.Thread(target=_run, name="warm-models", daemon=True).start()

    return app
