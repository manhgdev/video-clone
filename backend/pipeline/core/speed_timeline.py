"""Speed bake + timeline baseline math (t_display = t_1x / speed).

Tách từ core/media.py — domain thuần meta/timeline, không đụng ffmpeg.
media.py re-export (facade) nên call site cũ giữ nguyên import.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

def clamp_playback_speed(speed: float) -> float:
    return max(0.5, min(2.0, float(speed)))


def meta_baked_speed(meta: dict) -> float:
    """Tốc độ đã bake vào workVideo.

    - bakedSpeed có key (kể cả 1.0 sau «Áp dụng 1×») → dùng giá trị đó
    - chỉ bakedPreferVideo (legacy) → 0.80
    - không key → 1.0 (timeline 1×; soft preferVideo chỉ ở FE playbackRate)
    """
    if meta.get("bakedSpeed") is not None:
        return clamp_playback_speed(float(meta["bakedSpeed"]))
    if meta.get("bakedPreferVideo"):
        return 0.80
    return 1.0


def meta_has_user_bake(meta: dict) -> bool:
    """User đã bấm Áp dụng tốc độ (kể cả 1×). Không lẫn soft preferVideo."""
    return meta.get("bakedSpeed") is not None


def initial_rate_from_match_duration(match_duration: str | None) -> float:
    """preferVideo → 0.80; còn lại → 1.00. Không bake file."""
    return 0.80 if str(match_duration or "").strip() == "preferVideo" else 1.0


def ensure_project_initial_playback_rate(
    meta: dict,
    settings: dict | None = None,
) -> float:
    """Ghi projectInitialPlaybackRate đúng một lần — không reset, không bake."""
    raw = meta.get("projectInitialPlaybackRate")
    if raw is not None:
        try:
            return clamp_playback_speed(float(raw))
        except (TypeError, ValueError):
            pass
    s = settings if isinstance(settings, dict) else (meta.get("settings") or {})
    rate = initial_rate_from_match_duration(
        str((s or {}).get("matchDuration") or "")
    )
    meta["projectInitialPlaybackRate"] = rate
    return rate


def speed_cache_tag(speed: float) -> str:
    return f"s{int(round(clamp_playback_speed(speed) * 100)):03d}"


def preview_clip_matches(name: str, preview_sec: int) -> bool:
    """Đúng clip của cửa sổ preview Ns: preview_5.mp4 / preview_5_s080.mp4.

    So khớp đủ tên — substring cũ ("preview_5" in name) match nhầm preview_50….
    """
    import re as _re

    return bool(
        _re.fullmatch(
            rf"preview_{int(preview_sec)}(_s\d{{3}})?\.[a-z0-9]+", (name or "").lower()
        )
    )


def scale_time_fields(obj: dict, scale: float, keys: tuple[str, ...] = ("start", "end")) -> None:
    if abs(scale - 1.0) < 1e-9:
        return
    for k in keys:
        if obj.get(k) is None:
            continue
        try:
            obj[k] = float(obj[k]) * scale
        except (TypeError, ValueError):
            pass


_SEG_TIME_KEYS = ("start", "end", "coverStart", "coverEnd")


def _deepcopy_json(obj: Any) -> Any:
    import copy

    return copy.deepcopy(obj)


def _scale_segment_tree(seg: dict, scale: float) -> None:
    """Scale start/end/cover + compoundChildren (relative hoặc absolute)."""
    if not isinstance(seg, dict) or abs(scale - 1.0) < 1e-9:
        return
    scale_time_fields(seg, scale, _SEG_TIME_KEYS)
    children = seg.get("compoundChildren")
    if not isinstance(children, list):
        return
    for ch in children:
        if isinstance(ch, dict):
            scale_time_fields(ch, scale, _SEG_TIME_KEYS)


def _snapshot_timeline_1x(meta: dict, current_speed: float) -> dict[str, Any]:
    """Chụp timeline về mốc 1× (t_1x = t_display * current_speed). Tránh nhân chồng khi bake nhiều lần."""
    speed = clamp_playback_speed(current_speed)
    to_1x = speed  # display → 1×
    segs = _deepcopy_json(meta.get("segments") or [])
    ovs = _deepcopy_json(meta.get("overlays") or [])
    for seg in segs:
        if isinstance(seg, dict):
            _scale_segment_tree(seg, to_1x)
            # Giữ videoSpeed từng câu (TTS fit) — không ghi đè bake global
    for ov in ovs:
        if isinstance(ov, dict):
            scale_time_fields(ov, to_1x, ("start", "end"))
    # duration meta = nguồn 1×; workDuration = file bake (display)
    base_dur = float(meta.get("duration") or 0)
    work_dur = float(meta.get("workDuration") or 0)
    if base_dur <= 0 and work_dur > 0:
        # work đang ở display → quy về 1×
        base_dur = work_dur * speed
    if base_dur <= 0 and segs:
        try:
            # segs đã scale về 1× ở trên
            base_dur = max(float(s.get("end") or 0) for s in segs if isinstance(s, dict))
        except ValueError:
            base_dur = 0.0
    preview_sec = max(0, int(meta.get("previewSec") or 0))
    # previewSec lưu 1× (cửa sổ dịch)
    preview_1x = float(preview_sec) * speed if preview_sec > 0 else 0.0
    return {
        "segments": segs,
        "overlays": ovs,
        "duration1x": base_dur,
        "previewSec1x": preview_1x,
        "previewSec": preview_sec,
    }


def ensure_timeline_baseline(meta: dict, current_speed: float) -> dict[str, Any]:
    """Baseline 1× chỉ tạo một lần (hoặc khi thiếu). Mọi bake sau tính từ đây."""
    bl = meta.get("timelineBaseline")
    if isinstance(bl, dict) and isinstance(bl.get("segments"), list):
        return bl
    bl = _snapshot_timeline_1x(meta, current_speed)
    meta["timelineBaseline"] = bl
    return bl


def apply_timeline_from_baseline(meta: dict, new_speed: float) -> None:
    """t_display = t_1x / new_speed — luôn từ baseline bất biến, không cascade."""
    import copy

    new_speed = clamp_playback_speed(new_speed)
    bl = ensure_timeline_baseline(meta, meta_baked_speed(meta))
    # Dùng tỉ số chính xác; round µs-level khi ghi mốc (tránh drift float)
    scale = 1.0 / new_speed

    def _round_us(t: float) -> float:
        return round(float(t) * 1_000_000.0) / 1_000_000.0

    segs = copy.deepcopy(bl.get("segments") or [])
    ovs = copy.deepcopy(bl.get("overlays") or [])
    for seg in segs:
        if isinstance(seg, dict):
            _scale_segment_tree(seg, scale)
            for k in _SEG_TIME_KEYS:
                if seg.get(k) is not None:
                    try:
                        seg[k] = _round_us(float(seg[k]))
                    except (TypeError, ValueError):
                        pass
            for ch in seg.get("compoundChildren") or []:
                if isinstance(ch, dict):
                    for k in _SEG_TIME_KEYS:
                        if ch.get(k) is not None:
                            try:
                                ch[k] = _round_us(float(ch[k]))
                            except (TypeError, ValueError):
                                pass
    for ov in ovs:
        if isinstance(ov, dict):
            scale_time_fields(ov, scale, ("start", "end"))
            for k in ("start", "end"):
                if ov.get(k) is not None:
                    try:
                        ov[k] = _round_us(float(ov[k]))
                    except (TypeError, ValueError):
                        pass
    meta["segments"] = segs
    meta["overlays"] = ovs
    dur1 = float(bl.get("duration1x") or meta.get("duration") or 0)
    if dur1 > 0:
        meta["duration"] = _round_us(dur1)
        if abs(new_speed - 1.0) > 0.001:
            meta["workDuration"] = _round_us(dur1 / new_speed)
        else:
            meta.pop("workDuration", None)


def _merge_segment_content(dst: dict, src: dict, *, prefer_src: bool = False) -> None:
    """Giữ text/TTS/bbox từ src — không đụng start/end.

    prefer_src=True: text/TTS hiện tại (preRemap) thắng baseline cũ.
    """
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return
    for k in (
        "translation",
        "source",
        "audioUrl",
        "audioFile",
        "audioDuration",
        "bbox",
        "bboxInherited",
        "captionLayout",
        "layout",
        "dub",
        "voice",
        "fontSize",
        "ttsVolume",
        "ttsSpeed",
        "ttsBake",
        "videoSpeed",
        "fontFamily",
        "textColor",
        "groupId",
        "isCompound",
    ):
        sv, dv = src.get(k), dst.get(k)
        if sv is None:
            continue
        if k in ("translation", "source"):
            if str(sv).strip() and (prefer_src or not str(dv or "").strip()):
                dst[k] = sv
        elif k in ("audioUrl", "audioFile", "audioDuration", "voice", "ttsSpeed", "ttsVolume", "dub"):
            if prefer_src or dv is None:
                if sv is not None and sv != "":
                    dst[k] = sv
        elif dv is None:
            dst[k] = sv
    # compound children: merge theo id
    sc = src.get("compoundChildren")
    dc = dst.get("compoundChildren")
    if isinstance(sc, list) and sc:
        if not isinstance(dc, list) or not dc:
            dst["compoundChildren"] = _deepcopy_json(sc)
        else:
            by_id = {str(c.get("id")): c for c in sc if isinstance(c, dict) and c.get("id")}
            for c in dc:
                if isinstance(c, dict) and c.get("id") and str(c["id"]) in by_id:
                    _merge_segment_content(c, by_id[str(c["id"])], prefer_src=prefer_src)


def _heal_segments_content(meta: dict) -> None:
    """Sau remap: text/TTS lấy từ preRemap (ưu tiên), thiếu mới lấy baseline."""
    segs = meta.get("segments") or []
    if not isinstance(segs, list):
        return
    pre = meta.pop("_preRemapSegments", None)
    pre_by: dict[str, dict] = {}
    if isinstance(pre, list):
        for s in pre:
            if isinstance(s, dict) and s.get("id"):
                pre_by[str(s["id"])] = s
                for ch in s.get("compoundChildren") or []:
                    if isinstance(ch, dict) and ch.get("id"):
                        pre_by[str(ch["id"])] = ch
    bl_by: dict[str, dict] = {}
    bl = meta.get("timelineBaseline") or {}
    if isinstance(bl, dict):
        for s in bl.get("segments") or []:
            if isinstance(s, dict) and s.get("id"):
                bl_by[str(s["id"])] = s
                for ch in s.get("compoundChildren") or []:
                    if isinstance(ch, dict) and ch.get("id"):
                        bl_by[str(ch["id"])] = ch

    def _heal_one(seg: dict) -> None:
        sid = str(seg.get("id") or "")
        if sid and sid in pre_by:
            _merge_segment_content(seg, pre_by[sid], prefer_src=True)
        elif sid and sid in bl_by:
            _merge_segment_content(seg, bl_by[sid], prefer_src=False)
        for ch in seg.get("compoundChildren") or []:
            if isinstance(ch, dict):
                _heal_one(ch)

    for seg in segs:
        if isinstance(seg, dict):
            _heal_one(seg)


def remap_timeline_for_speed_change(meta: dict, old_speed: float, new_speed: float) -> None:
    """Đổi bake speed: luôn từ baseline 1× bất biến — không cascade 0.8→1.15→0.8.

    Baseline chỉ tạo MỘT LẦN (ensure_timeline_baseline). Mọi tốc độ mới:
      t_display = t_1x / new_speed
    Quay lại tốc độ cũ → cùng kết quả (không sai số tích lũy).
    """
    old_speed = clamp_playback_speed(old_speed)
    new_speed = clamp_playback_speed(new_speed)
    if abs(old_speed - new_speed) < 1e-12:
        return
    # Nội dung hiện tại (text/TTS/overlay) để heal sau scale
    meta["_preRemapSegments"] = _deepcopy_json(meta.get("segments") or [])
    meta["_preRemapOverlays"] = _deepcopy_json(meta.get("overlays") or [])
    # CHỈ tạo baseline nếu chưa có — KHÔNG snapshot lại từ timeline đã scale
    ensure_timeline_baseline(meta, old_speed)
    apply_timeline_from_baseline(meta, new_speed)
    _heal_segments_content(meta)
    # Overlay text/asset: ưu tiên bản vừa edit (preRemap)
    pre_ov = meta.pop("_preRemapOverlays", None)
    if isinstance(pre_ov, list) and isinstance(meta.get("overlays"), list):
        pre_by = {
            str(o.get("id")): o
            for o in pre_ov
            if isinstance(o, dict) and o.get("id")
        }
        for ov in meta["overlays"]:
            if not isinstance(ov, dict) or not ov.get("id"):
                continue
            src = pre_by.get(str(ov["id"]))
            if not src:
                continue
            for k in ("text", "assetUrl", "color", "fontFamily", "fontSize", "kind"):
                if src.get(k) is not None and src.get(k) != "":
                    ov[k] = src[k]
    # Đồng bộ text/TTS vào baseline (không đụng mốc thời gian 1×)
    try:
        bl = meta.get("timelineBaseline")
        if isinstance(bl, dict) and isinstance(bl.get("segments"), list):
            cur_by = {
                str(s.get("id")): s
                for s in (meta.get("segments") or [])
                if isinstance(s, dict) and s.get("id")
            }
            for bs in bl["segments"]:
                if isinstance(bs, dict) and bs.get("id") and str(bs["id"]) in cur_by:
                    _merge_segment_content(bs, cur_by[str(bs["id"])], prefer_src=True)
        if isinstance(bl, dict) and isinstance(bl.get("overlays"), list) and isinstance(pre_ov, list):
            cur_ov = {
                str(o.get("id")): o
                for o in (meta.get("overlays") or [])
                if isinstance(o, dict) and o.get("id")
            }
            for bo in bl["overlays"]:
                if isinstance(bo, dict) and bo.get("id") and str(bo["id"]) in cur_ov:
                    src = cur_ov[str(bo["id"])]
                    for k in ("text", "assetUrl", "color", "fontFamily", "fontSize", "kind"):
                        if src.get(k) is not None and src.get(k) != "":
                            bo[k] = src[k]
    except Exception:
        pass


def invalidate_timeline_baseline(meta: dict) -> None:
    """Gọi khi trim/split/đổi nguồn — baseline 1× phải chụp lại từ timeline hiện tại."""
    meta.pop("timelineBaseline", None)

def preview_1x_path(project_id: str, meta: dict) -> Path:
    """File preview/source 1× (chưa bake tốc độ)."""
    from .project import ensure_layout

    preview_sec = max(0, int(meta.get("previewSec") or 0))
    cache = ensure_layout(project_id) / "cache"
    if preview_sec > 0:
        cached = cache / f"preview_{preview_sec}.mp4"
        if cached.is_file():
            return cached
    return Path(str(meta["videoPath"]))
