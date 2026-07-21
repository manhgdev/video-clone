from api.deps import StudioSynthIn
from api.routes.tts_studio import api_tts_studio_synth
from pipeline.tts import studio


def test_tts_studio_schema_covers_text_and_srt(monkeypatch):
    monkeypatch.setattr(studio, "synth_text_job", lambda **kwargs: kwargs)
    monkeypatch.setattr(studio, "synth_srt_job", lambda **kwargs: kwargs)

    text = api_tts_studio_synth(StudioSynthIn(
        jobId="frontend01",
        text="Xin chào",
        voice="voice-1",
        autoSplit=True,
        gapMs=300,
    ))
    assert text["text"] == "Xin chào"
    assert text["auto_split"] is True
    assert text["gap_ms"] == 300
    assert text["job_id"] == "frontend01"

    srt = api_tts_studio_synth(StudioSynthIn(
        srtText="1\n00:00:00,000 --> 00:00:01,000\nXin chào",
        speaker_id="speaker-1",
        keepTimeline=True,
    ))
    assert srt["voice"] == "speaker-1"
    assert srt["keep_timeline"] is True
