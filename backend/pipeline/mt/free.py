"""Machine translation backends — free."""
from __future__ import annotations

"""MT: Ollama + Google free fallback."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from pipeline.core.jobs import check_cancel
from pipeline.core.project import set_status
from pipeline.core.resources import progress_msg


from .text import *  # noqa: F403

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

    from pipeline.core.resources import run_with_adaptive_workers

    last_t = [0.0]
    req = int(workers or 0)
    # workers từ UI: 0=auto (scale giữa chừng); >0=cố định
    start_w = max(1, min(16, req if req > 0 else 8, n))

    def _job(item: tuple[int, str]) -> str:
        i, text = item
        _, tr = _one(i, text)
        return tr

    def _prog(cur: int, total: int, w_now: int) -> None:
        _report_mt(
            project_id,
            label="Google",
            cur=cur,
            total=total,
            last_t=last_t,
            force=(cur == total),
            workers=w_now,
        )

    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=55,
            message=progress_msg("Dịch Google", 0, n, workers=(None if req <= 0 else start_w)),
            running=True,
        )
    rows = run_with_adaptive_workers(
        list(enumerate(texts)),
        _job,
        kind="network",
        requested=req if req > 0 else None,
        cap=min(16, n),
        thread_name_prefix="gtx",
        on_progress=_prog if project_id else None,
        cancel_check=lambda: check_cancel(project_id),
    )
    for i, tr in enumerate(rows):
        out[i] = tr or ""
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

    from pipeline.core.resources import run_with_adaptive_workers

    last_t = [0.0]
    req = int(workers or 0)

    def _job(item: tuple[int, str]) -> str:
        _, tr = _one(item[0], item[1])
        return tr

    def _prog(cur: int, total: int, w_now: int) -> None:
        _report_mt(
            project_id,
            label="MyMemory",
            cur=cur,
            total=total,
            last_t=last_t,
            force=(cur == total),
            workers=w_now,
        )

    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=55,
            message=progress_msg("Dịch MyMemory", 0, n, workers=(None if req <= 0 else max(1, min(12, req, n)))),
            running=True,
        )
    rows = run_with_adaptive_workers(
        list(enumerate(texts)),
        _job,
        kind="network",
        requested=req if req > 0 else None,
        cap=min(12, n),
        thread_name_prefix="mymem",
        on_progress=_prog if project_id else None,
        cancel_check=lambda: check_cancel(project_id),
    )
    for i, tr in enumerate(rows):
        out[i] = tr or ""
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

    # TikTok dễ rate-limit — cap thấp; auto vẫn co/duỗi trong 1–6
    from pipeline.core.resources import run_with_adaptive_workers

    last_t = [0.0]
    req = int(workers or 0)

    def _job(item: tuple[int, str]) -> str:
        _, tr = _one(item[0], item[1])
        return tr

    def _prog(cur: int, total: int, w_now: int) -> None:
        _report_mt(
            project_id,
            label="TikTok",
            cur=cur,
            total=total,
            last_t=last_t,
            force=(cur == total),
            workers=w_now,
        )

    if project_id:
        set_status(
            project_id,
            step="translate",
            progress=55,
            message=progress_msg("Dịch TikTok", 0, n, workers=(None if req <= 0 else max(1, min(6, req, n)))),
            running=True,
        )
    rows = run_with_adaptive_workers(
        list(enumerate(texts)),
        _job,
        kind="network",
        requested=req if req > 0 else None,
        cap=min(6, n),
        thread_name_prefix="tt-mt",
        on_progress=_prog if project_id else None,
        cancel_check=lambda: check_cancel(project_id),
    )
    for i, tr in enumerate(rows):
        out[i] = tr or ""
    return out

