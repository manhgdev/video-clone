"""Machine translation backends — cloud."""
from __future__ import annotations

"""MT: Ollama + Google free fallback."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from pipeline.core.jobs import check_cancel
from pipeline.core.project import set_status


from .text import *  # noqa: F403

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
    from pipeline.core.app_config import provider_credentials

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

