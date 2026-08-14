from api.deps import PreviewTtsIn
from api.routes import tts_preview


def test_preview_uses_speed_and_separates_cache(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tts_preview, "load_meta", lambda _project_id: {"settings": {}})
    monkeypatch.setattr(tts_preview, "ensure_layout", lambda _project_id: tmp_path)
    monkeypatch.setattr(
        tts_preview,
        "tts_segment",
        lambda *args, **kwargs: calls.append(kwargs["speed"]) or 1.0,
    )

    slow = tts_preview.api_preview_tts(
        "project", "segment", PreviewTtsIn(text="Xin chào", speed=0.8)
    )
    fast = tts_preview.api_preview_tts(
        "project", "segment", PreviewTtsIn(text="Xin chào", speed=1.1)
    )

    assert calls == [0.8, 1.1]
    assert slow["audioUrl"] != fast["audioUrl"]
