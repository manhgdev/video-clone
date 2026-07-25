import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.routes import jobs


class RevealOutputPathTest(unittest.TestCase):
    def test_reveal_prefers_published_export_over_intermediate_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project_id = "project"
            exports = root / "backend" / "public" / project_id / "exports"
            intermediate = root / "backend" / "public" / project_id / "out"
            exports.mkdir(parents=True)
            intermediate.mkdir(parents=True)
            published = exports / "render.mp4"
            work_file = intermediate / "final.mp4"
            published.write_bytes(b"published")
            work_file.write_bytes(b"work")

            meta = {
                "outputRel": "backend/public/project/exports/render.mp4",
                "exportCopy": "backend/public/project/exports/render.mp4",
                "outputPath": str(work_file),
                "exportOutputDir": str(exports),
                "lastRenderName": "render",
            }
            with (
                patch.object(jobs, "REPO_ROOT", root),
                patch.object(jobs, "PUBLIC_DATA", root / "backend" / "public"),
                patch("pipeline.core.project.load_meta", return_value=meta),
                patch("platform.system", return_value="Windows"),
                patch("subprocess.Popen") as popen,
            ):
                result = jobs.api_reveal_output(project_id)

            self.assertEqual(result["path"], str(published.resolve()))
            popen.assert_called_once_with(["explorer", f"/select,{published}"])


if __name__ == "__main__":
    unittest.main()
