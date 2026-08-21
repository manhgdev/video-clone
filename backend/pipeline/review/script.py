"""Review script from story graph. Voice lines are later timed by existing TTS."""
from __future__ import annotations

import re
from typing import Any

from pipeline.mt.text import _clean_burn_text, _lang_name
from pipeline.review.llm import generate_json

STYLES = {
    "normal": "balanced recap with hook and ending",
    "recap": "full plot recap, chapter by chapter",
    "tiktok": "fast punchy lines, 1-2 sentences each",
    "cinematic": "poetic, slower, atmospheric",
    "humorous": "witty, light spoilers unless forbidden",
    "deep": "analysis of theme, character, craft",
}


NARRATION = {"default": 1.0, "mild": 1.2, "more": 1.37}
_SEC_PER_LINE = 6.0
_PAD_VI = (
    "Câu chuyện tiếp tục với những tình tiết mới. Nhân vật chính phải đối mặt thử thách, "
    "đưa ra lựa chọn, và xung đột ngày càng rõ ràng hơn trước mắt khán giả."
)
_PAD_EN = (
    "The story keeps moving. The lead faces a new test, makes a choice, "
    "and the conflict comes into sharper focus for the audience."
)
_PAD_VARIANTS_VI = (
    _PAD_VI,
    "Diễn biến tiếp tục mở ra khi các nhân vật phản ứng trước biến cố, còn mâu thuẫn chính dần trở nên căng thẳng hơn.",
    "Những chi tiết vừa xuất hiện làm thay đổi tình thế, buộc nhân vật phải cân nhắc lựa chọn và hậu quả phía trước.",
    "Mạch truyện vẫn tiến về phía trước, đồng thời hé lộ thêm động cơ, quan hệ và thử thách của các nhân vật.",
    "Từ đây, câu chuyện phát triển theo hướng mới khi áp lực gia tăng và từng quyết định đều tác động đến kết quả.",
)
_PAD_VARIANTS_EN = (
    _PAD_EN,
    "Events keep unfolding as the characters react to new pressure and the central conflict becomes more intense.",
    "Recent details shift the situation, forcing the characters to weigh their choices and the consequences ahead.",
    "The narrative moves forward while revealing more about the characters, their motives, and their relationships.",
    "From here, the story takes a new direction as the stakes rise and each decision shapes what follows.",
)
_SCENE_MARK = re.compile(
    r"\b(?:scene|cảnh|ch(?:apter)?)\s*[:.#]?\s*\d+\b|\b(?:ene|cene)\s*\d+\b|\bch\d+\s*:",
    re.I,
)
_NAV_MARK = re.compile(
    r"\b(?:sang|chuyển\s+(?:sang|đến)|tiếp\s+theo\s+(?:là|đến))\s+"
    r"(?:phần|đoạn|cảnh|chương)\s*(?:thứ\s*)?\d+\s*[.:,-]?\s*",
    re.I,
)
_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_LETTERS = re.compile(r"[A-Za-zÀ-ỹ]")
_GENERIC_RECAP = re.compile(
    r"^(?:câu chuyện|diễn biến|mạch truyện|the story|events keep|the narrative)"
    r"\s+(?:tiếp tục|vẫn|keeps|continues|moves)",
    re.I,
)
_FILLER_TAIL = re.compile(
    # Catches filler appended after comma OR as a dangling participle phrase
    r"[,，]?\s*(?:thể hiện|phản ánh|tạo ra|cho thấy|biểu lộ|gợi mở|làm nổi bật"
    r"|khám phá|gợi lên|diễn tả|mô tả|nhấn mạnh)"
    r"\s+(?:sự|mối|thái|cảm|tình|một|những|rằng|rõ|của)\b.*$",
    re.I,
)
_VALID_PURPOSES = {
    "hook", "setup", "rising", "tension", "reveal",
    "climax", "reflection", "bridge", "outro", "body",
}


