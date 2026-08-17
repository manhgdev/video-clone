import unittest

from pipeline.core.project import normalize_project_tracks


class ProjectTrackMigrationTests(unittest.TestCase):
    def test_legacy_segment_gets_independent_source_and_dub_fields(self):
        meta = {"segments": [{"id": "a", "source": "你好", "translation": "Xin chào"}]}
        normalized = normalize_project_tracks(meta)
        cue = normalized["segments"][0]
        self.assertEqual(cue["sourceSubtitle"], "你好")
        self.assertEqual(cue["dubSubtitle"], "Xin chào")
        self.assertEqual(normalized["trackSchema"], 2)
        self.assertFalse(normalized["settings"]["sourceSubtitleVisible"])
        self.assertTrue(normalized["settings"]["dubSubtitleVisible"])

    def test_new_track_text_is_preserved_without_overwriting_legacy_aliases(self):
        meta = {"segments": [{"id": "a", "source": "old", "translation": "old dub", "sourceSubtitle": "new", "dubSubtitle": "new dub"}]}
        cue = normalize_project_tracks(meta)["segments"][0]
        self.assertEqual(cue["sourceSubtitle"], "new")
        self.assertEqual(cue["dubSubtitle"], "new dub")


if __name__ == "__main__":
    unittest.main()
