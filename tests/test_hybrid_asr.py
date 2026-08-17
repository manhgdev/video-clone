import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path


class HybridAsrTests(unittest.TestCase):
    def test_chunks_emit_incremental_events_and_dedupe_overlap(self):
        import numpy as np
        import soundfile as sf
        from pipeline.asr.hybrid import hybrid_asr
        with tempfile.TemporaryDirectory() as raw:
            wav = Path(raw) / "in.wav"
            sf.write(wav, np.zeros((32000 * 3, 1), dtype="float32"), 32000)
            events = []
            with patch("pipeline.asr.whisper.asr_whisper", return_value=[{"start": 0.0, "end": 1.0, "source": "same"}]):
                rows = hybrid_asr(wav, "whisper", "auto", chunk_sec=1, overlap_sec=.2, on_chunk=lambda i, cues: events.append((i, cues)))
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(events), 3)


if __name__ == "__main__":
    unittest.main()
