"""FastAPI application factory."""
from __future__ import annotations

import sys
import threading
from contextlib import asynccontextmanager

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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            from pipeline.core.config import ensure_data_dirs

            ensure_data_dirs()
        except Exception:
            pass
        try:
            from pipeline.download import ensure_download_dirs

            ensure_download_dirs()
        except Exception:
            pass

        threading.Thread(
            target=run_public_cleanup_periodically,
            name="cleanup-public",
            daemon=True,
        ).start()

        try:
            from pipeline.core.app_log import append_log, install_process_hooks

            install_process_hooks()
            append_log("[api] lifespan start", also_print=False)
        except Exception:
            pass

        def _run() -> None:
            # Frozen + dev warm: không pip torch (DLL lock / WinError 5). Chỉ warm model đã cài.
            if getattr(sys, "frozen", False):
                return
            try:
                from pipeline.core.system_check import ensure_runtime_torch

                # ensure_runtime_torch no-ops pip when torch already loaded
                ensure_runtime_torch()
            except Exception as exc:
                try:
                    from pipeline.core.app_log import append_exception

                    append_exception("[warm-models] ensure_runtime_torch skipped", exc)
                except Exception:
                    print(f"[warm-models] ensure_runtime_torch skipped: {exc}", flush=True)
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
        yield

    app = FastAPI(title="Video-Clone Local", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def api_health() -> dict[str, object]:
        """Cheap readiness — dev.mjs / desktop launcher; no torch / model load."""
        import os

        from pipeline.core.config import DATA

        port = int(os.environ.get("VIDEO_CLONE_PORT") or 8787)
        return {"ok": True, "app": "videoclone", "port": port, "data": str(DATA)}

    app.include_router(router)
    app.mount("/data", StaticFiles(directory=str(PUBLIC_DATA)), name="public-data")

    return app
