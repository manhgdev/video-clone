"""ZM Tool license check and local activation state."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from pipeline.core.config import DATA, ensure_data_dirs

API_BASE = "https://api.zm.io.vn/key"
APP_PATH = "zm_tool"
LICENSE_FILE = DATA / "license.json"
_CACHE_SECONDS = 30 * 60.0
_cache_lock = threading.Lock()
_request_lock = threading.Lock()
_cache_at = 0.0
_cache: dict[str, Any] | None = None


def _masked(key: str) -> str:
    if len(key) <= 6:
        return "•" * len(key)
    return f"{key[:3]}{'•' * min(8, len(key) - 6)}{key[-3:]}"


def _read_key() -> str:
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
        return str(data.get("key") or "").strip()
    except (OSError, ValueError, TypeError):
        return ""


def _save_key(key: str) -> None:
    ensure_data_dirs()
    tmp = Path(f"{LICENSE_FILE}.tmp")
    tmp.write_text(json.dumps({"key": key}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LICENSE_FILE)


def _request(action: str, key: str) -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ZM-Tool/1.0",
        "Origin": "https://zm.io.vn",
        "Referer": "https://zm.io.vn/",
    }
    # httpx có thể parse NO_PROXY chứa IPv6 trần ``::1`` thành port ``:1``.
    # License API là HTTPS cố định nên kết nối trực tiếp, không phụ thuộc proxy máy.
    with httpx.Client(trust_env=False, timeout=30.0) as client:
        response = client.post(f"{API_BASE}/{action}", json={"key": key}, headers=headers)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        detail = payload.get("data") or payload.get("message") or "Key không hợp lệ"
        raise RuntimeError(str(detail))
    if isinstance(payload.get("data"), dict) and payload["data"].get("apps") is not None:
        return payload["data"]
    return payload


def status_from_payload(payload: dict[str, Any], key: str = "") -> dict[str, Any]:
    apps = payload.get("apps") if isinstance(payload.get("apps"), list) else []
    app = next((item for item in apps if item.get("path") == APP_PATH), None)
    if not payload.get("status"):
        message = "Key đã bị khóa"
    elif not app:
        message = "Key không có quyền sử dụng ZM Tool"
    elif not app.get("status"):
        message = "Quyền sử dụng ZM Tool đã bị khóa"
    else:
        remaining = int(app.get("remaining_day") or 0)
        message = "Đã kích hoạt" if remaining == -1 or remaining > 0 else "Key đã hết hạn"

    remaining = int(app.get("remaining_day") or 0) if app else 0
    valid = bool(
        payload.get("status")
        and app
        and app.get("status")
        and (remaining == -1 or remaining > 0)
    )
    return {
        "valid": valid,
        "configured": bool(key),
        "keyMasked": _masked(key) if key else "",
        "remainingDay": remaining,
        "expiresAt": app.get("expires_at") if app else None,
        "activationLimit": int(app.get("activation_limit") or 0) if app else 0,
        "message": message,
    }


def license_status(*, force: bool = False) -> dict[str, Any]:
    global _cache, _cache_at
    key = _read_key()
    if not key:
        return {
            "valid": False,
            "configured": False,
            "keyMasked": "",
            "remainingDay": 0,
            "expiresAt": None,
            "activationLimit": 0,
            "message": "Chưa nhập key kích hoạt",
        }
    with _cache_lock:
        if not force and _cache and time.monotonic() - _cache_at < _CACHE_SECONDS:
            return dict(_cache)
    with _request_lock:
        # React StrictMode/F5 can issue two status calls together; the second one
        # reuses the result created by the first instead of hitting the key API.
        with _cache_lock:
            if not force and _cache and time.monotonic() - _cache_at < _CACHE_SECONDS:
                return dict(_cache)
        try:
            result = status_from_payload(_request("checkkey", key), key)
        except Exception as exc:
            result = {
                "valid": False,
                "configured": True,
                "keyMasked": _masked(key),
                "remainingDay": 0,
                "expiresAt": None,
                "activationLimit": 0,
                "message": f"Không thể kiểm tra key: {exc}",
            }
        with _cache_lock:
            _cache = dict(result)
            _cache_at = time.monotonic()
    return result


def activate_license(key: str) -> dict[str, Any]:
    global _cache, _cache_at
    key = key.strip()
    if not key:
        raise ValueError("Vui lòng nhập key")
    if key == _read_key():
        current = license_status(force=True)
        if current["valid"]:
            return current

    checked = status_from_payload(_request("checkkey", key), key)
    if not checked["valid"]:
        raise ValueError(checked["message"])
    if checked["activationLimit"] <= 0:
        raise ValueError("Key đã hết lượt kích hoạt")

    activated = status_from_payload(_request("activate", key), key)
    if not activated["valid"]:
        raise ValueError(activated["message"])
    _save_key(key)
    with _cache_lock:
        _cache = dict(activated)
        _cache_at = time.monotonic()
    return activated


def deactivate_license() -> dict[str, Any]:
    """Remove this computer's saved key without changing the key server."""
    global _cache, _cache_at
    try:
        LICENSE_FILE.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Không thể xoá key đã lưu: {exc}") from exc
    with _cache_lock:
        _cache = None
        _cache_at = 0.0
    return license_status(force=True)


def license_cached_valid() -> bool:
    return bool(license_status().get("valid"))
