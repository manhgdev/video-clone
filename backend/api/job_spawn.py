"""Background job spawn for pipeline threads."""
from __future__ import annotations

import threading


def spawn(fn, *args) -> None:
    def wrap():
        try:
            fn(*args)
        except Exception:
            pass  # status already set in pipeline

    threading.Thread(target=wrap, daemon=True).start()
