"""SRT import/export — UTF-8, timeline CapCut-friendly (cue ngắn)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

# ── 5 kiểu xuất SRT ────────────────────────────────────────────────
SRT_STYLES = ("hard", "v916", "h169", "clause", "sentence")

class _StyleP(NamedTuple):
    max_chars: int
    max_words: int
    wrap_line: int
    mode: str  # hard | clause | sentence

def style_params(style: str = "hard") -> _StyleP:
    if style == "v916":
        return _StyleP(28, 7, 28, "hard")
    if style == "h169":
        return _StyleP(56, 16, 56, "hard")
    if style == "clause":
        return _StyleP(48, 14, 48, "clause")
    if style == "sentence":
        return _StyleP(120, 40, 60, "sentence")
    return _StyleP(42, 12, 42, "hard")  # "hard" default


def _ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap_capcut_text(text: str, max_line: int = 42, max_lines: int = 2) -> str:
    """1–2 dòng ngắn (CapCut / hardsub)."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return "…"
    if len(raw) <= max_line:
        return raw
    words = raw.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip() if cur else w
        if len(trial) <= max_line:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = w
        else:
            # từ quá dài
            lines.append(w[:max_line])
            cur = w[max_line:]
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    elif cur and lines:
        # dồn phần còn lại vào dòng cuối (cắt)
        rest = cur
        while rest and len(lines) < max_lines:
            lines.append(rest[:max_line])
            rest = rest[max_line:]
        if rest and lines:
            lines[-1] = (lines[-1][: max(0, max_line - 1)] + "…")[:max_line]
    return "\n".join(lines[:max_lines]) or "…"


def split_display_cues(
    text: str,
    *,
    max_chars: int = 42,
    max_words: int = 12,
) -> list[str]:
    """Tách text thành cue hiển thị ngắn (CapCut).

    ~8–12 từ / ~35–45 ký tự / cue — không 1 đoạn dài 1 cue.
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ["…"]
    # Tách câu trước (giữ 100.000)
    sentences = re.split(
        r"(?<=[!?\…])\s+|(?<=\.)(?!\d)\s+(?=[\"'“”‘’]?[^\s\d])",
        raw,
    )
    units: list[str] = []
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        words = sent.split(" ")
        buf: list[str] = []
        for w in words:
            trial = (*buf, w)
            chars = len(" ".join(trial))
            if buf and (len(trial) > max_words or chars > max_chars):
                units.append(" ".join(buf))
                buf = [w]
            else:
                buf.append(w)
        if buf:
            units.append(" ".join(buf))
    # gộp mẩu < 12 ký tự vào trước
    out: list[str] = []
    for u in units:
        if out and len(u) < 12 and len(out[-1]) + 1 + len(u) <= max_chars + 8:
            out[-1] = f"{out[-1]} {u}"
        else:
            out.append(u)
    return out or ["…"]


def split_by_clauses(text: str, max_chars: int = 48) -> list[str]:
    """Tách theo clause: , ; : — và dấu câu; cắt cứng nếu > max_chars."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ["…"]
    parts = re.split(r"(?<=[,;:—–])\s+", raw)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_chars:
            out.append(p)
        else:
            # cắt gần khoảng trắng
            while len(p) > max_chars:
                idx = p.rfind(" ", 0, max_chars)
                if idx <= 0:
                    idx = max_chars
                out.append(p[:idx].rstrip())
                p = p[idx:].lstrip()
            if p:
                out.append(p)
    # gộp mẩu nhỏ (< 10 ký tự) vào trước
    merged: list[str] = []
    for u in out:
        if merged and len(u) < 10 and len(merged[-1]) + 1 + len(u) <= max_chars + 6:
            merged[-1] = f"{merged[-1]} {u}"
        else:
            merged.append(u)
    return merged or ["…"]