def write_script(
    story: dict[str, Any],
    *,
    duration_sec: float,
    style: str,
    language: str,
    spoiler: str,
    narration: str = "default",
    notes: str = "",
    genre: str = "",
    visuals: list[dict[str, Any]] | None = None,
    source_transcript: list[dict[str, Any]] | None = None,
    project_id: str | None = None,
    use_llm: bool = False,
    llm_model: str | None = None,
) -> dict[str, Any]:
    graph = story.get("story_graph") or {}
    context = story.get("movie_context") or {}
    hint = STYLES.get(style, STYLES["normal"])
    factor = NARRATION.get(str(narration or "default"), 1.0)
    n = max(6, min(120, int(round(float(duration_sec or 0) / _SEC_PER_LINE * factor))))
    name = _lang_name(language)
    word_budget = max(n * 44, round(float(duration_sec or 0) * 6.0))
    evidence, event_ids, scene_ids = _part_evidence(story, visuals)
    part_position = _part_position(visuals)
    if not use_llm:
        return _translation_script(
            story, n, language,
            source_transcript=source_transcript,
            visuals=visuals,
        )
    extra = ""
    if genre:
        extra += f" Genre: {genre}."
    if notes:
        extra += f" Writer notes: {notes}."
    words_per_seg = word_budget // n
    prompt = (
        f"Write a {duration_sec:.0f}-second movie review voiceover in {name}. "
        f"This is the {part_position} of the source film. Style: {hint}. "
        f"Genre: {genre or 'auto'}.\n"
        "CRITICAL RULES:\n"
        "1. Write FLOWING NARRATION that connects events naturally — NOT isolated scene descriptions.\n"
        "2. NEVER end sentences with abstract labels like 'thể hiện sự X', 'phản ánh Y', 'tạo ra Z', 'cho thấy W'. These are BANNED.\n"
        "3. Use concrete character names, actions, spoken words, and emotions shown through behavior.\n"
        "4. Vary sentence length and rhythm: mix short punchy lines with longer descriptive ones.\n"
        "5. The FIRST segment MUST hook the viewer with a dramatic question or striking image.\n"
        "6. The LAST segment MUST be 'outro' — leave a cliffhanger, question, or emotional resonance.\n"
        "7. 'purpose' MUST be one of: hook, setup, rising, tension, reveal, climax, reflection, bridge, outro.\n"
        f"8. Every segment text MUST be {name} only — never copy source-language subtitles.\n"
        f"9. Never write Scene/Cảnh/chapter numbers. Spoiler mode: {spoiler}.{extra}\n"
        f"10. CRITICAL: Each segment MUST cite a DIFFERENT event from the evidence. "
        f"Do NOT repeat the same event_ref across multiple segments. "
        f"Distribute coverage across ALL {len(event_ids)} available events chronologically.\n"
        f"Output JSON {{segments:[{{id,text,purpose,visual_intent,character_refs,event_refs,preferred_scene_ids}}]}}.\n"
        f"Every segment MUST cite at least one event_refs from {sorted(event_ids)} and only use "
        f"preferred_scene_ids from {sorted(scene_ids)}.\n"
        f"WORD COUNT IS CRITICAL: write EXACTLY {n} segments with AT LEAST {words_per_seg} words each.\n"
        f"Total MUST be at least {word_budget} words. Each segment is read aloud for ~{words_per_seg//8}s of screen time.\n"
        f"Global context: {context.get('logline','')}\n"
        f"PART EVIDENCE:\n{evidence}"
    )
    min_words = max(60, round(word_budget * 0.45))

    def _log(msg: str) -> None:
        if not project_id:
            return
        try:
            from pipeline.review.run import _note as _run_note
            _run_note(project_id, msg)
        except Exception:
            try:
                from pipeline.core.app_log import append_log
                append_log(msg)
            except Exception:
                pass

    _log(f"LLM đang viết kịch bản · {n} đoạn · ~{word_budget} từ · {llm_model or 'auto'}…")
    parsed = generate_json(prompt, model=llm_model)
    if not _script_is_usable(parsed, min_words, event_ids, scene_ids):
        _log(f"LLM thử lại (lần 2) · cần tối thiểu {min_words} từ…")
        parsed = generate_json(
            prompt
            + f"\nRETURN VALID JSON NOW. Requirements: {n} segments, "
            f"MINIMUM {min_words} words total, at least {words_per_seg} words per segment, "
            f"valid event_refs, no generic filler phrases.",
            model=llm_model,
        )
    if not _script_is_usable(parsed, min_words, event_ids, scene_ids):
        _log("LLM không tạo được kịch bản hợp lệ — REVIEW_LLM_EVIDENCE_INVALID")
        raise RuntimeError("REVIEW_LLM_EVIDENCE_INVALID")
    clean = _clean_segments(parsed["segments"], language, event_ids, scene_ids)
    if len(clean) < 3:
        raise RuntimeError("REVIEW_LLM_EVIDENCE_INVALID")
    return {"segments": clean[:n], "language": language, "style": style, "spoiler": spoiler}

def _part_position(visuals: list[dict[str, Any]] | None) -> str:
    if not visuals:
        return "selected section"
    start = min(float(scene.get("start") or 0) for scene in visuals)
    return "opening section" if start < 5 else "middle or later section"


