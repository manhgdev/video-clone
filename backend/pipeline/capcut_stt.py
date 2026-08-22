"""CapCut cloud STT + translation for the standalone SRT exporter.

This is intentionally separate from local Whisper: a media file is uploaded to
CapCut, the ``cc_audio_subtitle_asr`` task is requested with translation
enabled, then its timed translated utterances are written as SRT by
``pipeline.srt_export``.  The request/device helpers are shared with the
existing CapCut TTS integration.
"""
from __future__ import annotations

import binascii
import datetime as dt
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx

from pipeline.tts.capcut import BASE, base_headers, common_query, compact_json, load_device, make_sign_header

_VOD_REGION = "sdwdmwlll"
_VOD_SERVICE = "vod"
_LANGUAGES = {
    "auto": "auto", "vi": "vi-VN", "en": "en-US", "zh": "zh-CN", "ja": "ja-JP", "ko": "ko-KR",
}
# App endpoints normally report ``ret=0``.  CapCut's VOD transfer endpoints
# instead return ``ret=2000, errmsg=Success`` after accepting a binary part.
# Both are successful responses, despite using different response contracts.
_SUCCESS_CODES = {"", "0", "00", "2000"}
# A valid task id may be returned before CapCut's worker picks it up, but an
# empty query response for long periods is not a queued job.  Do not leave the
# editor stuck for the full 20-minute task deadline in that situation.
_MAX_MISSING_STATUS_POLLS = 15


class CapCutSttError(RuntimeError):
    """CapCut cannot complete a cloud subtitle/translation task."""


def _language(value: str, *, default: str) -> str:
    return _LANGUAGES.get((value or "").lower().split("-", 1)[0], value or default)


def _cancelled(check: Callable[[], bool] | None) -> None:
    if check and check():
        raise InterruptedError("Đã hủy xuất phụ đề CapCut")


def _json(client: httpx.Client, method: str, url: str, *, headers: dict[str, str], content: Any = None, timeout: float = 60) -> dict[str, Any]:
    response = client.request(method, url, headers=headers, content=content, timeout=timeout)
    try:
        data = response.json()
    except Exception as exc:
        raise CapCutSttError(f"CapCut trả HTTP {response.status_code} không phải JSON") from exc
    if response.status_code >= 400:
        raise CapCutSttError(f"CapCut HTTP {response.status_code}: {data}")
    ret = str(data.get("ret") or data.get("code") or "")
    message = str(data.get("errmsg") or data.get("message") or "")
    if ret not in _SUCCESS_CODES:
        raise CapCutSttError(f"CapCut lỗi {ret}: {message or data}")
    return data


def _post_task(client: httpx.Client, path: str, body: dict[str, Any], device: dict[str, Any], *, babi: dict[str, Any] | None, appid: bool) -> dict[str, Any]:
    body_text = compact_json(body)
    url = BASE + path + "?" + urlencode(common_query(device, babi, include_region=babi is not None))
    headers = base_headers(device, body_text, appid=appid)
    headers["sign"] = make_sign_header(url, str(device["appvr"]), headers["device-time"], str(device["tdid"]))
    return _json(client, "POST", url, headers=headers, content=body_text.encode("utf-8"))


def _file_hashes(path: Path, check: Callable[[], bool] | None) -> tuple[str, str]:
    md5 = hashlib.md5()
    crc = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            _cancelled(check)
            md5.update(block)
            crc = binascii.crc32(block, crc)
    return md5.hexdigest(), f"{crc & 0xffffffff:08x}"


def _hmac(key: str | bytes, value: str | bytes) -> bytes:
    return hmac.new(key.encode() if isinstance(key, str) else key, value.encode() if isinstance(value, str) else value, hashlib.sha256).digest()


