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