def _script_is_usable(
    parsed: Any, min_words: int, event_ids: set[str], scene_ids: set[int],
) -> bool:
    """Require enough evidence-backed text before sending it to TTS."""
    if not isinstance(parsed, dict):
        return False
    segments = parsed.get("segments")
    if not isinstance(segments, list) or len(segments) < 3:
        return False
    words = sum(
        len(str(segment.get("text") or "").split())
        for segment in segments
        if isinstance(segment, dict)
    )
    if words < min_words:
        return False
    for segment in segments:
        if not isinstance(segment, dict):
            return False
        refs = {str(ref) for ref in (segment.get("event_refs") or [])}
        preferred = {
            int(ref) for ref in (segment.get("preferred_scene_ids") or [])
            if str(ref).isdigit() or isinstance(ref, int)
        }
        text = str(segment.get("text") or "").strip()
        if (
            not refs or not refs <= event_ids or not preferred or not preferred <= scene_ids
            or not text or _GENERIC_RECAP.search(text)
        ):
            return False
    return True


def _part_evidence(
    story: dict[str, Any], visuals: list[dict[str, Any]] | None,
) -> tuple[str, set[str], set[int]]:
    """Return chronological evidence and the IDs allowed in LLM output."""
    events = list((story.get("story_graph") or {}).get("events") or [])
    if events:
        rows = []
        event_ids: set[str] = set()
        scene_ids: set[int] = set()
        for event in events:
            event_id = str(event.get("event_id") or "")
            ids = [int(scene_id) for scene_id in (event.get("scene_ids") or []) if str(scene_id).isdigit() or isinstance(scene_id, int)]
            if not event_id or not ids:
                continue
            event_ids.add(event_id)
            scene_ids.update(ids)
            rows.append(
                f"{event_id} {float(event.get('start') or 0):.1f}-{float(event.get('end') or 0):.1f} "
                f"scenes={ids}: {str(event.get('summary') or '')[:400]}"
            )
        if rows:
            return "\n".join(rows)[:6500], event_ids, scene_ids
    rows = list(visuals or [])
    if not rows:
        return "(No part evidence available.)", set(), set()
    facts: list[str] = []
    event_ids = set()
    scene_ids = set()
    for index, scene in enumerate(rows):
        text = str(scene.get("description") or scene.get("transcript") or "").strip()
        if not text:
            continue
        start = int(float(scene.get("start") or 0))
        end = int(float(scene.get("end") or start))
        event_id = f"evt_{index:03d}"
        scene_id = int(scene.get("scene_id") or index)
        event_ids.add(event_id)
        scene_ids.add(scene_id)
        facts.append(f"{event_id} {start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d} scenes=[{scene_id}] {text[:180]}")
    return "\n".join(facts)[:6500] or "(No usable part evidence available.)", event_ids, scene_ids


