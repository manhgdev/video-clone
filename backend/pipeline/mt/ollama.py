"""Machine translation backends — ollama."""
from __future__ import annotations

"""MT: Ollama + Google free fallback."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from pipeline.core.jobs import check_cancel
from pipeline.core.project import set_status
from pipeline.core.resources import progress_msg


from .text import *  # noqa: F403

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
                    message=progress_msg("Dịch Ollama", workers=w, extra=f"{model} · batch {bs}"),
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
                            message=progress_msg(f"Dịch {model}", cur, total, workers=w),
                            running=True,
                        )
    except (httpx.HTTPError, RuntimeError) as e:
        raise RuntimeError(
            f"Ollama lỗi ({e}). Chạy `ollama serve` rồi `ollama pull qwen2.5`."
        ) from e
    return out

