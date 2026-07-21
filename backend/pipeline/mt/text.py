"""Machine translation backends — text."""
from __future__ import annotations

"""MT: Ollama + Google free fallback."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from pipeline.core.jobs import check_cancel
from pipeline.core.project import set_status



def _report_mt(
    project_id: str | None,
    *,
    label: str,
    cur: int,
    total: int,
    last_t: list[float],
    force: bool = False,
    workers: int | None = None,
) -> None:
    """Heartbeat dịch — cập nhật message ≥1.2s; luôn hiện số luồng khi auto."""
    import time

    if not project_id or total <= 0:
        return
    now = time.monotonic()
    if not force and cur not in (1, total) and now - last_t[0] < 1.2:
        return
    last_t[0] = now
    from pipeline.core.resources import progress_msg

    set_status(
        project_id,
        step="translate",
        progress=55 + int(35 * cur / max(1, total)),
        message=progress_msg(f"Dịch {label}", cur, total, workers=workers),
        running=True,
    )


def _parse_numbered_batch(raw: str, n: int, sources: list[str]) -> list[str] | None:
    """Parse '1. ...\\n2. ...' → n dòng. Fail → None (caller fallback 1-by-1)."""
    import re

    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if not lines:
        return None
    # Bỏ header kiểu "Here are the translations:"
    while lines and not re.match(r"^\d+[\.\)\:\-]\s*", lines[0]):
        lines = lines[1:]
    by_i: dict[int, str] = {}
    for ln in lines:
        m = re.match(r"^(\d+)[\.\)\:\-]\s*(.+)$", ln)
        if not m:
            continue
        idx = int(m.group(1))
        if 1 <= idx <= n:
            by_i[idx] = m.group(2).strip().strip("「」\"'“”")
    if len(by_i) < max(1, int(n * 0.6)):
        return None
    out: list[str] = []
    for i in range(1, n + 1):
        out.append(by_i.get(i) or sources[i - 1])
    return out


def _same_lang_too_rewritten(src: str, out: str) -> bool:
    """Cùng ngôn ngữ: cho sửa OCR nhẹ, chặn paraphrase."""
    from difflib import SequenceMatcher

    a = "".join(src.split())
    b = "".join(out.split())
    if not a or not b:
        return True
    if a == b:
        return False
    if abs(len(b) - len(a)) > max(2, len(a) // 4):
        return True
    changed = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    return changed > max(2, len(a) // 4)


def _wrong_script_for_target(text: str, *, target_lang: str) -> bool:
    """True nếu bản dịch lệch script so với ngôn ngữ đích (vd. Cyrillic khi target=vi)."""
    import re

    t = text or ""
    if not t or target_lang != "vi":
        return False
    # vi = Latin+dấu; Cyrillic/Hangul/Arabic/kana = model lạc ngôn ngữ
    n = len(
        re.findall(
            r"[\u0400-\u04FF\u0500-\u052F\uAC00-\uD7AF\u0600-\u06FF\u3040-\u30FF]",
            t,
        )
    )
    return n >= 3


def _strip_cjk_punct(text: str) -> str:
    """Chuẩn hóa dấu câu CJK → ASCII; bỏ ngoặc/chấm thừa.

    Google/MyMemory hay trả · / 、 thay phẩy (TikTok trả ,) — giữ separator.
    """
    import re

    t = text or ""
    # separator nhãn / list: · ・ 、 ， → ", "
    t = re.sub(r"\s*[・·、，]+\s*", ", ", t)
    # fullwidth / CJK punctuation còn lại
    t = re.sub(r"[。．；：！？…‥「」『』（）【】〔〕《》〈〉]", "", t)
    # gộp ", ," / trailing comma
    t = re.sub(r"(,\s*){2,}", ", ", t)
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r"\s*[.,;:!?]+$", "", t)
    t = re.sub(r"^[.,;:!?]+\s*", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def _clean_burn_text(text: str, *, target_lang: str = "vi") -> str:
    """Bỏ dòng bẩn (prompt/instruction / LLM từ chối dịch) trước khi đè."""
    import re

    t = (text or "").strip()
    if not t:
        return ""
    bad = (
        "指令",
        "似乎是",
        "只输出",
        "禁止任何",
        "任务：",
        "return only",
        "one line only",
        "do not",
        "note:",
        "翻译这段",
        "không thể dịch",
        "không dịch được",
        "không phải là một câu",
        "cannot be translated",
        "cannot translate",
        "i cannot",
        "i'm unable",
        "as an ai",
        "not a valid",
    )
    low = t.lower()
    if any(b in t or b in low for b in bad):
        return ""
    if _wrong_script_for_target(t, target_lang=target_lang):
        return ""
    # target vi: bỏ Hán còn sót (vd. "Có thịt ăn了") + từ chối dòng gần như toàn CJK
    if target_lang == "vi":
        mixed = re.sub(r"[\u4e00-\u9fff]", "", t)
        mixed = re.sub(r"\s+", " ", mixed).strip()
        if mixed and mixed != t:
            t = mixed
        cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
        if cjk >= max(4, len(t) // 3):
            return ""
        t = _strip_cjk_punct(t)
    elif target_lang in ("en", "none", ""):
        t = _strip_cjk_punct(t)
    return t


def _needs_google_fallback(source: str, translation: str, *, target_lang: str) -> bool:
    """True khi bản dịch là meta/từ chối/sai ngôn ngữ hoặc rỗng — cần Google."""
    src = (source or "").strip()
    tr = (translation or "").strip()
    if not tr:
        return bool(src)
    if _wrong_script_for_target(tr, target_lang=target_lang):
        return True
    if not _clean_burn_text(tr, target_lang=target_lang):
        return True
    # câu OCR ngắn mà model viết cả đoạn giải thích
    if len(src) <= 24 and len(tr) > max(60, len(src) * 5):
        low = tr.lower()
        if any(w in low for w in ("dịch", "ngôn ngữ", "translate", "language", "tiếng việt")):
            return True
    return False


def _mt_lang_code(code: str | None, *, for_mymemory: bool = False) -> str:
    c = (code or "auto").strip().lower().split("-")[0]
    if c in ("", "auto", "none", "off", "source"):
        return "auto"
    if for_mymemory and c == "zh":
        return "zh-CN"
    return c


def _lang_name(code: str) -> str:
    return {
        "vi": "Vietnamese",
        "en": "English",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
    }.get(code, code or "the target language")


__all__ = [
    '_report_mt',
    '_parse_numbered_batch',
    '_same_lang_too_rewritten',
    '_wrong_script_for_target',
    '_strip_cjk_punct',
    '_clean_burn_text',
    '_needs_google_fallback',
    '_mt_lang_code',
    '_lang_name',
]