def _translation_script(
    story: dict[str, Any],
    n: int,
    language: str,
    *,
    source_transcript: list[dict[str, Any]] | None,
    visuals: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Translate chronological source evidence without trying to recap it."""
    transcript_rows = [
        row for row in (source_transcript or [])
        if str(row.get("text") or "").strip()
    ]
    events = list((story.get("story_graph") or {}).get("events") or [])
    source_rows: list[dict[str, Any]]
    if transcript_rows:
        source_rows = [
            {
                "event_id": f"src_{index:05d}",
                "summary": str(row.get("text") or "").strip(),
                "start": float(row.get("start") or 0),
                "end": float(row.get("end") or row.get("start") or 0),
                "scene_ids": _overlapping_scene_ids(row, visuals),
            }
            for index, row in enumerate(transcript_rows)
        ]
    else:
        source_rows = [
            event for event in events
            if str(event.get("summary") or "").strip() and (event.get("scene_ids") or [])
        ]
    texts = [str(row.get("summary") or "").strip() for row in source_rows]
    translated = _translate_beats(texts, language)
    segments: list[dict[str, Any]] = []
    for index, (event, text) in enumerate(zip(source_rows, translated)):
        for sentence in _sentence_units(text):
            clean = _finalize_line(sentence, language)
            if not clean:
                continue
            segments.append(_voice_item(index, clean, {
                "event_refs": [str(event.get("event_id") or f"evt_{index:03d}")],
                "preferred_scene_ids": list(event.get("scene_ids") or []),
            }))
            if len(segments) >= n:
                break
        if len(segments) >= n:
            break
    if not segments:
        raise RuntimeError("REVIEW_TRANSLATION_EMPTY")
    return {"segments": segments, "language": language, "style": "translate", "spoiler": "full"}


def _overlapping_scene_ids(
    row: dict[str, Any], visuals: list[dict[str, Any]] | None,
) -> list[int]:
    start = float(row.get("start") or 0)
    end = float(row.get("end") or start)
    ids = [
        int(scene.get("scene_id"))
        for scene in (visuals or [])
        if scene.get("scene_id") is not None
        and float(scene.get("end") or 0) >= start
        and float(scene.get("start") or 0) <= end
    ]
    return ids[:8]


def scrub_script(script: dict[str, Any], language: str) -> dict[str, Any] | None:
    """Drop cached lines that are scene labels / leftover source script."""
    segs = _clean_segments(script.get("segments") or [], language)
    if len(segs) < 3:
        return None
    orig = len(script.get("segments") or [])
    if orig >= 8 and len(segs) < orig // 2:
        return None
    return {**script, "segments": segs}


def _in_voiceover_lang(text: str, language: str) -> bool:
    """True when vi/en line has no leftover CJK/Hangul/kana."""
    if language not in {"vi", "en"}:
        return True
    return len(_CJK.findall(text or "")) == 0


def _strip_scene_marks(text: str) -> str:
    t = _NAV_MARK.sub(" ", text or "")
    t = _SCENE_MARK.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,;:/-")
    return t


def _finalize_line(text: str, language: str) -> str:
    t = _strip_scene_marks(text)
    if not t:
        return ""
    if language in {"vi", "en"}:
        t = _clean_burn_text(t, target_lang="vi" if language == "vi" else "en")
        t = _strip_scene_marks(t)
        # Strip generic filler tails: "…, thể hiện sự X" → "…."
        t = _FILLER_TAIL.sub(".", t)
        t = _CJK.sub(" ", t)
        t = re.sub(r"\s+", " ", t).strip(" .,;:/-")
        if len(t) < 18 or len(_LETTERS.findall(t)) < 10:
            return ""
        if not _in_voiceover_lang(t, language):
            return ""
    return t


def _sentence_units(text: str) -> list[str]:
    return [
        bit.strip(" \t\r\n,;:.-")
        for bit in re.split(r"(?<=[.!?。！？])\s*|[\n;；]+", text or "")
        if bit.strip(" \t\r\n,;:.-")
    ]


def _text_key(text: str) -> str:
    return " ".join(re.findall(r"[0-9A-Za-zÀ-ỹ]+", text.lower()))


def _dedupe_text(text: str, seen: set[str] | None = None) -> str:
    """Drop repeated dialogue sentences inside and across voice segments."""
    local: set[str] = set()
    kept: list[str] = []
    for sentence in _sentence_units(text):
        key = _text_key(sentence)
        if len(key) < 6 or key in local or (seen is not None and key in seen):
            continue
        local.add(key)
        if seen is not None:
            seen.add(key)
        kept.append(sentence.rstrip(".!?。！？") + ".")
    return " ".join(kept).strip()


def _fallback_script(
    story: dict[str, Any],
    n: int,
    language: str,
    spoiler: str,
    visuals: list[dict[str, Any]] | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    raw = _story_beats(story, spoiler)
    if visuals:
        raw.extend(_visual_texts(visuals, max(n * 2, 24)))
    # Faithful story exposes the same transcript as blocks, chapters and
    # events. Deduplicate before translation/packing, otherwise differently
    # packed repeats survive later text cleanup and become repeated narration.
    source: list[str] = []
    source_seen: set[str] = set()
    for sentence in _source_sentences(raw):
        key = _text_key(sentence)
        if key and key not in source_seen:
            source_seen.add(key)
            source.append(sentence)
    packed = _pack_sentences(source, n)
    translated = _translate_beats(packed, language, project_id=project_id)
    # Translation expands compact CJK text several-fold. Re-split after
    # translation so one voice item never becomes a wall of copied dialogue.
    translated_sentences = _source_sentences(translated)
    unique_sentences: list[str] = []
    seen: set[str] = set()
    for sentence in translated_sentences:
        cleaned = _dedupe_text(sentence, seen)
        if cleaned:
            unique_sentences.extend(_sentence_units(cleaned))
    repacked = _pack_sentences(unique_sentences, n, width=55)
    lines = [_finalize_line(t, language) for t in repacked]
    lines = [_dedupe_text(t) for t in lines if t]
    lines = [t for t in lines if t]
    return [_voice_item(i, text) for i, text in enumerate(lines[:n])]


def _story_beats(story: dict[str, Any], spoiler: str) -> list[str]:
    out: list[str] = []
    logline = str((story.get("movie_context") or {}).get("logline") or "").strip()
    if logline:
        out.append(logline)
    for key in ("chapters", "blocks"):
        for item in story.get(key) or []:
            summary = str(item.get("summary") or "").strip()
            if summary:
                out.append(summary)
    for ev in (story.get("story_graph") or {}).get("events") or []:
        if spoiler == "none" and float(ev.get("spoiler_level") or 0) > 0.6:
            continue
        summary = str(ev.get("summary") or "").strip()
        if summary:
            out.append(summary)
    return out


def _visual_texts(visuals: list[dict[str, Any]], n: int) -> list[str]:
    if not visuals:
        return []
    step = max(1, len(visuals) // max(n, 1))
    out: list[str] = []
    for scene in visuals[::step]:
        text = str(scene.get("transcript") or scene.get("description") or "").strip()
        text = _strip_scene_marks(text)
        if text:
            out.append(text[:240])
        if len(out) >= n:
            break
    return out


def _source_sentences(texts: list[str]) -> list[str]:
    out: list[str] = []
    for raw in texts:
        t = _strip_scene_marks(raw)
        if not t:
            continue
        for bit in re.split(r"(?<=[。！？.!?])\s*|\n+", t):
            bit = _strip_scene_marks(bit)
            if len(bit) >= 6:
                out.append(bit)
    return out


def _pack_sentences(sents: list[str], n: int, width: int = 80) -> list[str]:
    if not sents:
        return []
    chunks: list[str] = []
    buf = ""
    limit = max(40, width * 2)
    for sent in sents:
        cand = (buf + " " + sent).strip() if buf else sent
        if len(cand) <= limit:
            buf = cand
            continue
        if buf:
            chunks.append(buf)
        buf = sent if len(sent) <= limit else sent[:limit]
        if len(chunks) >= n:
            return chunks[:n]
    if buf:
        chunks.append(buf)
    return chunks[:n]


def _translate_beats(texts: list[str], language: str, project_id: str | None = None) -> list[str]:
    if language not in {"vi", "en"} or not texts:
        return list(texts)
    need = [i for i, t in enumerate(texts) if not _finalize_line(t, language)]
    if not need:
        return [_finalize_line(t, language) or t for t in texts]
    try:
        from pipeline.mt.free import translate_google_free

        mapped = translate_google_free([texts[i] for i in need], language, project_id=project_id)
        out = list(texts)
        for i, tr in zip(need, mapped):
            out[i] = str(tr or "").strip() or out[i]
        return out
    except Exception:
        return [t for t in texts if _finalize_line(t, language)]


def _pad_lines(lines: list[str], n: int, language: str) -> list[str]:
    pads = _PAD_VARIANTS_EN if language == "en" else _PAD_VARIANTS_VI
    out = list(lines)
    i = 0
    while len(out) < n:
        out.append(pads[i % len(pads)])
        i += 1
    return out


def _clean_segments(
    segs: list[Any],
    language: str,
    event_ids: set[str] | None = None,
    scene_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, seg in enumerate(segs):
        if not isinstance(seg, dict):
            continue
        text = _finalize_line(str(seg.get("text") or ""), language)
        text = _dedupe_text(text, seen)
        if not text or _GENERIC_RECAP.search(text):
            continue
        refs = {str(ref) for ref in (seg.get("event_refs") or [])}
        preferred = {
            int(ref) for ref in (seg.get("preferred_scene_ids") or [])
            if str(ref).isdigit() or isinstance(ref, int)
        }
        if event_ids is not None and (not refs or not refs <= event_ids):
            continue
        if scene_ids is not None and (not preferred or not preferred <= scene_ids):
            continue
        clean.append(_voice_item(i, text, seg))
    return clean


def _voice_item(i: int, text: str, seg: dict[str, Any] | None = None) -> dict[str, Any]:
    row = seg or {}
    purpose = str(row.get("purpose") or ("hook" if i == 0 else "body"))
    if purpose not in _VALID_PURPOSES:
        purpose = "hook" if i == 0 else "body"
    return {
        "id": str(row.get("id") or f"voice_{i+1:03d}"),
        "text": text,
        "purpose": purpose,
        "visual_intent": str(row.get("visual_intent") or ""),
        "character_refs": list(row.get("character_refs") or []),
        "event_refs": list(row.get("event_refs") or []),
        "preferred_scene_ids": [
            int(x) for x in (row.get("preferred_scene_ids") or [])
            if str(x).isdigit() or isinstance(x, int)
        ],
    }
