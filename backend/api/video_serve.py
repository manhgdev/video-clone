"""Serve project video with safe Range handling (Windows socket storm)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from starlette.responses import FileResponse as StarletteFileResponse


class VideoFileResponse(StarletteFileResponse):
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
            return


def range_start(range_header: str | None) -> int | None:
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


def serve_video_file(path: Path, request: Request) -> StarletteFileResponse:
    if not path.is_file():
        raise HTTPException(404, detail="Không thấy video")
    st = path.stat()
    if st.st_size <= 0:
        raise HTTPException(404, detail="Video chưa sẵn sàng")
    start = range_start(request.headers.get("range"))
    force_full = start is not None and start >= st.st_size
    return VideoFileResponse(
        path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "private, max-age=2, must-revalidate",
            "ETag": f'"{st.st_mtime_ns:x}-{st.st_size:x}"',
            "Accept-Ranges": "bytes",
        },
        force_full=force_full,
    )
