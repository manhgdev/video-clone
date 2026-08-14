"""Subtitle files used as a source for Clone Video."""
from __future__ import annotations

import uuid
from pathlib import Path

from pipeline.export.srt import parse_srt, split_display_cues, style_params


def subtitle_segments(path: Path, *, preview_sec: int = 0) -> list[dict]:
    """Read an SRT exactly as supplied, preserving every cue and timestamp."""
    cues = parse_srt(path.read_text(encoding="utf-8-sig", errors="replace"))
    limit = max(0, int(preview_sec or 0))
    out = []
    for cue in cues:
        start, end = float(cue["start"]), float(cue["end"])
        if limit and start >= limit:
            continue
        if limit:
            end = min(end, float(limit))
        text = str(cue.get("text") or "").strip()
        if text and end >= start:
            out.append({"id": str(uuid.uuid4()), "index": len(out), "start": start, "end": end,
                        "source": text, "translation": "", "voice": ""})
    return out


def split_portrait_caption_segments(segments: list[dict]) -> list[dict]:
    """Split speech/SRT cues for a 9:16 caption lane without losing timing.

    Downloaded SRT and Whisper cues describe spoken sentences, which are often
    much longer than a readable portrait hard-sub.  A portrait cue therefore
    gets 7 words / 28 characters at most and receives a proportional slice of
    the original cue's timeline.  The original source text is retained in
    full across the resulting consecutive cues, so translation and TTS use
    the same text rather than truncating it for display.
    """
    params = style_params("v916")
    result: list[dict] = []
    for segment in segments:
        source = " ".join(str(segment.get("source") or "").split())
        start = float(segment.get("start") or 0)
        end = max(start, float(segment.get("end") or start))
        # ``split_display_cues`` preserves sentence rhythm and then may merge a
        # tiny trailing fragment.  For a 9:16 lane enforce the hard 7-word /
        # 28-character ceiling once more after that friendly merge.
        candidates = split_display_cues(
            source, max_chars=params.max_chars, max_words=params.max_words
        ) if source else []
        pieces: list[str] = []
        for candidate in candidates:
            words = candidate.split()
            buf: list[str] = []
            for word in words:
                trial = " ".join((*buf, word))
                if buf and (
                    len(buf) >= params.max_words or len(trial) > params.max_chars
                ):
                    pieces.append(" ".join(buf))
                    buf = [word]
                else:
                    buf.append(word)
            if buf:
                pieces.append(" ".join(buf))
        # A lone trailing word is never a useful on-screen cue.  Keep it with
        # its neighbour even if that one cue slightly exceeds the normal 7
        # word / 28 character target; readability wins over a hard split.
        while len(pieces) > 1:
            lone_index = next(
                (index for index, piece in enumerate(pieces) if len(piece.split()) == 1),
                None,
            )
            if lone_index is None:
                break
            if lone_index > 0:
                pieces[lone_index - 1] = f"{pieces[lone_index - 1]} {pieces[lone_index]}"
                pieces.pop(lone_index)
            else:
                pieces[1] = f"{pieces[0]} {pieces[1]}"
                pieces.pop(0)
        if len(pieces) <= 1:
            row = dict(segment)
            row["source"] = source
            result.append(row)
            continue

        weights = [max(1, len("".join(piece.split()))) for piece in pieces]
        total = sum(weights)
        cursor = start
        for pos, (piece, weight) in enumerate(zip(pieces, weights)):
            # The last piece reaches the supplied cue end exactly: no gaps or
            # overlaps are introduced when the file is exported again.
            next_cursor = end if pos == len(pieces) - 1 else (
                cursor + (end - start) * weight / total
            )
            row = dict(segment)
            row.update({
                "id": str(uuid.uuid4()),
                "index": len(result),
                "start": cursor,
                "end": max(cursor, next_cursor),
                "source": piece,
                "translation": "",
                "voice": segment.get("voice") or "",
            })
            result.append(row)
            cursor = next_cursor

    for index, row in enumerate(result):
        row["index"] = index
    return result
