import unittest
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

from pipeline.asr.speaker import (
    EMBEDDING_NAME,
    assign_speakers,
    ensure_diarization_models,
    diarization_provider_for_device,
    preferred_diarization_provider,
)


class SpeakerAssignmentTests(unittest.TestCase):
    def test_provider_follows_video_clone_device_detection(self):
        cases = [
            ({"accel": "cuda", "gpuKind": "nvidia"}, "cuda"),
            ({"accel": "metal", "gpuKind": "apple"}, "coreml"),
            ({"accel": "directml", "gpuKind": "amd"}, "cpu"),
            ({"accel": "cpu", "gpuKind": "cpu"}, "cpu"),
        ]
        for device, expected in cases:
            with self.subTest(device=device), patch.dict(os.environ, {"SPEAKER_DIARIZATION_PROVIDER": "auto"}), patch("pipeline.core.media.detect_device", return_value=device):
                self.assertEqual(preferred_diarization_provider(), expected)
                self.assertEqual(diarization_provider_for_device(device), expected)

    def test_assign_speakers_uses_largest_overlap(self):
        segments = [
            {"start": 0.0, "end": 1.2},
            {"start": 1.2, "end": 2.5},
            {"start": 3.0, "end": 3.5},
        ]
        turns = [
            {"start": 0.0, "end": 1.6, "speaker": "SPEAKER_00"},
            {"start": 1.4, "end": 2.8, "speaker": "SPEAKER_01"},
        ]
        assign_speakers(segments, turns)
        self.assertEqual(
            [item.get("speaker") for item in segments],
            ["SPEAKER_00", "SPEAKER_01", None],
        )

    def test_model_installer_extracts_and_reuses_downloads(self):
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            source_model = tmp / "model.int8.onnx"
            source_model.write_bytes(b"segmentation")
            archive = tmp / "segmentation.tar.bz2"
            with tarfile.open(archive, "w:bz2") as bundle:
                bundle.add(source_model, arcname="bundle/model.int8.onnx")
            embedding_source = tmp / "embedding.onnx"
            embedding_source.write_bytes(b"embedding")
            destination = tmp / "installed"
            with patch("pipeline.asr.speaker.SEGMENTATION_URL", archive.as_uri()), patch(
                "pipeline.asr.speaker.EMBEDDING_URL", embedding_source.as_uri()
            ):
                segmentation, embedding = ensure_diarization_models(destination)
                self.assertEqual(segmentation.read_bytes(), b"segmentation")
                self.assertEqual(embedding.name, EMBEDDING_NAME)
                self.assertEqual(embedding.read_bytes(), b"embedding")
                archive.unlink()
                embedding_source.unlink()
                self.assertEqual(ensure_diarization_models(destination), (segmentation, embedding))


if __name__ == "__main__":
    unittest.main()