def _aws_auth(method: str, url: str, payload: bytes, credentials: dict[str, Any], timestamp: str) -> str:
    day = timestamp[:8]
    scope = f"{day}/{_VOD_REGION}/{_VOD_SERVICE}/aws4_request"
    pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(pairs))
    token = str(credentials["session_token"])
    headers = f"x-amz-date:{timestamp}\nx-amz-security-token:{token}\n"
    signed = "x-amz-date;x-amz-security-token"
    canonical = "\n".join((method, urlsplit(url).path, query, headers, signed, hashlib.sha256(payload).hexdigest()))
    to_sign = "\n".join(("AWS4-HMAC-SHA256", timestamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    key = _hmac(_hmac(_hmac(_hmac("AWS4" + str(credentials["secret_access_key"]), day), _VOD_REGION), _VOD_SERVICE), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={credentials['access_key_id']}/{scope}, SignedHeaders={signed}, Signature={signature}"


def _vod_headers(method: str, url: str, payload: bytes, credentials: dict[str, Any], device: dict[str, Any]) -> dict[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return {
        "Authorization": _aws_auth(method, url, payload, credentials, stamp),
        "Date": now.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "X-Amz-Date": stamp, "X-Amz-Expires": "31536000", "X-Amz-Security-Token": str(credentials["session_token"]),
        "User-Agent": f"BDFileUpload({int(time.time() * 1000)})", "accept-encoding": "identity",
        "tdid": str(device["tdid"]), "pf": str(device["pf"]),
        "store-country-code": str(device["loc"]).lower(), "store-country-code-src": "did",
    }


def _upload(path: Path, client: httpx.Client, device: dict[str, Any], check: Callable[[], bool] | None, progress: Callable[[str], None] | None) -> tuple[str, str, int]:
    if not path.is_file():
        raise CapCutSttError("Không tìm thấy video để gửi CapCut")
    if progress:
        progress("CapCut: đang chuẩn bị video…")
    md5, crc32 = _file_hashes(path, check)
    _cancelled(check)
    sign = _post_task(client, "/lv/v1/upload_sign", {"biz": "cc_pc_text_recognize", "key_version": "v5"}, device, babi=None, appid=True)
    credentials = sign.get("data") or {}
    required = ("domain", "access_key_id", "secret_access_key", "session_token", "space_name")
    if any(not credentials.get(key) for key in required):
        raise CapCutSttError("CapCut không cấp quyền upload video")
    base = f"https://{credentials['domain']}/top/v1?"
    apply_url = base + urlencode({"Action": "ApplyUploadInner", "SpaceName": credentials["space_name"], "UseQuic": "false", "Version": "2020-11-19", "device_platform": "win"})
    apply = _json(client, "GET", apply_url, headers=_vod_headers("GET", apply_url, b"", credentials, device))
    try:
        node = apply["Result"]["InnerUploadAddress"]["UploadNodes"][0]
        store = node["StoreInfos"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise CapCutSttError("CapCut trả dữ liệu upload không hợp lệ") from exc
    if progress:
        progress("CapCut: đang tải video lên…")
    transfer_url = f"https://{node['UploadHost']}/upload/v1/{store['StoreUri']}?" + urlencode({"uploadid": store["UploadID"], "part_number": "0", "phase": "transfer"})
    transfer_headers = {"Authorization": store["Auth"], "X-Upload-Content-CRC32": crc32, "User-Agent": "BDFileUpload", "accept-encoding": "identity"}
    with path.open("rb") as stream:
        _json(client, "POST", transfer_url, headers=transfer_headers, content=stream, timeout=900)
    _cancelled(check)
    finish_url = f"https://{node['UploadHost']}/upload/v1/{store['StoreUri']}?" + urlencode({"uploadmode": "part", "phase": "finish", "uploadid": store["UploadID"]})
    _json(client, "POST", finish_url, headers={"Authorization": store["Auth"], "User-Agent": "BDFileUpload", "accept-encoding": "identity"}, content=f"0:{crc32}".encode())
    commit_url = base + urlencode({"Action": "CommitUploadInner", "SpaceName": credentials["space_name"], "Version": "2020-11-19", "device_platform": "win"})
    commit_body = compact_json({"Functions": [{"Input": {"SnapshotTime": 0.0}, "Name": "Snapshot"}], "SessionKey": node["SessionKey"]}).encode()
    committed = _json(client, "POST", commit_url, headers=_vod_headers("POST", commit_url, commit_body, credentials, device), content=commit_body, timeout=120)
    try:
        result = committed["Result"]["Results"][0]
        duration = int(float((result.get("VideoMeta") or {}).get("Duration") or 0) * 1000)
        return str(result.get("Vid") or node.get("Vid")), str((result.get("VideoMeta") or {}).get("Md5") or md5), duration
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CapCutSttError("CapCut không xác nhận video đã tải") from exc


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise CapCutSttError("CapCut trả phụ đề không hợp lệ") from exc
    return payload if isinstance(payload, dict) else {}


def subtitle_cues(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize CapCut STT payload while tolerating current field aliases."""
    rows = payload.get("utterances") or payload.get("captions") or payload.get("subtitles") or []
    source: list[dict[str, Any]] = []
    translated: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("source_text") or item.get("original_text") or "").strip()
        start = float(item.get("start_time", item.get("start", 0)) or 0) / (1000 if "start_time" in item else 1)
        end = float(item.get("end_time", item.get("end", 0)) or 0) / (1000 if "end_time" in item else 1)
        if not text or end <= start:
            continue
        cue = {"start": start, "end": end, "text": text}
        source.append(cue)
        # The live CapCut ASR task uses ``translation_text``.  Older payloads
        # have used the aliases that follow, so retain all of them.
        target = str(item.get("translation_text") or item.get("translation") or item.get("translated_text") or item.get("target_text") or item.get("translate_text") or "").strip()
        if target:
            translated.append({**cue, "text": target})
    return source, translated


def _poll_progress_message(status: str, elapsed_sec: float, poll_count: int) -> str:
    """Useful progress without inventing a completion percentage for CapCut."""
    state = {
        "queueing": "đang xếp hàng trên CapCut",
        "queued": "đang xếp hàng trên CapCut",
        "pending": "đang chờ CapCut xử lý",
        "processing": "CapCut đang nhận dạng và dịch",
        "running": "CapCut đang nhận dạng và dịch",
    }.get((status or "").lower(), "đang chờ phản hồi từ CapCut")
    return f"CapCut: {state} · đã chờ {max(0, round(elapsed_sec))}s · kiểm tra #{max(1, poll_count)}"


def _task_status(query: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a valid task and status, or an empty status for a stale reply."""
    rows = ((query.get("data") or {}).get("tasks") or [])
    task = rows[0] if rows and isinstance(rows[0], dict) else {}
    raw = task.get("status") or task.get("task_status") or task.get("state") or ""
    status = str(raw).strip().lower()
    # VOD/common-task deployments use these equivalent terminal labels.
    # The live common-task API currently reports ``succeed`` (not
    # ``success``) when subtitle recognition has finished.  Treat all of the
    # terminal-success spellings equivalently before reading its payload.
    status = {
        "done": "success",
        "completed": "success",
        "succeed": "success",
        "failure": "failed",
    }.get(status, status)
    return task, status


def transcribe_and_translate(path: Path, source_lang: str, target_lang: str, *, require_translation: bool = True, cancelled: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Upload one video, ask CapCut STT to translate it, and return timed cues."""
    device = load_device()
    with httpx.Client(trust_env=False, follow_redirects=True) as client:
        vid, md5, duration = _upload(path, client, device, cancelled, progress)
        _cancelled(cancelled)
        if progress:
            progress("CapCut: đang nhận dạng và dịch…")
        babi = {"feature_entrance": "editor", "feature_entrance_detail": "editor-elements-captions-subtitle_recognition", "feature_key": "subtitle_recognition", "scenario": "video_editor"}
        cap_json = {"adjust_endtime": 200, "audio": vid, "audio_type": "vid", "caption_type": 0, "client_request_id": str(uuid.uuid4()), "duration": max(1, duration), "enable_cache": True, "enter_from": "asr", "language": _language(source_lang, default="auto"), "max_lines": 1, "md5": md5, "pack_options": {"need_attribute": True}, "songs_info": [{"end_time": max(1, duration) - 10.334, "id": "", "start_time": 0}], "translation_language": _language(target_lang, default="vi-VN"), "use_translation": True, "words_per_line": 15}
        created = _post_task(client, "/lv/v1/common_task/new", {"bind_id": str(uuid.uuid4()).upper(), "can_queue": True, "enter_from": "asr", "tasks": [{"context": str(uuid.uuid4()), "payload": compact_json({"cap_json": cap_json}), "req_key": "cc_audio_subtitle_asr", "task_version": "v3"}]}, device, babi=babi, appid=False)
        tasks = ((created.get("data") or {}).get("tasks") or [])
        if not tasks or not tasks[0].get("id") or not tasks[0].get("token"):
            raise CapCutSttError("CapCut không tạo được tác vụ dịch phụ đề")
        task_id, token = str(tasks[0]["id"]), str(tasks[0]["token"])
        started = time.monotonic()
        deadline = started + 20 * 60
        polls = 0
        missing_status_polls = 0
        last_report_at = -6.0
        last_status = ""
        while time.monotonic() < deadline:
            _cancelled(cancelled)
            query = _post_task(client, "/lv/v1/common_task/query", {"tasks": [{"bind_id": "", "id": task_id, "req_key": "cc_audio_subtitle_asr", "task_version": "v3", "token": token}]}, device, babi=None, appid=False)
            task, status = _task_status(query)
            polls += 1
            elapsed = time.monotonic() - started
            if not status:
                missing_status_polls += 1
                if progress and (elapsed - last_report_at >= 6.0):
                    progress(_poll_progress_message("", elapsed, polls))
                    last_report_at = elapsed
                if missing_status_polls >= _MAX_MISSING_STATUS_POLLS:
                    raise CapCutSttError(
                        "CapCut không trả trạng thái tác vụ sau 30 giây. Hãy thử lại sau ít phút."
                    )
                time.sleep(2)
                continue
            missing_status_polls = 0
            # Polling is every two seconds; log at a readable six-second
            # cadence and whenever CapCut moves between queue/processing.
            if progress and (status != last_status or elapsed - last_report_at >= 6.0):
                progress(_poll_progress_message(status, elapsed, polls))
                last_report_at, last_status = elapsed, status
            if status == "success":
                source, translated = subtitle_cues(_task_payload(task))
                if not source:
                    raise CapCutSttError("CapCut không trả câu phụ đề có timecode")
                if require_translation and not translated:
                    raise CapCutSttError("CapCut không trả bản dịch cho ngôn ngữ đã chọn")
                return source, translated
            if status in {"failed", "error", "cancelled"}:
                raise CapCutSttError(f"CapCut dịch phụ đề thất bại: {task.get('message') or task}")
            time.sleep(2)
    raise CapCutSttError("CapCut dịch phụ đề quá thời gian chờ")
