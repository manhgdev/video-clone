"""MT: Ollama + Google free fallback."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .core.jobs import check_cancel
from .core.project import set_status

def _ollama_model(models: list[str], *, prefer_fast: bool = True) -> str:
    """prefer_fast: ưu tiên 3–14B (dịch phụ đề). 32B quá chậm; 1B dễ hỏng."""
    import re

    def size_b(name: str) -> float:
        m = re.search(r"(\d+(?:\.\d+)?)[bB]", name)
        return float(m.group(1)) if m else 99.0

    def score(name: str) -> tuple[int, float, float]:
        n = name.lower()
        fam = 0 if "qwen" in n else 1 if "mistral" in n else 2 if "llama" in n else 9
        sb = size_b(n)
        if prefer_fast:
            # 3–14B sweet spot; 1.5–3; 14–20; rồi 32B+ / tiny
            if 3.0 <= sb <= 14.0:
                tier = 0
            elif 1.5 <= sb < 3.0:
                tier = 1
            elif 14.0 < sb <= 20.0:
                tier = 2
            elif sb > 20.0:
                tier = 3
            else:
                tier = 4  # ≤1.5B
            return (tier, fam, sb)
        return (fam, 0.0, -sb)

    return sorted(models, key=score)[0]


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


def translate_ollama(
    texts: list[str],
    target_lang: str,
    project_id: str | None = None,
    *,
    source_lang: str = "auto",
    batch_size: int = 12,
    workers: int = 2,
) -> list[str]:
    """Dịch batch Ollama — nhiều batch song song theo `workers`."""
    names = {
        "vi": "Vietnamese",
        "en": "English",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
    }
    lang_name = names.get(target_lang, target_lang)
    src = source_lang if source_lang not in ("", "auto") else "auto"
    same_lang = src != "auto" and src == target_lang
    out: list[str] = [""] * len(texts)
    if not texts:
        return out
    try:
        with httpx.Client(timeout=300.0, trust_env=False) as client:
            tags = client.get("http://127.0.0.1:11434/api/tags")
            tags.raise_for_status()
            models = [m["name"] for m in tags.json().get("models", [])]
            if not models:
                raise RuntimeError("Ollama không có model. Chạy: ollama pull qwen2.5")
            model = _ollama_model(models, prefer_fast=True)
            # 32B + nhiều request song song → thrash GPU, chậm hơn tuần tự
            size_m = __import__("re").search(r"(\d+(?:\.\d+)?)[bB]", model.lower())
            size_b = float(size_m.group(1)) if size_m else 7.0
            total = max(1, len(texts))
            # Batch vừa: 32B → batch nhỏ; model vừa → batch lớn hơn
            if size_b >= 20:
                bs = max(1, min(8, int(batch_size or 8)))
                w = 1  # 1 request GPU tại 1 thời điểm
            elif size_b >= 10:
                bs = max(1, min(12, int(batch_size or 12)))
                w = max(1, min(2, int(workers or 2)))
            else:
                bs = max(1, min(16, int(batch_size or 12)))
                w = max(1, min(4, int(workers or 2)))
            starts = list(range(0, len(texts), bs))
            w = max(1, min(w, len(starts)))
            done = 0
            done_lock = __import__("threading").Lock()
            if project_id:
                set_status(
                    project_id,
                    step="translate",
                    progress=55,
                    message=f"Dịch Ollama {model} · batch {bs} · {w} luồng…",
                    running=True,
                )

            def _gen_opts(n_lines: int) -> dict:
                # ctx nhỏ + predict gọn = nhanh hơn nhiều với phụ đề
                return {
                    "temperature": 0.0,
                    "num_predict": min(512, 28 * n_lines + 32),
                    "num_ctx": min(2048, 128 + 64 * n_lines),
                }

            def _one_line(text: str) -> str:
                if same_lang:
                    one_prompt = (
                        f"Proofread this {lang_name} OCR subtitle. "
                        "One line only, no notes.\n\n"
                        f"{text}"
                    )
                else:
                    one_prompt = (
                        f"Translate into {lang_name}. Return ONLY the translation.\n\n"
                        f"{text}"
                    )
                with httpx.Client(timeout=180.0, trust_env=False) as c:
                    rr = c.post(
                        "http://127.0.0.1:11434/api/generate",
                        json={
                            "model": model,
                            "prompt": one_prompt,
                            "stream": False,
                            "keep_alive": "30m",
                            "options": {
                                "temperature": 0.0,
                                "num_predict": 64,
                                "num_ctx": 512,
                            },
                        },
                    )
                    rr.raise_for_status()
                    one_raw = (rr.json().get("response") or "").strip()
                for ln in one_raw.splitlines():
                    s = ln.strip().strip("「」\"'“”")
                    if s and not s.lower().startswith("note"):
                        return s
                return text

            def _batch(start: int) -> tuple[int, list[str]]:
                check_cancel(project_id)
                chunk = texts[start : start + bs]
                n = len(chunk)
                numbered = "\n".join(f"{j + 1}. {t}" for j, t in enumerate(chunk))
                if same_lang:
                    if target_lang == "zh":
                        prompt = (
                            "你是中文字幕OCR校对器。\n"
                            f"下面有 {n} 行字幕，编号 1..{n}。\n"
                            "只修正明显错别字/漏字/多余空格，不要改写，不要加说明。\n"
                            f"输出恰好 {n} 行，格式：1. 校对后\\n2. ...\n\n"
                            f"{numbered}"
                        )
                    else:
                        prompt = (
                            f"Proofread these {n} {lang_name} OCR subtitles. "
                            "Fix clear errors only; do not paraphrase. "
                            f"Return exactly {n} numbered lines: 1. ... 2. ...\n\n"
                            f"{numbered}"
                        )
                else:
                    src_name = (
                        names.get(src, "the source language")
                        if src != "auto"
                        else "the source language"
                    )
                    prompt = (
                        f"Translate these {n} video subtitles from {src_name} into {lang_name}. "
                        "Be faithful. Do not invent content. "
                        f"Return exactly {n} numbered lines only, format: 1. translation\\n2. ...\n\n"
                        f"{numbered}"
                    )
                with httpx.Client(timeout=300.0, trust_env=False) as c:
                    r = c.post(
                        "http://127.0.0.1:11434/api/generate",
                        json={
                            "model": model,
                            "prompt": prompt,
                            "stream": False,
                            "keep_alive": "30m",
                            "options": _gen_opts(n),
                        },
                    )
                    r.raise_for_status()
                    raw = (r.json().get("response") or "").strip()
                    parsed = _parse_numbered_batch(raw, n, chunk)
                    if parsed is None:
                        # Fallback 1-by-1 tuần tự (tránh 16×32B cùng lúc)
                        parsed = []
                        for text in chunk:
                            check_cancel(project_id)
                            parsed.append(_one_line(text))
                    cleaned: list[str] = []
                    for text, line in zip(chunk, parsed):
                        if same_lang and line and _same_lang_too_rewritten(text, line):
                            line = text
                        line = _clean_burn_text(line, target_lang=target_lang) or (
                            text
                            if same_lang
                            else _clean_burn_text(text, target_lang=target_lang) or text
                        )
                        cleaned.append(line or text)
                    return start, cleaned

            with ThreadPoolExecutor(max_workers=w, thread_name_prefix="ollama") as pool:
                futs = {pool.submit(_batch, s): s for s in starts}
                for fut in as_completed(futs):
                    check_cancel(project_id)
                    start, lines = fut.result()
                    for j, line in enumerate(lines):
                        out[start + j] = line
                    with done_lock:
                        done += len(lines)
                        cur = done
                    if project_id:
                        pct = 55 + int(20 * cur / total)
                        set_status(
                            project_id,
                            step="translate",
                            progress=pct,
                            message=f"Dịch {model} {cur}/{total}",
                            running=True,
                        )
    except (httpx.HTTPError, RuntimeError) as e:
        raise RuntimeError(
            f"Ollama lỗi ({e}). Chạy `ollama serve` rồi `ollama pull qwen2.5`."
        ) from e
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


def translate_google_free(
    texts: list[str],
    target_lang: str,
    source_lang: str = "auto",
    *,
    workers: int = 8,
    project_id: str | None = None,
) -> list[str]:
    """Google Translate không key (client=gtx) — song song."""
    import threading

    sl = "auto" if source_lang in ("", "auto", None) else source_lang
    tl = target_lang or "vi"
    n = len(texts)
    out: list[str] = [""] * n
    if n == 0:
        return out

    def _one(i: int, text: str) -> tuple[int, str]:
        q = (text or "").strip()
        if not q:
            return i, ""
        with httpx.Client(timeout=30.0, trust_env=False) as client:
            r = client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": q},
            )
            r.raise_for_status()
            data = r.json()
            parts: list[str] = []
            for chunk in data[0] or []:
                if chunk and chunk[0]:
                    parts.append(str(chunk[0]))
            return i, "".join(parts).strip() or q

    w = max(1, min(16, int(workers or 8), n))
    done = 0
    done_lock = threading.Lock()
    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=55,
            message=f"Dịch Google {n} đoạn ({w} luồng)…",
            running=True,
        )
    with ThreadPoolExecutor(max_workers=w, thread_name_prefix="gtx") as pool:
        futs = [pool.submit(_one, i, t) for i, t in enumerate(texts)]
        for fut in as_completed(futs):
            check_cancel(project_id)
            i, tr = fut.result()
            out[i] = tr
            with done_lock:
                done += 1
                cur = done
            if project_id and (done % max(1, n // 10) == 0 or done == n):
                set_status(
                    project_id,
                    step="translate",
                    progress=55 + int(35 * cur / max(1, n)),
                    message=f"Dịch Google {cur}/{n}",
                    running=True,
                )
    return out


def translate_mymemory(
    texts: list[str],
    target_lang: str,
    source_lang: str = "auto",
    *,
    workers: int = 6,
    project_id: str | None = None,
) -> list[str]:
    """MyMemory free API — không key (giới hạn quota IP)."""
    import threading

    sl = _mt_lang_code(source_lang, for_mymemory=True)
    tl = _mt_lang_code(target_lang, for_mymemory=True) or "vi"
    if sl == "auto":
        # MyMemory cần langpair; auto → đoán theo script hoặc en
        sl = "zh-CN" if any(
            "\u4e00" <= ch <= "\u9fff" for t in texts for ch in (t or "")[:8]
        ) else "en"
    n = len(texts)
    out: list[str] = [""] * n
    if n == 0:
        return out
    pair = f"{sl}|{tl}"

    def _one(i: int, text: str) -> tuple[int, str]:
        q = (text or "").strip()
        if not q:
            return i, ""
        with httpx.Client(timeout=30.0, trust_env=False) as client:
            r = client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": q, "langpair": pair},
            )
            r.raise_for_status()
            data = r.json()
            tr = str((data.get("responseData") or {}).get("translatedText") or "").strip()
            # API đôi khi trả lại nguyên câu + note lỗi
            if not tr or tr.upper().startswith("MYMEMORY WARNING"):
                return i, q
            return i, tr

    w = max(1, min(12, int(workers or 6), n))
    done = 0
    done_lock = threading.Lock()
    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=55,
            message=f"Dịch MyMemory {n} đoạn…",
            running=True,
        )
    with ThreadPoolExecutor(max_workers=w, thread_name_prefix="mymem") as pool:
        futs = [pool.submit(_one, i, t) for i, t in enumerate(texts)]
        for fut in as_completed(futs):
            check_cancel(project_id)
            i, tr = fut.result()
            out[i] = tr
            with done_lock:
                done += 1
                cur = done
            if project_id and (done % max(1, n // 10) == 0 or done == n):
                set_status(
                    project_id,
                    step="translate",
                    progress=55 + int(35 * cur / max(1, n)),
                    message=f"Dịch MyMemory {cur}/{n}",
                    running=True,
                )
    return out


def translate_tiktok(
    texts: list[str],
    target_lang: str,
    source_lang: str = "auto",
    *,
    workers: int = 4,
    project_id: str | None = None,
) -> list[str]:
    """TikTok content translation endpoint (free, no key)."""
    import threading

    tl = _mt_lang_code(target_lang) or "vi"
    n = len(texts)
    out: list[str] = [""] * n
    if n == 0:
        return out

    def _one(i: int, text: str) -> tuple[int, str]:
        q = (text or "").strip()
        if not q:
            return i, ""
        params = {
            "content": q,
            "scene": "1",
            "trg_lang": tl,
            "aid": "1233",
            "device_id": "7351199229782427141",
            "app_name": "musical_ly",
            "version_code": "19.3.0",
            "language": tl if tl != "auto" else "vi",
            "app_language": tl if tl != "auto" else "vi",
            "locale": f"{tl}-VN" if tl != "auto" else "vi-VN",
            "device_platform": "iphone",
            "device_type": "iPhone8,1",
            "os_version": "15.7.5",
            "channel": "App Store",
            "build_number": "193021",
            "iid": "7351201073010951941",
        }
        headers = {
            "User-Agent": "TikTok 19.3.0 rv:193021 (iPhone; iOS 15.7.5; vi_VN) Cronet",
            "sdk-version": "2",
            "Host": "api16-normal-c-alisg.tiktokv.com",
        }
        with httpx.Client(timeout=30.0, trust_env=False) as client:
            r = client.get(
                "https://api16-normal-c-alisg.tiktokv.com/aweme/v1/content/translation/",
                params=params,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            if int(data.get("status_code") or 0) != 0:
                raise RuntimeError(data.get("status_msg") or "TikTok translate failed")
            tr = str(data.get("translated_content") or "").strip()
            return i, tr or q

    # TikTok dễ rate-limit — worker vừa phải
    w = max(1, min(6, int(workers or 4), n))
    done = 0
    done_lock = threading.Lock()
    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=55,
            message=f"Dịch TikTok {n} đoạn…",
            running=True,
        )
    with ThreadPoolExecutor(max_workers=w, thread_name_prefix="tt-mt") as pool:
        futs = [pool.submit(_one, i, t) for i, t in enumerate(texts)]
        for fut in as_completed(futs):
            check_cancel(project_id)
            i, tr = fut.result()
            out[i] = tr
            with done_lock:
                done += 1
                cur = done
            if project_id and (done % max(1, n // 10) == 0 or done == n):
                set_status(
                    project_id,
                    step="translate",
                    progress=55 + int(35 * cur / max(1, n)),
                    message=f"Dịch TikTok {cur}/{n}",
                    running=True,
                )
    return out


def _lang_name(code: str) -> str:
    return {
        "vi": "Vietnamese",
        "en": "English",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
    }.get(code, code or "the target language")


def _cloud_batch_prompt(
    chunk: list[str], *, target_lang: str, source_lang: str
) -> str:
    n = len(chunk)
    numbered = "\n".join(f"{j + 1}. {t}" for j, t in enumerate(chunk))
    src = source_lang if source_lang not in ("", "auto") else "auto"
    src_name = _lang_name(src) if src != "auto" else "the source language"
    return (
        f"Translate these {n} video subtitles from {src_name} into {_lang_name(target_lang)}. "
        "Be faithful. Do not invent content. "
        f"Return exactly {n} numbered lines only, format: 1. translation\\n2. ...\n\n"
        f"{numbered}"
    )


def _estimate_tokens(text: str) -> int:
    """Token estimate: CJK=1 char/token, mixed≈1.5, ASCII≈4."""
    cjk = sum(1 for c in text if "一" <= c <= "鿿" or "぀" <= c <= "ヿ")
    latin = len(text) - cjk
    return cjk + latin // 3 + 1


def _openai_compatible_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 120.0,
    max_output_tokens: int = 512,
    max_input_tokens: int = 200_000,
) -> str:
    """Cap input+output ≤ max_input_tokens; raise ValueError if prompt too big."""
    system_msg = "You translate video subtitles. Output numbered lines only."
    in_est = _estimate_tokens(prompt) + _estimate_tokens(system_msg)
    total_est = in_est + max_output_tokens
    if total_est > max_input_tokens:
        raise ValueError(
            f"Prompt ~{in_est} tokens + output {max_output_tokens} > "
            f"limit {max_input_tokens}. Retry with smaller batch."
        )
    url = base_url.rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        r = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://localhost/videoclone",
                "X-Title": "VideoClone",
            },
            json={
                "model": model,
                "temperature": 0,
                "max_tokens": max_output_tokens,
                "messages": [
                    {
                        "role": "system",
                        "content": system_msg,
                    },
                    {"role": "user", "content": prompt},
                ],
            },
        )
        r.raise_for_status()
        data = r.json()
        return (
            (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
            or ""
        ).strip()


def _gemini_generate(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 120.0,
) -> str:
    # base: .../v1beta  →  .../v1beta/models/{model}:generateContent
    root = base_url.rstrip("/")
    if not root.endswith("v1beta") and "/models" not in root:
        root = root + "/v1beta"
    url = f"{root}/models/{model}:generateContent"
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        r = client.post(
            url,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 2048},
            },
        )
        r.raise_for_status()
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            return ""
        parts = ((cands[0].get("content") or {}).get("parts")) or []
        return "".join(str(p.get("text") or "") for p in parts).strip()


def translate_cloud(
    texts: list[str],
    target_lang: str,
    provider: str,
    project_id: str | None = None,
    *,
    source_lang: str = "auto",
    batch_size: int = 8,
    workers: int = 2,
) -> list[str]:
    """OpenAI / DeepSeek / OpenRouter (chat) hoặc Gemini generateContent."""
    from .core.app_config import provider_credentials

    pid = (provider or "").lower().strip()
    if pid == "9router":
        pid = "openrouter"
    if pid == "xai":
        pid = "grok"
    cred = provider_credentials(pid)
    api_key, base_url, model = cred["apiKey"], cred["baseUrl"], cred["model"]
    out: list[str] = [""] * len(texts)
    if not texts:
        return out
    total = max(1, len(texts))
    bs = max(1, min(16, int(batch_size or 8)))
    starts = list(range(0, len(texts), bs))
    w = max(1, min(6, int(workers or 2), len(starts)))
    label = {
        "openai": "OpenAI",
        "gemini": "Gemini",
        "deepseek": "DeepSeek",
        "openrouter": "OpenRouter",
        "grok": "Grok",
    }.get(pid, pid)
    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=55,
            message=f"Dịch {label} · {model}…",
            running=True,
        )
    done = 0
    done_lock = __import__("threading").Lock()

    def _batch(start: int) -> tuple[int, list[str]]:
        check_cancel(project_id)
        chunk = texts[start : start + bs]
        prompt = _cloud_batch_prompt(chunk, target_lang=target_lang, source_lang=source_lang)
        try:
            if pid == "gemini":
                raw = _gemini_generate(
                    base_url=base_url, api_key=api_key, model=model, prompt=prompt
                )
            else:
                raw = _openai_compatible_chat(
                    base_url=base_url, api_key=api_key, model=model, prompt=prompt
                )
        except ValueError:
            # Input too large for context window — send 1-by-1 instead
            raw = None
        if raw is None:
            # 1 request / câu (batch quá lớn hoặc parse fail)
            parsed = []
            for text in chunk:
                check_cancel(project_id)
                one = (
                    f"Translate into {_lang_name(target_lang)}. "
                    "Return ONLY the translation.\n\n"
                    f"{text}"
                )
                if pid == "gemini":
                    line = _gemini_generate(
                        base_url=base_url, api_key=api_key, model=model, prompt=one
                    )
                else:
                    line = _openai_compatible_chat(
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        prompt=one,
                        max_output_tokens=256,
                    )
                line = (line or "").strip().splitlines()[0].strip() if line else text
                parsed.append(line or text)
        else:
            parsed = _parse_numbered_batch(raw, len(chunk), chunk)
            if parsed is None:
                # parse fail — retry 1-by-1
                parsed = []
                for text in chunk:
                    check_cancel(project_id)
                    one = (
                        f"Translate into {_lang_name(target_lang)}. "
                        "Return ONLY the translation.\n\n"
                        f"{text}"
                    )
                    if pid == "gemini":
                        line = _gemini_generate(
                            base_url=base_url,
                            api_key=api_key,
                            model=model,
                            prompt=one,
                        )
                    else:
                        line = _openai_compatible_chat(
                            base_url=base_url,
                            api_key=api_key,
                            model=model,
                            prompt=one,
                            max_output_tokens=256,
                        )
                    line = (line or "").strip().splitlines()[0].strip() if line else text
                    parsed.append(line or text)
        cleaned = []
        for text, line in zip(chunk, parsed):
            line = _clean_burn_text(line, target_lang=target_lang) or line
            cleaned.append((line or "").strip() or text)
        return start, cleaned

    with ThreadPoolExecutor(max_workers=w, thread_name_prefix=f"cloud-{pid}") as pool:
        futs = {pool.submit(_batch, s): s for s in starts}
        for fut in as_completed(futs):
            check_cancel(project_id)
            start, lines = fut.result()
            for j, line in enumerate(lines):
                out[start + j] = line
            with done_lock:
                done += len(lines)
                cur = done
            if project_id:
                set_status(
                    project_id,
                    step="translate",
                    progress=55 + int(35 * cur / total),
                    message=f"Dịch {label} {cur}/{total}",
                    running=True,
                )
    return out


def translate_segments(
    texts: list[str],
    target_lang: str,
    project_id: str | None = None,
    *,
    source_lang: str = "auto",
    translator: str = "google",
    workers: int = 2,
) -> list[str]:
    """google | mymemory | tiktok | ollama | openai | gemini | deepseek | openrouter | grok.

    Free MT fallback cứng: Google → TikTok → MyMemory (bỏ engine đã thử).
    """
    if not texts:
        return []
    eng = (translator or "google").lower().strip()
    if eng in ("9router", "open-router"):
        eng = "openrouter"
    if eng in ("xai", "x-ai"):
        eng = "grok"
    if eng in ("tt", "tiktok_trans", "tiktok-translate"):
        eng = "tiktok"
    if eng in ("mm", "my-memory", "my_memory"):
        eng = "mymemory"
    w = max(1, min(16, int(workers or 2)))

    def _clean_all(raw: list[str]) -> list[str]:
        out: list[str] = []
        for src, tr in zip(texts, raw):
            cleaned = _clean_burn_text(tr, target_lang=target_lang) or tr
            out.append((cleaned or "").strip() or src)
        return out

    def _run_free(name: str) -> list[str]:
        if name == "google":
            return translate_google_free(
                texts,
                target_lang,
                source_lang,
                workers=w,
                project_id=project_id,
            )
        if name == "tiktok":
            return translate_tiktok(
                texts,
                target_lang,
                source_lang,
                workers=min(w, 6),
                project_id=project_id,
            )
        if name == "mymemory":
            return translate_mymemory(
                texts,
                target_lang,
                source_lang,
                workers=min(w, 8),
                project_id=project_id,
            )
        raise RuntimeError(f"unknown free mt: {name}")

    def _free_chain(primary: str) -> list[str]:
        # Thứ tự fallback: Google → TikTok → MyMemory (primary lên đầu)
        base = ["google", "tiktok", "mymemory"]
        order = [primary] + [x for x in base if x != primary]
        last_err: Exception | None = None
        for name in order:
            try:
                if project_id and name != primary:
                    set_status(
                        project_id,
                        step="translate",
                        progress=58,
                        message=f"Fallback {name}…",
                        running=True,
                    )
                raw = _run_free(name)
                out = _clean_all(raw)
                # vá chỗ rỗng/hỏng bằng engine kế trong chain
                need = [
                    i
                    for i, (s, t) in enumerate(zip(texts, out))
                    if _needs_google_fallback(s, t, target_lang=target_lang)
                ]
                if not need:
                    return out
                for fb in order:
                    if fb == name:
                        continue
                    try:
                        fixed = _run_free(fb)
                        # chỉ lấy các index need
                        for i in need:
                            tr = fixed[i] if i < len(fixed) else ""
                            cleaned = _clean_burn_text(tr, target_lang=target_lang) or tr
                            if cleaned and not _needs_google_fallback(
                                texts[i], cleaned, target_lang=target_lang
                            ):
                                out[i] = cleaned.strip() or texts[i]
                        need = [
                            i
                            for i, (s, t) in enumerate(zip(texts, out))
                            if _needs_google_fallback(s, t, target_lang=target_lang)
                        ]
                        if not need:
                            return out
                    except (
                        httpx.HTTPError,
                        RuntimeError,
                        ValueError,
                        TypeError,
                        IndexError,
                    ):
                        continue
                return out
            except (httpx.HTTPError, RuntimeError, ValueError, TypeError, IndexError) as e:
                last_err = e
                continue
        if last_err:
            raise last_err
        return list(texts)

    # Cloud LLM — lỗi → free chain Google→TikTok→MyMemory
    if eng in ("openai", "gemini", "deepseek", "openrouter", "grok"):
        try:
            raw = translate_cloud(
                texts,
                target_lang,
                eng,
                project_id=project_id,
                source_lang=source_lang,
                workers=w,
            )
            out = _clean_all(raw)
            need = [
                i
                for i, (s, t) in enumerate(zip(texts, out))
                if _needs_google_fallback(s, t, target_lang=target_lang)
            ]
            if not need:
                return out
            # vá free chain cho chỗ hỏng
            free = _free_chain("google")
            for i in need:
                if i < len(free):
                    out[i] = free[i]
            return out
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError, IndexError) as e:
            if project_id:
                set_status(
                    project_id,
                    step="translate",
                    progress=58,
                    message=f"{eng} lỗi — free MT… ({e})",
                    running=True,
                )
            eng = "google"

    if eng in ("ollama", "local", "llm"):
        try:
            raw = translate_ollama(
                texts,
                target_lang,
                project_id=project_id,
                source_lang=source_lang,
                workers=w,
            )
            out = _clean_all(raw)
            need = [
                i
                for i, (s, t) in enumerate(zip(texts, out))
                if _needs_google_fallback(s, t, target_lang=target_lang)
            ]
            if need:
                free = _free_chain("google")
                for i in need:
                    if i < len(free):
                        out[i] = free[i]
            return out
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError, IndexError) as e:
            if project_id:
                set_status(
                    project_id,
                    step="translate",
                    progress=58,
                    message=f"Ollama lỗi — free MT… ({e})",
                    running=True,
                )
            eng = "google"

    # Free: google | tiktok | mymemory (+ fallback chain)
    if eng not in ("google", "tiktok", "mymemory"):
        eng = "google"
    return _free_chain(eng)


def _with_google_fallback(
    texts: list[str],
    translations: list[str],
    *,
    target_lang: str,
    source_lang: str,
    project_id: str | None = None,
    workers: int = 8,
) -> list[str]:
    """Chỗ nào LLM hỏng → Google free (song song)."""
    out = list(translations)
    need = [
        i
        for i, (src, tr) in enumerate(zip(texts, out))
        if _needs_google_fallback(src, tr, target_lang=target_lang)
    ]
    if not need:
        return out
    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=78,
            message=f"Google fallback {len(need)} đoạn…",
            running=True,
        )
    try:
        fixed = translate_google_free(
            [texts[i] for i in need],
            target_lang,
            source_lang,
            workers=workers,
            project_id=None,
        )
        for i, tr in zip(need, fixed):
            cleaned = _clean_burn_text(tr, target_lang=target_lang) or tr
            out[i] = cleaned.strip() or texts[i]
    except (httpx.HTTPError, ValueError, TypeError, IndexError):
        for i in need:
            if not (out[i] or "").strip() or _needs_google_fallback(
                texts[i], out[i], target_lang=target_lang
            ):
                out[i] = texts[i]
    return out
