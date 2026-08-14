import numpy as np

from pipeline.tts import voice_store
from pipeline.tts.engines import vieneu


def test_zmai_reference_encoding_survives_process_cache_reset(tmp_path, monkeypatch):
    reference = tmp_path / "adam.wav"
    reference.write_bytes(b"audio")
    item = {"id": "adam", "name": "Adam", "ref_file": reference.name}
    monkeypatch.setattr(voice_store, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(voice_store, "get_reference_voice", lambda _voice_id: item)
    monkeypatch.setattr(voice_store, "reference_path", lambda _item: reference)
    monkeypatch.setattr(vieneu, "_reference_cache", {})

    class FirstClient:
        def encode_reference(self, _path, denoise=False):
            assert denoise is False
            return np.asarray([1.0, 2.0], dtype=np.float32), np.asarray([[3, 4]])

    first = vieneu._encoded_reference(FirstClient(), "adam")
    vieneu._reference_cache.clear()

    class SecondClient:
        def encode_reference(self, *_args, **_kwargs):
            raise AssertionError("disk cache should avoid encoding again")

    second = vieneu._encoded_reference(SecondClient(), "adam")

    np.testing.assert_array_equal(second["speaker_emb"], first["speaker_emb"])
    np.testing.assert_array_equal(second["codes"], first["codes"])
