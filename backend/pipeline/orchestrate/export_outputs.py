"""Đóng gói file xuất: mp4 + cover nhúng, audio, SRT, GIF, render metadata."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from pipeline.core.config import PUBLIC_DATA, export_display_path


import re as _re

def _project_slug(meta: dict) -> str:
    """Slug an toàn từ tên file video nguồn — dùng làm subfolder trong exports."""
    vp = str(meta.get("videoPath") or "")
    stem = Path(vp).stem if vp else ""
    slug = _re.sub(r"[^\w\s-]", "", stem).strip()
    slug = _re.sub(r"[\s_]+", "-", slug)
    slug = slug[:48].strip("-") or "project"
    return slug.lower()


def write_export_artifacts(
    meta: dict[str, Any],
    settings: dict[str, Any],
    out: Path,
    project_id: str,
    segments: list[dict[str, Any]],
    do_video: bool,
) -> tuple[Path, Path, str, str, str]:
    """Trả (exports_dir, easy_path, audio_rel, render_id, render_name)."""
    # ban de tim: backend/public/exports/<slug>/<id>.mp4
    _custom_dir = str(settings.get("exportOutputDir") or "").strip()
    if _custom_dir:
        exports = Path(_custom_dir)
        _slug = _project_slug(meta)
        exports = exports / _slug
    else:
        exports = PUBLIC_DATA / project_id / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    render_id = f"{project_id}-{time.time_ns()}"
    render_name = str(meta.pop("pendingRenderName", "")).strip() or f"Render {project_id}"
    import re as _re_ext
    safe_name = _re_ext.sub(r'[^\w\s-]', '', render_name).strip()
    safe_name = _re_ext.sub(r'[-\s]+', '-', safe_name)
    if not safe_name:
        safe_name = project_id

    img_out = None
    if str(settings.get("coverDataUrl") or "").startswith("data:image/"):
        try:
            import base64
            cover_data_url = str(settings.get("coverDataUrl"))
            header, encoded = cover_data_url.split(",", 1)
            img_data = base64.b64decode(encoded)
            ext = "jpg" if "jpeg" in header else "png"
            img_out = exports / f"{safe_name}.{ext}"
            img_out.write_bytes(img_data)
        except Exception as e:
            print(f"[export] Cover image decode error: {e}", flush=True)

    if do_video:
        easy = exports / f"{safe_name}.mp4"
        if img_out and img_out.is_file():
            try:
                from pipeline.core.jobs import run_cmd as _run_cmd
                _run_cmd(project_id, ["ffmpeg", "-y", "-i", str(out), "-i", str(img_out), "-map", "0", "-map", "1", "-c", "copy", "-disposition:v:1", "attached_pic", str(easy)])
            except Exception as e:
                print(f"[export] Cover image embed error: {e}", flush=True)
                shutil.copy2(out, easy)
        else:
            shutil.copy2(out, easy)
        (exports / f"{safe_name}.json").write_text(
            json.dumps({"name": render_name, "projectId": project_id}, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        easy = out  # audio/gif dung file tam; khong luu mp4 dau ra

    # Xuất Âm thanh (MP3/WAV) nếu người dùng chọn
    audio_rel = ""
    if bool(settings.get("exportAudio", False)):
        try:
            from pipeline.core.jobs import run_cmd as _run_cmd
            fmt = str(settings.get("exportAudioFormat") or "mp3").lower()
            audio_out = exports / f"{safe_name}.{fmt}"
            acodec = "libmp3lame" if fmt == "mp3" else "pcm_s16le" if fmt == "wav" else "aac"
            _run_cmd(project_id, ["ffmpeg", "-y", "-i", str(out), "-vn", "-acodec", acodec, str(audio_out)])
            if audio_out.is_file():
                audio_rel = export_display_path(audio_out)
                if not do_video:
                    # Audio-only → ghi render JSON để xuất hiện trong danh sách
                    (exports / f"{safe_name}.json").write_text(
                        json.dumps({"name": render_name, "projectId": project_id, "kind": "audio"}, ensure_ascii=False),
                        encoding="utf-8",
                    )
        except Exception as ae:
            print(f"[export] Audio export error: {ae}", flush=True)

    # Xuất Chú thích (SRT) nếu người dùng chọn
    if bool(settings.get("exportSrt", False)):
        try:
            from pipeline.export.srt import write_srt
            srt_out = exports / f"{safe_name}.srt"
            cues = []
            for s in segments:
                if not s.get("maskOnly") and (str(s.get("translation") or s.get("source") or "")).strip():
                    cues.append({
                        "start": float(s.get("start") or 0),
                        "end": float(s.get("end") or 0),
                        "text": (str(s.get("translation") or s.get("source") or "")).strip(),
                    })
            write_srt(srt_out, cues, capcut=False)
        except Exception as se:
            print(f"[export] SRT export error: {se}", flush=True)

    # Xuất GIF nếu người dùng chọn
    if bool(settings.get("exportGif", False)):
        try:
            from pipeline.core.jobs import run_cmd as _run_cmd
            gif_out = exports / f"{safe_name}.gif"
            res = int(settings.get("exportGifRes") or 480)
            vf = f"fps=10,scale={res}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            _run_cmd(project_id, ["ffmpeg", "-y", "-i", str(out), "-vf", vf, "-loop", "0", str(gif_out)])
        except Exception as ge:
            print(f"[export] GIF export error: {ge}", flush=True)
    return exports, easy, audio_rel, render_id, render_name
