"""Background job spawn for pipeline threads."""
from __future__ import annotations

import threading
import traceback


def spawn(fn, *args) -> None:
    def wrap():
        try:
            fn(*args)
        except Exception as e:
            # Pipeline thường tự set_status; lỗi sớm (ImportError/NameError…) thì không —
            # không nuốt im → UI kẹt «Queued…».
            traceback.print_exc()
            project_id = args[0] if args else None
            if isinstance(project_id, str) and project_id:
                try:
                    from pipeline.core.jobs import Cancelled
                    from pipeline import set_status

                    if isinstance(e, Cancelled):
                        return
                    set_status(
                        project_id,
                        progress=0,
                        message="Lỗi",
                        running=False,
                        error=str(e).strip()[:280] or type(e).__name__,
                    )
                except Exception:
                    pass

    threading.Thread(target=wrap, daemon=True).start()
