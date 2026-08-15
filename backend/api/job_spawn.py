"""Background job spawn for pipeline threads."""
from __future__ import annotations

import threading
import traceback


def spawn(fn, *args) -> None:
    """Chạy job trên thread daemon — lỗi không lan ra main (kéo sập desktop)."""

    def wrap() -> None:
        import time

        # Yield GIL to allow FastAPI main thread to flush HTTP response
        # before this background thread starts loading heavy models (PyTorch etc.)
        time.sleep(0.2)

        job = getattr(fn, "__name__", "job")
        try:
            fn(*args)
        except BaseException as e:
            # Không re-raise. Native crash (cv2/CUDA) vẫn có thể kill process.
            try:
                from pipeline.core.jobs import Cancelled

                if isinstance(e, Cancelled):
                    return
            except Exception:
                if type(e).__name__ == "Cancelled":
                    return
            try:
                from pipeline.core.app_log import append_exception

                append_exception(f"[job:{job}] FAILED", e)
            except Exception:
                traceback.print_exc()
            project_id = args[0] if args else None
            if isinstance(project_id, str) and project_id:
                try:
                    from pipeline import set_status

                    msg = str(e).strip()[:280] or type(e).__name__
                    set_status(
                        project_id,
                        progress=0,
                        message=f"Lỗi: {msg}",
                        running=False,
                        error=msg,
                    )
                except Exception as st_e:
                    try:
                        from pipeline.core.app_log import append_exception

                        append_exception("[job] set_status failed", st_e)
                    except Exception:
                        traceback.print_exc()

    threading.Thread(target=wrap, daemon=True, name=f"job-{getattr(fn, '__name__', 'fn')}").start()
