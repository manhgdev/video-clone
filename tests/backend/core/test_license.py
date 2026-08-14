from pipeline.core import license
from pipeline.core.license import status_from_payload


def test_zm_tool_license_requires_exact_app_and_time() -> None:
    payload = {
        "status": True,
        "apps": [{"path": "zm_tool", "status": True, "remaining_day": 30,
                  "activation_limit": 2, "expires_at": "2027-01-01T00:00:00Z"}],
    }
    result = status_from_payload(payload, "ABCDEF123456")
    assert result["valid"] is True
    assert result["remainingDay"] == 30
    assert result["activationLimit"] == 2
    assert "123456" not in result["keyMasked"]

    payload["apps"][0]["path"] = "tool_download_aio"
    assert status_from_payload(payload, "ABCDEF123456")["valid"] is False

    payload["apps"][0].update(path="zm_tool", remaining_day=0)
    assert status_from_payload(payload, "ABCDEF123456")["valid"] is False


def test_license_request_ignores_broken_environment_proxy(monkeypatch) -> None:
    seen = {}

    class Response:
        def raise_for_status(self): pass
        def json(self): return {"status": True, "apps": []}

    class Client:
        def __init__(self, **kwargs): seen.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def post(self, *_args, **_kwargs): return Response()

    monkeypatch.setattr(license.httpx, "Client", Client)

    assert license._request("checkkey", "test")["status"] is True
    assert seen["trust_env"] is False


def test_deactivate_removes_only_the_local_saved_key(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "license.json"
    key_file.write_text('{"key":"LOCAL-KEY"}', encoding="utf-8")
    monkeypatch.setattr(license, "LICENSE_FILE", key_file)

    result = license.deactivate_license()

    assert not key_file.exists()
    assert result["configured"] is False
    assert result["valid"] is False