def split_by_sentences(text: str, max_chars: int = 120) -> list[str]:
    """Mỗi câu . ! ? … = 1 cue; cắt mềm nếu > max_chars."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ["…"]
    sents = re.split(
        r"(?<=[.!?\u2026])\s+(?=[\"'\u201c\u201d\u2018\u2019]?[A-Z\u00C0-\u1ef9\d])",
        raw,
    )
    out: list[str] = []
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_chars:
            out.append(s)
        else:
            while len(s) > max_chars:
                idx = s.rfind(" ", 0, max_chars)
                if idx <= 0:
                    idx = max_chars
                out.append(s[:idx].rstrip())
                s = s[idx:].lstrip()
            if s:
                out.append(s)
    return out or ["…"]


def _split_for_style(text: str, sp: _StyleP) -> list[str]:
    """Dispatch splitter theo mode."""
    if sp.mode == "clause":
        return split_by_clauses(text, max_chars=sp.max_chars)
    if sp.mode == "sentence":
        return split_by_sentences(text, max_chars=sp.max_chars)
    return split_display_cues(text, max_chars=sp.max_chars, max_words=sp.max_words)


def cues_from_parts(
    chunks: list[str],
    part_durs: list[float],
    *,
    gap_sec: float = 0.0,
    style: str = "hard",
    # ponytail: legacy callers still pass max_chars/max_words — ignored when style given
    max_chars: int = 42,
    max_words: int = 12,
) -> list[dict]:
    """Chia mỗi part TTS thành nhiều cue SRT ngắn, timeline ∝ số ký tự trong part."""
    sp = style_params(style)
    cues: list[dict] = []
    cursor = 0.0
    n = min(len(chunks), len(part_durs))
    for i in range(n):
        text = (chunks[i] or "").strip() or "…"
        pd = max(0.08, float(part_durs[i]))
        pieces = _split_for_style(text, sp)
        weights = [max(1, len(p)) for p in pieces]
        total_w = sum(weights) or 1
        t0 = cursor
        acc = 0.0
        for j, piece in enumerate(pieces):
            if j < len(pieces) - 1:
                share = pd * (weights[j] / total_w)
            else:
                share = max(0.06, pd - acc)
            start = t0 + acc
            end = start + max(0.06, share)
            cues.append(
                {
                    "start": start,
                    "end": end,
                    "text": wrap_capcut_text(piece, max_line=sp.wrap_line, max_lines=2),
                }
            )
            acc += max(0.06, share)
        cursor = t0 + pd + max(0.0, gap_sec)
    return cues


def write_srt(
    path: Path,
    cues: list[dict],
    *,
    capcut: bool = True,
    wrap_line: int = 42,
) -> None:
    """cues: {start, end, text}. capcut=True → wrap 1–2 dòng ngắn."""
    lines: list[str] = []
    for i, c in enumerate(cues, 1):
        text = str(c.get("text") or "").strip() or "…"
        if capcut and "\n" not in text:
            text = wrap_capcut_text(text, max_line=wrap_line)
        lines.append(str(i))
        lines.append(f"{_ts(float(c['start']))} --> {_ts(float(c['end']))}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def _ms_frac(raw: str) -> float:
    """1–3 digit fractional seconds → milliseconds (SRT standard = 3 digits)."""
    s = (raw or "0").strip()
    if not s:
        return 0.0
    if len(s) >= 3:
        return int(s[:3]) / 1000.0
    return int(s.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[dict]:
    """Parse SRT timestamps exactly (HH:MM:SS,mmm → seconds float)."""
    # strip BOM
    if text and text.startswith("\ufeff"):
        text = text[1:]
    # normalize newlines; allow single blank-line or multi-blank between cues
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    blocks = re.split(r"\n\s*\n+", normalized)
    out: list[dict] = []
    ts_re = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
    )
    for b in blocks:
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # find timestamp line (index 0 or 1 — some files omit cue index)
        ts_i = 1 if "-->" in lines[1] else 0 if "-->" in lines[0] else -1
        if ts_i < 0:
            continue
        m = ts_re.search(lines[ts_i])
        if not m:
            continue
        h1, m1, s1, f1, h2, m2, s2, f2 = m.groups()
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + _ms_frac(f1)
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + _ms_frac(f2)
        if end < start:
            end = start
        body_lines = lines[ts_i + 1 :]
        out.append({"start": start, "end": end, "text": "\n".join(body_lines)})
    return out
