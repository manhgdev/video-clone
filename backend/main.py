"""Uvicorn entry — create_app only."""
from __future__ import annotations

from api.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8787, reload=True)
