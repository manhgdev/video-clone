"""Queue persist, filename sanitize, matcher, invalidate stages."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pipeline.queue.paths import output_name, sanitize_filename, scan_videos
from pipeline.queue import store
from pipeline.review.match import match_voice, score_scene, tokenize
from pipeline.review.run import invalidate_from, _reuse, _settings_diff
from pipeline.review.scenes import detect_scenes


class ReviewQueueTests(unittest.TestCase):
    def test_visual_normalization_never_promotes_unverified_llm_props(self):
        """A text-only model must not turn guessed food/props into Review facts."""
        from pipeline.review.vision import _normalize

        row = _normalize(
            {"scene_id": 7, "start": 1, "end": 4, "duration": 3},
            {
                "characters": ["A person the model invented"],
                "location": "restaurant",
                "objects": ["food", "bowl"],
                "description": "They prepare a meal.",
            },
            "Hai người tu tiên giao chiến dữ dội.",
        )
        self.assertEqual(row["description"], "Hai người tu tiên giao chiến dữ dội.")
        self.assertEqual(row["characters"], [])
        self.assertEqual(row["location"], "")
        self.assertEqual(row["objects"], [])

    def test_review_script_prompt_forbids_unsupported_scene_details(self):
        from pipeline.review import script as sc

        prompts: list[str] = []
        original = sc.generate_json
        sc.generate_json = lambda prompt, **_kwargs: (
            prompts.append(prompt)
            or {"script": [
                "Hai người giao chiến khi mâu thuẫn bùng nổ và tình thế trở nên căng thẳng hơn.",
                "Cuộc đối đầu tiếp tục đẩy cả hai vào một lựa chọn khó khăn.",
            ]}
        )
        try:
            sc.write_script(
                {"movie_context": {}, "story_graph": {"events": []}},
                duration_sec=30,
                style="recap",
                language="vi",
                spoiler="none",
                visuals=[{"scene_id": 1, "start": 0, "end": 10, "transcript": "Hai người giao chiến."}],
                use_llm=True,
            )
        finally:
            sc.generate_json = original
        self.assertIn("Never invent food, props, weapons", prompts[0])

    def test_capcut_review_transcript_never_calls_whisper(self):
        from pipeline.review import transcript as tr

        rows = [{"start": 0.0, "end": 1.0, "text": "CapCut transcript"}]
        with patch("pipeline.capcut_stt.transcribe_and_translate", return_value=(rows, [])), patch(
            "pipeline.review.transcript._whisper_chunks",
            side_effect=AssertionError("Whisper must not run for CapCut"),
        ):
            result = tr.load_transcript(
                Path("/tmp/review.mp4"), Path("/tmp"), source_lang="zh", target_lang="vi",
                recognition_engine="capcut",
            )
        self.assertEqual(result, rows)

    def test_local_llm_stream_observes_cancel(self):
        """Deleting a Review must not wait for a complete Ollama response."""
        from pipeline.core.jobs import Cancelled, arm_job, clear_job, request_cancel
        from pipeline.review import llm

        job_id = "review-llm-cancel"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_lines(self):
                yield '{"response":"{\\\"script\\\":[", "done":false}'
                request_cancel(job_id)
                yield '{"response":"\\\"too late\\\"]}", "done":true}'

        class Client:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def stream(self, *_args, **_kwargs):
                return Response()

        original_client = llm.httpx.Client
        llm.httpx.Client = Client  # type: ignore[assignment]
        arm_job(job_id)
        try:
            with self.assertRaises(Cancelled):
                llm.generate_json("test", model="local-test", job_id=job_id)
        finally:
            llm.httpx.Client = original_client  # type: ignore[assignment]
            clear_job(job_id)

    def test_copy_output_rejects_missing_render(self):
        from pipeline.clone_run.headless import _copy_output
        with self.assertRaisesRegex(RuntimeError, "không tạo file đầu ra"):
            _copy_output(None, "/tmp/source.mp4", {}, {})

    def test_logo_detector_recognizes_ai_brand_marks(self):
        from pipeline.ocr.logo import _branding_text
        self.assertTrue(_branding_text("Veo 3"))
        self.assertTrue(_branding_text("Grok"))
        self.assertTrue(_branding_text("Kling AI"))

    def test_apple_gpu_usage_parser(self):
        from api.routes.system import _apple_gpu_percent
        self.assertEqual(_apple_gpu_percent('"Device Utilization %" = 47'), 47)
        self.assertIsNone(_apple_gpu_percent("no utilization data"))

    def test_render_parallelism_is_bounded(self):
        from pipeline.export.burn_parts.ffgraph import _merge_adjacent_masks, _segment_parallelism
        self.assertGreaterEqual(_segment_parallelism(), 3)
        self.assertLessEqual(_segment_parallelism(), 6)
        masks = _merge_adjacent_masks([
            {"kind": "mask", "t0": 0.0, "t1": 2.0, "box": (1, 2, 3, 4), "style": "blur", "color": "#000", "opacity": 40},
            {"kind": "mask", "t0": 2.0, "t1": 4.0, "box": (1, 2, 3, 4), "style": "blur", "color": "#000", "opacity": 40},
        ])
        self.assertEqual(len(masks), 1)
        self.assertEqual(masks[0]["t1"], 4.0)

    def test_sanitize_windows_and_macos(self):
        self.assertNotIn(":", sanitize_filename("a:b/c*?"))
        self.assertEqual(sanitize_filename("  "), "video")
        name = output_name(Path("/tmp/My Movie.mkv"), "{name}_{type}", {"type": "review"})
        self.assertTrue(name.endswith(".mp4"))

    def test_scan_skips_cache(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a.mp4").write_bytes(b"x")
            nested = root / "cache" / "b.mp4"
            nested.parent.mkdir()
            nested.write_bytes(b"y")
            found = scan_videos([str(root)])
            self.assertTrue(any(p.endswith("a.mp4") for p in found))
            self.assertFalse(any(p.endswith("b.mp4") for p in found))

    def test_queue_interrupted_roundtrip(self):
        with tempfile.TemporaryDirectory() as raw:
            store.DATA = Path(raw)  # type: ignore[misc]
            store.insert({"id": "j1", "status": "running", "type": "clone"})
            store.insert({"id": "j2", "status": "queued", "type": "review"})
            store.mark_interrupted()
            jobs = {j["id"]: j for j in store.load_all()}
            self.assertEqual(jobs["j1"]["status"], "interrupted")
            self.assertEqual(jobs["j2"]["status"], "queued")

    def test_job_log_appends_and_caps(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "jobs.json"
            orig = store._path
            store._path = lambda: path  # type: ignore[method-assign]
            try:
                store.insert({"id": "j1", "status": "running", "log": []})
                store.mutate("j1", {"stage": "tts"}, log="TTS 1/2: hello")
                store.mutate("j1", {}, log="LỖI TTS_ERROR: boom\nTraceback (most recent call last):")
                job = store.get("j1") or {}
                lines = job.get("log") or []
                self.assertGreaterEqual(len(lines), 3)
                self.assertTrue(any("TTS 1/2" in x for x in lines))
                self.assertTrue(any("LỖI TTS_ERROR" in x for x in lines))
                self.assertTrue(any("Traceback" in x for x in lines))
            finally:
                store._path = orig  # type: ignore[method-assign]

    def test_queue_pause_and_resume_clear_cancel_flag(self):
        from pipeline.queue import engine as queue_engine
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "jobs.json"
            orig_path = store._path
            orig_cancel = queue_engine.request_cancel
            orig_arm = queue_engine.arm_job
            cancelled: list[str] = []
            armed: list[str] = []
            store._path = lambda: path  # type: ignore[method-assign]
            queue_engine.request_cancel = lambda job_id: cancelled.append(job_id) or True
            queue_engine.arm_job = lambda job_id: armed.append(job_id) or 1
            try:
                store.insert({
                    "id": "j1",
                    "status": "running",
                    "type": "review",
                    "parts": [
                        {"index": 1, "status": "done"},
                        {"index": 2, "status": "running"},
                        {"index": 3, "status": "pending"},
                    ],
                })
                engine = object.__new__(queue_engine.QueueEngine)
                engine._pause_all = False
                engine.kick = lambda: None  # type: ignore[method-assign]
                engine.snapshot = lambda: {"jobs": store.load_all()}  # type: ignore[method-assign]
                engine.action("j1", "pause")
                paused = store.get("j1") or {}
                self.assertEqual(paused["status"], "paused")
                self.assertEqual([p["status"] for p in paused["parts"]], ["done", "paused", "pending"])
                self.assertEqual(cancelled, ["j1"])
                engine.action("j1", "resume")
                resumed = store.get("j1") or {}
                self.assertEqual(resumed["status"], "queued")
                self.assertEqual([p["status"] for p in resumed["parts"]], ["done", "pending", "pending"])
                self.assertEqual(armed, ["j1"])
            finally:
                store._path = orig_path  # type: ignore[method-assign]
                queue_engine.request_cancel = orig_cancel
                queue_engine.arm_job = orig_arm

    def test_queue_cancel_and_remove_signal_backend_stop_first(self):
        from pipeline.queue import engine as queue_engine

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "jobs.json"
            original_path = store._path
            original_cancel = queue_engine.request_cancel
            stopped: list[str] = []
            store._path = lambda: path  # type: ignore[method-assign]
            queue_engine.request_cancel = lambda job_id: stopped.append(job_id) or True
            try:
                store.insert({"id": "cancel", "status": "running", "type": "review", "parts": []})
                store.insert({"id": "remove", "status": "running", "type": "review", "parts": []})
                engine = object.__new__(queue_engine.QueueEngine)
                engine._active = {}
                engine._guard = __import__("threading").Lock()
                engine.kick = lambda: None  # type: ignore[method-assign]
                engine.snapshot = lambda: {"jobs": store.load_all()}  # type: ignore[method-assign]

                engine.action("cancel", "cancel")
                cancelled = store.get("cancel") or {}
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertIn("Đã gửi lệnh huỷ", (cancelled.get("log") or [])[-1])

                engine.action("remove", "remove")
                self.assertIsNone(store.get("remove"))
                self.assertEqual(stopped, ["cancel", "remove"])
            finally:
                store._path = original_path  # type: ignore[method-assign]
                queue_engine.request_cancel = original_cancel

    def test_queue_interrupted_waits_for_resume(self):
        from pipeline.queue import engine as queue_engine
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "jobs.json"
            orig_path = store._path
            orig_arm = queue_engine.arm_job
            armed: list[str] = []
            store._path = lambda: path  # type: ignore[method-assign]
            queue_engine.arm_job = lambda job_id: armed.append(job_id) or 1
            try:
                store.insert({"id": "j1", "status": "interrupted", "type": "review", "parts": []})
                engine = object.__new__(queue_engine.QueueEngine)
                engine._pause_all = False
                engine._active = {}
                engine._guard = __import__("threading").Lock()
                engine.kick = lambda: None  # type: ignore[method-assign]
                engine._disk_ok = lambda _dest: True  # type: ignore[method-assign]
                engine._capacity = lambda: 2  # type: ignore[method-assign]
                engine._schedule()
                self.assertEqual((store.get("j1") or {}).get("status"), "interrupted")
                engine.action("j1", "resume")
                self.assertEqual((store.get("j1") or {}).get("status"), "queued")
                self.assertEqual(armed, ["j1"])
            finally:
                store._path = orig_path  # type: ignore[method-assign]
                queue_engine.arm_job = orig_arm

    def test_queue_serializes_jobs_for_same_source(self):
        from pipeline.queue import engine as queue_engine

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.started = False

            def is_alive(self):
                return True

            def start(self):
                self.started = True

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "jobs.json"
            source = str(Path(raw) / "movie.mp4")
            orig_path = store._path
            orig_thread = queue_engine.threading.Thread
            store._path = lambda: path  # type: ignore[method-assign]
            queue_engine.threading.Thread = FakeThread  # type: ignore[assignment]
            try:
                store.insert({"id": "active", "status": "running", "source": source})
                store.insert({"id": "same", "status": "queued", "source": source})
                store.insert({"id": "other", "status": "queued", "source": str(Path(raw) / "other.mp4")})
                engine = object.__new__(queue_engine.QueueEngine)
                engine._pause_all = False
                engine._guard = queue_engine.threading.Lock()
                engine._active = {"active": FakeThread()}
                engine._capacity = lambda: 3  # type: ignore[method-assign]
                engine._disk_ok = lambda path: True  # type: ignore[method-assign]
                engine._schedule()
                self.assertNotIn("same", engine._active)
                self.assertIn("other", engine._active)
            finally:
                store._path = orig_path  # type: ignore[method-assign]
                queue_engine.threading.Thread = orig_thread

    def test_clear_movie_cache(self):
        from pipeline.review import cache as review_cache
        with tempfile.TemporaryDirectory() as raw:
            src = Path(raw) / "film.mp4"
            src.write_bytes(b"video")
            orig = review_cache.DATA
            review_cache.DATA = Path(raw) / "data"
            try:
                root = review_cache.movie_root(src)
                (root / "transcript_auto.json").write_text("[]", encoding="utf-8")
                miss = review_cache.clear_movie_cache(src)
                self.assertTrue(miss["cleared"])
                self.assertFalse(root.exists())
                again = review_cache.clear_movie_cache(src)
                self.assertFalse(again["cleared"])
            finally:
                review_cache.DATA = orig

    def test_review_job_uses_a_fresh_project_workspace(self):
        """Different Review settings for one source must not share cancellation state."""
        from pipeline.clone_run import open_source

        with tempfile.TemporaryDirectory() as raw:
            src = Path(raw) / "film.mp4"
            src.write_bytes(b"video")
            original_find = open_source.find_project_by_fp
            original_layout = open_source.ensure_layout
            original_duration = open_source.ffprobe_duration
            original_save = open_source.save_meta
            original_status = open_source.set_status
            try:
                open_source.find_project_by_fp = lambda _fp: "existing-project"
                open_source.ensure_layout = lambda project_id: Path(raw) / project_id
                open_source.ffprobe_duration = lambda _path: 10.0
                open_source.save_meta = lambda *_args, **_kwargs: None
                open_source.set_status = lambda *_args, **_kwargs: None
                project_id = open_source.open_local_video(
                    str(src), kind="review", reuse_existing=False,
                )
                self.assertNotEqual(project_id, "existing-project")
            finally:
                open_source.find_project_by_fp = original_find
                open_source.ensure_layout = original_layout
                open_source.ffprobe_duration = original_duration
                open_source.save_meta = original_save
                open_source.set_status = original_status

    def test_scene_fallback_long_duration(self):
        scenes = detect_scenes(Path("/no/such.mp4"), 3600)
        self.assertGreater(len(scenes), 10)
        self.assertEqual(scenes[0]["start"], 0)

    def test_matcher_avoids_spoiler_and_repeat(self):
        visuals = [
            {"scene_id": 1, "start": 0, "end": 5, "duration": 5, "description": "hook city night", "plot_score": 0.4, "visual_score": 0.9, "emotion_score": 0.2, "spoiler_score": 0.1, "transcript": "skyline"},
            {"scene_id": 2, "start": 10, "end": 18, "duration": 8, "description": "ending death twist", "plot_score": 0.95, "visual_score": 0.5, "emotion_score": 0.8, "spoiler_score": 0.9, "transcript": "he dies"},
            {"scene_id": 3, "start": 20, "end": 26, "duration": 6, "description": "fight loses control", "plot_score": 0.8, "visual_score": 0.7, "emotion_score": 0.7, "spoiler_score": 0.2, "transcript": "mất kiểm soát"},
        ]
        voice = {"id": "voice_001", "text": "Từ đây mọi chuyện bắt đầu mất kiểm soát.", "duration": 5.6, "purpose": "body", "preferred_scene_ids": [3]}
        plan = match_voice([voice], visuals, style="normal", spoiler="none", mode="fixed")
        ids = [c["scene_id"] for s in plan["segments"] for c in s["clips"]]
        self.assertNotIn(2, ids)
        self.assertIn(3, ids)
        self.assertEqual(plan["segments"][0]["voice_end"] - plan["segments"][0]["voice_start"], 5.6)

    def test_resolve_build_mode_exclusive(self):
        from pipeline.review.match import resolve_build_mode
        self.assertEqual(resolve_build_mode({"buildMode": "stretch"}), "stretch")
        self.assertEqual(resolve_build_mode({"buildMode": "fixed", "cutMode": "accumulate"}), "fixed")
        self.assertEqual(resolve_build_mode({"cutMode": "smart"}), "smart")
        self.assertEqual(resolve_build_mode({}), "accumulate")

    def test_review_mode_uses_its_own_model_provider(self):
        """Ollama mode must not inherit the saved Cloud provider from a draft."""
        from pipeline.review.run import _select_review_model

        self.assertEqual(
            _select_review_model("llm", "gemini", "qwen3:8b", ["qwen3:8b"]),
            "qwen3:8b",
        )
        self.assertIsNone(_select_review_model("translate", "gemini", "auto", []))

    def test_review_provider_invalidates_story_cache(self):
        self.assertEqual(invalidate_from({"reviewProvider"}), "story_graph")
        self.assertEqual(
            _settings_diff(
                {"reviewProvider": "gemini"},
                {"reviewProvider": "openai"},
            ),
            {"reviewProvider"},
        )

    def test_windows_only_accumulate_splits(self):
        from pipeline.review.run import _windows
        hour = {"chunkMinutes": 15}
        self.assertEqual(_windows(3600, "fixed", hour), [(0.0, 3600)])
        self.assertEqual(_windows(3600, "stretch", hour), [(0.0, 3600)])
        self.assertEqual(_windows(3600, "smart", hour), [(0.0, 3600)])
        acc = _windows(3600, "accumulate", hour)
        self.assertEqual(len(acc), 4)
        self.assertEqual(acc[0], (0.0, 900.0))
        self.assertEqual(acc[-1], (2700.0, 3600))

    def test_accumulate_rebalances_tiny_tail_into_previous_parts(self):
        from pipeline.review.run import _windows

        windows = _windows(2462.666667, "accumulate", {"chunkMinutes": 10})
        lengths = [end - start for start, end in windows]

        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0], (0.0, 615.666667))
        self.assertEqual(windows[-1], (1847.0, 2462.666667))
        self.assertLess(max(lengths) - min(lengths), 0.001)

    def test_accumulate_parallelism_bounds_nested_workers(self):
        from pipeline.review.run import _accumulate_worker_limits

        outer, tts, compose = _accumulate_worker_limits(8)
        self.assertEqual(outer, 1)
        self.assertEqual(tts, 24)
        self.assertGreaterEqual(compose, 8)
        self.assertLessEqual(compose, 16)

    def test_story_workers_distinguish_local_and_cloud_models(self):
        from pipeline.review.story import story_pool_fixed, story_workers

        self.assertEqual(story_workers("qwen3:4b"), 8)
        self.assertEqual(story_workers("qwen3:8b"), 6)
        self.assertEqual(story_workers("qwen3:14b"), 4)
        self.assertEqual(story_workers("gemma4:26b"), 2)
        self.assertEqual(story_workers("custom-model"), 6)
        self.assertEqual(story_workers("cloud:gemini:gemini-2.5-flash"), 3)
        self.assertFalse(story_pool_fixed("qwen3:8b"))
        self.assertTrue(story_pool_fixed("cloud:gemini:gemini-2.5-flash"))

    def test_accumulate_match_only_reuses_after_source_exhausted(self):
        voices = [
            {"id": "v1", "duration": 2, "text": "one"},
            {"id": "v2", "duration": 2, "text": "two"},
        ]
        visuals = [
            {"scene_id": 1, "start": 0, "end": 2, "duration": 2, "plot_score": 1, "visual_score": 1, "emotion_score": 0, "spoiler_score": 0},
            {"scene_id": 2, "start": 2, "end": 4, "duration": 2, "plot_score": 1, "visual_score": 1, "emotion_score": 0, "spoiler_score": 0},
        ]
        plan = match_voice(voices, visuals, style="normal", spoiler="full", mode="accumulate")
        # Two short scenes coalesce into one window; the second line reuses it
        # only after the source timeline is exhausted.
        starts = [segment["clips"][0]["source_start"] for segment in plan["segments"]]
        self.assertEqual(starts, [0.0, 0.0])
        self.assertEqual(
            plan["segments"][0]["clips"][0]["scene_id"],
            plan["segments"][1]["clips"][0]["scene_id"],
        )

    def test_accumulate_splits_selected_review_duration(self):
        from pipeline.review.run import _script_duration, _windows

        settings = {"chunkMinutes": 15, "durationSec": 900}
        windows = _windows(3600, "accumulate", settings)
        lengths = [
            _script_duration("accumulate", start, end, 3600, settings, 1.37)
            for start, end in windows
        ]
        self.assertEqual(lengths, [225.0, 225.0, 225.0, 225.0])
        self.assertEqual(sum(lengths), 900.0)

        capped = _windows(12 * 3600, "accumulate", settings)
        covered = sum(end - start for start, end in capped)
        self.assertEqual(len(capped), 40)
        self.assertAlmostEqual(sum(
            _script_duration("accumulate", start, end, covered, settings, 1.0)
            for start, end in capped
        ), 900.0)

    def test_part_cache_requires_current_review_plan_and_target(self):
        from pipeline.review.run import REVIEW_PLAN_VERSION, _part_cache_matches

        self.assertFalse(_part_cache_matches({}, 225.0))
        self.assertFalse(_part_cache_matches(
            {"reviewPlanVersion": 2, "targetDurationSec": 225.0}, 225.0
        ))
        self.assertFalse(_part_cache_matches(
            {"reviewPlanVersion": REVIEW_PLAN_VERSION, "targetDurationSec": 900.0}, 225.0
        ))
        self.assertTrue(_part_cache_matches(
            {"reviewPlanVersion": REVIEW_PLAN_VERSION, "targetDurationSec": 225.0}, 225.0
        ))
        self.assertFalse(_part_cache_matches(
            {"reviewPlanVersion": REVIEW_PLAN_VERSION, "targetDurationSec": 225.0},
            225.0, source_start=0, source_end=60,
        ))
        self.assertTrue(_part_cache_matches(
            {
                "reviewPlanVersion": REVIEW_PLAN_VERSION,
                "targetDurationSec": 225.0,
                "windowSourceVersion": 1,
                "sourceStart": 0,
                "sourceEnd": 60,
            },
            225.0, source_start=0, source_end=60,
        ))

    def test_window_source_cache_binds_source_range(self):
        from pipeline.review.cache import save_json
        from pipeline.review.run import WINDOW_SOURCE_VERSION, _materialize_window

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "input.mp4"
            cache = base / "window"
            source = cache / "source.mp4"
            src.write_bytes(b"input")
            cache.mkdir()
            source.write_bytes(b"window")
            save_json(cache / "source.json", {
                "version": WINDOW_SOURCE_VERSION,
                "source": str(src.resolve()),
                "start": 0.0,
                "end": 60.0,
            })
            with patch("pipeline.review.run.ffprobe_duration", return_value=60.0):
                self.assertEqual(_materialize_window(src, cache, 0, 60, "test"), source)
            with patch("pipeline.review.run.ffprobe_duration", return_value=60.0), patch(
                "pipeline.review.run.run_cmd"
            ) as cut:
                with self.assertRaisesRegex(RuntimeError, "REVIEW_WINDOW_CUT_FAILED"):
                    _materialize_window(src, cache, 60, 120, "test")
                cut.assert_called_once()

    def test_part_export_segments_use_local_timing_and_fixed_review_band(self):
        from pipeline.review.match import match_voice
        from pipeline.review.run import _cap_voiced_duration, _floor_voiced_duration, _part_export_segments

        fitted = _cap_voiced_duration([
            {"id": "p03_voice_001", "duration": 8.0, "text": "Local caption", "audio": "/tmp/p03_voice_001.wav"},
            {"id": "p03_voice_002", "duration": 4.0, "text": "Next caption", "audio": "/tmp/p03_voice_002.wav"},
        ], 6.0)
        self.assertEqual(sum(row["duration"] for row in fitted), 10.0)
        self.assertEqual(fitted[0]["ttsSpeed"], 1.2)
        floored = _floor_voiced_duration([
            {"id": "short", "duration": 70.0, "text": "Short narration"},
        ], 90.0)
        self.assertAlmostEqual(floored[0]["duration"], 90.0, places=1)
        self.assertAlmostEqual(floored[0]["ttsSpeed"], 70 / 90, places=3)
        plan = match_voice(
            fitted,
            [{"scene_id": 1, "start": 0, "end": 20, "duration": 20}],
            style="normal",
            spoiler="full",
        )
        fixed_band = {"x": 0, "y": 864, "w": 1920, "h": 216}
        segment = _part_export_segments(plan, Path("/tmp/raw.mp4"), caption_bbox=fixed_band)[0]
        self.assertEqual((segment["start"], segment["end"]), (0.0, 6.667))
        self.assertEqual(segment["audioFile"], "p03_voice_001.wav")
        self.assertEqual(segment["audioDuration"], 8.0)
        self.assertEqual(segment["ttsSpeed"], 1.2)
        self.assertEqual(segment["source"], "")
        self.assertEqual(segment["translation"], "Local caption")
        self.assertEqual(segment["bbox"], {"x": 0, "y": 864, "w": 1920, "h": 216})

    def test_review_caption_lane_uses_one_consensus_box(self):
        from pipeline.review import adapter

        with patch(
            "pipeline.review.adapter.locate_review_caption_bands",
            return_value=[
                (0.1, {"x": 0, "y": 820, "w": 1920, "h": 88}),
                (0.5, {"x": 0, "y": 824, "w": 1920, "h": 86}),
                (0.9, {"x": 0, "y": 900, "w": 1920, "h": 40}),
            ],
        ), patch("pipeline.core.media.video_size", return_value=(1920, 1080)):
            self.assertEqual(
                adapter.locate_review_caption_band(Path("/tmp/review.mp4")),
                {"x": 0, "y": 822, "w": 1920, "h": 87},
            )

    def test_review_caption_lane_ignores_one_temporal_label_group(self):
        from pipeline.review import adapter

        with patch(
            "pipeline.review.adapter.locate_review_caption_bands",
            return_value=[
                (0.06, {"x": 0, "y": 874, "w": 1920, "h": 74}),
                (0.28, {"x": 0, "y": 876, "w": 1920, "h": 72}),
                (0.50, {"x": 0, "y": 782, "w": 1920, "h": 118}),
                (0.72, {"x": 0, "y": 875, "w": 1920, "h": 73}),
                (0.94, {"x": 0, "y": 873, "w": 1920, "h": 75}),
            ],
        ), patch("pipeline.core.media.video_size", return_value=(1920, 1080)):
            self.assertEqual(
                adapter.locate_review_caption_band(Path("/tmp/review.mp4")),
                {"x": 0, "y": 874, "w": 1920, "h": 74},
            )

    def test_review_cover_lane_masks_the_full_part(self):
        from pipeline.review.run import _review_cover_lane

        with patch("pipeline.review.run.ffprobe_duration", return_value=42.5):
            lane = _review_cover_lane(
                Path("/tmp/review.mp4"), {"x": 0, "y": 824, "w": 1920, "h": 86},
            )
        self.assertEqual((lane["start"], lane["end"]), (0.0, 42.5))
        self.assertTrue(lane["maskOnly"])
        self.assertEqual(lane["bbox"], {"x": 0, "y": 824, "w": 1920, "h": 86})

    def test_review_caption_box_uses_detected_subtitle_lane(self):
        from pipeline.review.adapter import _review_caption_box_from_boxes

        box = _review_caption_box_from_boxes([(300, 650, 900, 682)], 1282, 720)
        self.assertLess(box["x"], 300)
        self.assertGreater(box["x"] + box["w"], 900)
        self.assertLess(box["y"], 650)
        self.assertGreater(box["y"] + box["h"], 682)

    def test_review_cover_keeps_the_full_top_edge_of_hardsub(self):
        from pipeline.export.burn_parts.layout_geo import _fit_hardsub_box

        source = (300, 900, 1500, 970)
        cover = _fit_hardsub_box(source, 900, 48, 1920, 1080)

        # The cover may expand, but must never crop the detected ink from the
        # top as the former `top_slack` calculation did.
        self.assertLess(cover[1], source[1])
        self.assertGreaterEqual(cover[3], source[3])

    def test_review_caption_cues_are_brief_and_cover_audio_timing(self):
        from pipeline.review.run import _review_caption_cues

        text = (
            "Chàng trai tóc đỏ đưa tấm thẻ cho cô bé, "
            "khiến bầu không khí giữa họ trở nên căng thẳng hơn."
        )
        cues = _review_caption_cues([
            {"id": "voice_001", "start": 2.0, "end": 12.0, "translation": text}
        ])
        self.assertGreater(len(cues), 1)
        self.assertEqual(cues[0]["start"], 2.0)
        self.assertEqual(cues[-1]["end"], 12.0)
        self.assertTrue(all(len(cue["translation"]) <= 38 for cue in cues))
        self.assertTrue(all(
            cues[index]["end"] == cues[index + 1]["start"]
            for index in range(len(cues) - 1)
        ))

    def test_story_summaries_run_with_bounded_parallel_workers(self):
        from pipeline.review.story import STORY_WORKERS, _parallel_summaries

        progress: list[tuple[str, int, int, int]] = []
        rows = _parallel_summaries(
            [1, 2, 3],
            lambda item: {"item": item},
            stage="blocks",
            on_progress=lambda stage, done, total, workers: progress.append(
                (stage, done, total, workers)
            ),
        )
        self.assertEqual(rows, [{"item": 1}, {"item": 2}, {"item": 3}])
        self.assertEqual(progress[-1][:3], ("blocks", 3, 3))
        self.assertEqual(progress[-1][3], min(STORY_WORKERS, 3))

    def test_parts_cache_requires_raw_and_finished_media(self):
        from pipeline.review.cache import save_json
        from pipeline.review.run import REVIEW_PLAN_VERSION, _parts_complete

        with tempfile.TemporaryDirectory() as raw:
            rd = Path(raw)
            save_json(rd / "script_00.json", {
                "reviewPlanVersion": REVIEW_PLAN_VERSION,
                "segments": [{"id": "v1"}],
            })
            save_json(rd / "plan_00.json", {"segments": [{"voice_id": "v1"}]})
            save_json(rd / "voice_00.json", [{"id": "v1"}])
            save_json(rd / "final_00.json", {
                "reviewPlanVersion": REVIEW_PLAN_VERSION,
                "finalizeKey": "key",
            })
            raw_part = rd / "raw_part_00.mp4"
            finished = rd / "part_00.mp4"
            raw_part.write_bytes(b"raw")
            finished.write_bytes(b"finished")
            with patch("pipeline.review.run.ffprobe_duration", return_value=1.0):
                self.assertTrue(_parts_complete(rd, 1, "key"))
                raw_part.unlink()
                self.assertFalse(_parts_complete(rd, 1, "key"))

    def test_mux_dub_parallel_destination_and_namespace(self):
        from pipeline.export import mux_audio

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "cache").mkdir()
            video = root / "raw.mp4"
            voice = root / "voice.wav"
            dest = root / "part_00.mp4"
            video.write_bytes(b"video")
            voice.write_bytes(b"voice")
            commands: list[list[str]] = []
            with (
                patch.object(mux_audio, "ensure_layout", return_value=root),
                patch.object(mux_audio, "ffprobe_duration", return_value=10.0),
                patch.object(mux_audio, "_tts_clip_plan", return_value=([], 1.0)),
                patch.object(mux_audio, "_mix_tts_track", return_value=voice),
                patch.object(mux_audio, "_has_audio_stream", return_value=False),
                patch.object(mux_audio, "run_cmd", side_effect=lambda _pid, cmd: commands.append(cmd)),
            ):
                out = mux_audio.mux_dub(
                    "project",
                    video,
                    [{"id": "v1", "start": 0, "end": 2}],
                    original_audio_mode="mute",
                    destination=dest,
                    namespace="review-run/part-00",
                )
            self.assertEqual(out, dest)
            self.assertEqual(Path(commands[-1][-1]), dest)
            self.assertTrue((root / "cache" / "mux_fc_review-run_part-00.txt").is_file())

    def test_final_concat_is_copy_only_and_namespaced(self):
        from pipeline.review.compose import concat_parts

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dest = root / "review_final.mp4"
            with patch(
                "pipeline.review.compose.run_cmd",
                side_effect=RuntimeError("copy concat failed"),
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "copy concat failed"):
                    concat_parts(
                        [root / "part_00.mp4", root / "part_01.mp4"],
                        dest,
                        job_id="job",
                        reencode_fallback=False,
                    )
            self.assertIn("-c", run.call_args.args[1])
            self.assertIn("copy", run.call_args.args[1])
            self.assertTrue((root / "concat_review_final.txt").is_file())

    def test_part_timeline_uses_completed_output_duration(self):
        from pipeline.review.run import _refresh_part_timeline
        parts = [
            {"sourceStart": 0, "sourceEnd": 900, "outputDuration": 952},
            {"sourceStart": 900, "sourceEnd": 1800, "outputDuration": None},
        ]
        _refresh_part_timeline(parts)
        self.assertEqual(parts[0]["label"], "00:00 - 15:52")
        self.assertEqual(parts[1]["label"], "15:52 - 30:52")

    def test_tts_cache_requires_matching_clean_text(self):
        from pipeline.review.run import _attach_prev_voice
        with tempfile.TemporaryDirectory() as raw:
            wav = Path(raw) / "voice.wav"
            wav.write_bytes(b"wav")
            script = {"segments": [{"id": "voice_001", "text": "Câu chuyện tiếp tục."}]}
            dirty = [{"id": "p00_voice_001", "text": "Sang phần 36. Câu chuyện tiếp tục.", "duration": 2, "audio": str(wav)}]
            clean = [{"id": "p00_voice_001", "text": "Câu chuyện tiếp tục.", "duration": 2, "audio": str(wav)}]
            self.assertIsNone(_attach_prev_voice(script, dirty, 0))
            self.assertIsNotNone(_attach_prev_voice(script, clean, 0))

    def test_accumulate_walks_forward(self):
        visuals = [
            {"scene_id": 1, "start": 0, "end": 8, "duration": 8, "plot_score": 0.2, "visual_score": 0.2, "emotion_score": 0.1, "spoiler_score": 0},
            {"scene_id": 2, "start": 8, "end": 16, "duration": 8, "plot_score": 0.9, "visual_score": 0.9, "emotion_score": 0.5, "spoiler_score": 0},
        ]
        voices = [
            {"id": "a", "text": "một", "duration": 3},
            {"id": "b", "text": "hai", "duration": 3},
        ]
        plan = match_voice(voices, visuals, style="normal", spoiler="full", mode="accumulate")
        starts = [c["source_start"] for s in plan["segments"] for c in s["clips"]]
        self.assertEqual(starts, sorted(starts))
        self.assertFalse(any("target_duration" in c for s in plan["segments"] for c in s["clips"]))

    def test_accumulate_windows_coalesce_micro_scenes(self):
        from pipeline.review.match import accumulate_windows

        micro_scenes = [
            {"scene_id": i, "start": i, "end": i + 1, "duration": 1, "plot_score": i}
            for i in range(12)
        ]
        windows = accumulate_windows(micro_scenes, min_duration=6)
        self.assertEqual(len(windows), 2)
        self.assertEqual(
            [(window["start"], window["end"]) for window in windows],
            [(0.0, 6.0), (6.0, 12.0)],
        )
        self.assertEqual(windows[1]["member_scene_ids"], [6, 7, 8, 9, 10, 11])

    def test_accumulate_matches_narration_to_preferred_source_scenes(self):
        visuals = [
            {
                "scene_id": i, "start": i, "end": i + 1, "duration": 1,
                "description": f"scene {i}", "plot_score": 0.1,
                "visual_score": 0.1, "emotion_score": 0.1, "spoiler_score": 0,
            }
            for i in range(24)
        ]
        voices = [
            {
                "id": "early", "text": "sự kiện đầu", "duration": 3,
                "preferred_scene_ids": [2],
            },
            {
                "id": "late", "text": "sự kiện sau", "duration": 3,
                "preferred_scene_ids": [20],
            },
        ]

        plan = match_voice(
            voices, visuals, style="normal", spoiler="full", mode="accumulate",
        )

        self.assertEqual(plan["segments"][0]["clips"][0]["source_start"], 0.0)
        self.assertEqual(plan["segments"][1]["clips"][0]["source_start"], 18.0)

    def test_accumulate_overflow_does_not_steal_later_preferred_scenes(self):
        """Multi-clip cover must not leapfrog past the next line's evidence."""
        visuals = [
            {
                "scene_id": i,
                "start": float(i),
                "end": float(i + 1),
                "duration": 1.0,
                # Later scenes look "better" so the old scorer jumped ahead.
                "description": f"scene {i}",
                "plot_score": 0.1 + (i / 100.0),
                "visual_score": 0.1 + (i / 100.0),
                "emotion_score": 0.1,
                "spoiler_score": 0,
            }
            for i in range(80)
        ]
        voices = [
            {"id": "a", "text": "đầu", "duration": 5.8, "preferred_scene_ids": [0]},
            {"id": "b", "text": "giữa", "duration": 4.0, "preferred_scene_ids": [20]},
            {"id": "c", "text": "sau", "duration": 4.0, "preferred_scene_ids": [40]},
        ]
        plan = match_voice(
            voices, visuals, style="normal", spoiler="full", mode="accumulate",
        )
        starts = [
            segment["clips"][0]["source_start"]
            for segment in plan["segments"]
        ]
        self.assertLess(starts[0], 3.0)
        self.assertGreaterEqual(starts[1], 18.0)
        self.assertLess(starts[1], 24.0)
        self.assertGreaterEqual(starts[2], 36.0)
        self.assertLess(starts[2], 48.0)
        # Overflow clips of line A stay before line B's preferred window.
        a_clips = plan["segments"][0]["clips"]
        self.assertGreaterEqual(len(a_clips), 2)
        self.assertLess(a_clips[-1]["source_start"], starts[1])

    def test_stretch_sets_target_duration(self):
        visuals = [{"scene_id": 1, "start": 0, "end": 4, "duration": 4, "plot_score": 0.5, "visual_score": 0.5, "emotion_score": 0.2, "spoiler_score": 0}]
        plan = match_voice([{"id": "a", "text": "x", "duration": 8}], visuals, style="normal", spoiler="full", mode="stretch")
        clip = plan["segments"][0]["clips"][0]
        self.assertGreater(clip["target_duration"], 4)
        self.assertAlmostEqual(clip["target_duration"], 8, places=1)

    def test_smart_keep_skip_windows(self):
        from pipeline.review.match import keep_skip_windows
        visuals = [{"scene_id": 1, "start": 0, "end": 30, "duration": 30, "plot_score": 0.5, "visual_score": 0.5, "emotion_score": 0, "spoiler_score": 0}]
        wins = keep_skip_windows(visuals, 4, 10)
        self.assertGreaterEqual(len(wins), 2)
        self.assertAlmostEqual(wins[0]["duration"], 4, places=1)
        self.assertAlmostEqual(wins[1]["start"], 14, places=1)

    def test_score_prefers_preferred_id(self):
        scene = {"scene_id": 9, "description": "x", "plot_score": 0.1, "visual_score": 0.1, "emotion_score": 0.1, "spoiler_score": 0}
        high = score_scene({"text": "", "preferred_scene_ids": [9]}, scene, used={}, spoiler="full", last_id=None)
        low = score_scene({"text": "", "preferred_scene_ids": []}, scene, used={}, spoiler="full", last_id=None)
        self.assertGreater(high, low)
        self.assertTrue(tokenize("mất kiểm soát"))

    def test_caption_export_settings(self):
        from pipeline.review.adapter import _fallback_review_bbox, caption_export_settings
        cover = caption_export_settings({"language": "vi", "subtitle": True})
        self.assertEqual(cover["targetLang"], "vi")
        self.assertTrue(cover["coverHardsubs"])
        self.assertTrue(cover["burnSubs"])
        self.assertEqual(cover["captionPlacement"], "over")
        above = caption_export_settings({"language": "vi", "captionMode": "above"})
        self.assertFalse(above["coverHardsubs"])
        self.assertTrue(above["burnSubs"])
        self.assertEqual(above["captionPlacement"], "above")
        off = caption_export_settings({"language": "en", "subtitle": False})
        self.assertEqual(off["targetLang"], "none")
        self.assertFalse(off["burnSubs"])
        disabled = caption_export_settings({"language": "vi", "subtitle": True, "captionMode": "off"})
        self.assertEqual(disabled["targetLang"], "none")
        self.assertFalse(disabled["burnSubs"])
        self.assertFalse(disabled["coverHardsubs"])
        self.assertEqual(
            _fallback_review_bbox(1920, 1080),
            {"x": 0, "y": 983, "w": 1920, "h": 70},
        )

    def test_filter_complex_args_inline(self):
        from pipeline.core.media import filter_complex_args
        self.assertEqual(filter_complex_args("scale=2:2"), ["-filter_complex", "scale=2:2"])
        self.assertNotIn("-filter_complex_script", filter_complex_args("null"))

    def test_invalidate_duration_keeps_story(self):
        self.assertEqual(invalidate_from({"durationSec"}), "script")
        self.assertEqual(invalidate_from({"reviewPlanVersion"}), "script")
        self.assertEqual(invalidate_from({"reviewModel"}), "story_graph")
        self.assertEqual(invalidate_from({"reviewMode"}), "story_graph")
        self.assertEqual(invalidate_from({"reviewMatchVersion"}), "matching")
        self.assertEqual(invalidate_from({"voice"}), "tts")
        self.assertEqual(invalidate_from({"ratio"}), "matching")
        self.assertEqual(invalidate_from({"buildMode"}), "matching")
        self.assertEqual(invalidate_from({"captionMode"}), "render")
        self.assertEqual(invalidate_from({"originalAudioPct"}), "matching")
        self.assertEqual(invalidate_from({"notes"}), "script")
        self.assertTrue(_reuse("tts", "script"))
        self.assertFalse(_reuse("tts", "tts"))
        self.assertTrue(_reuse("render", "matching"))
        self.assertEqual(_settings_diff({"voice": "a", "outputDir": "/x"}, {"voice": "b", "outputDir": "/y"}), {"voice"})
        self.assertEqual(_settings_diff({"voice": "a", "originalAudioPct": 18}, {"voice": "a", "originalAudioPct": 18.0}), set())
        self.assertEqual(invalidate_from({"sourceLang"}), "transcript")
        self.assertEqual(invalidate_from({"language"}), "script")

    def test_scripts_from_edit_plan(self):
        from pipeline.review.run import _scripts_from_plan
        plan = {
            "segments": [
                {"voice_id": "p00_voice_001", "text": "một"},
                {"voice_id": "p00_voice_002", "text": "hai"},
                {"voice_id": "p01_voice_001", "text": "ba"},
            ]
        }
        rows = _scripts_from_plan(plan, 2)
        self.assertEqual(len(rows[0]["segments"]), 2)
        self.assertEqual(rows[1]["segments"][0]["text"], "ba")
        self.assertIsNone(_scripts_from_plan(plan, 3))

    def test_translate_mode_rejects_untranslated_source(self):
        from pipeline.review import script as sc
        orig_g, orig_t = sc.generate_json, sc._translate_beats
        sc.generate_json = lambda *a, **k: None
        sc._translate_beats = lambda texts, language, project_id=None: []
        try:
            story = {
                "movie_context": {"logline": "你好世界测试这段中文还要更长"},
                "chapters": [{"summary": "你好场景一", "start": 0, "end": 10, "scene_ids": [0], "index": 0}],
                "blocks": [],
                "story_graph": {"events": [{"summary": "Scene 0", "spoiler_level": 0, "scene_ids": [0], "event_id": "e0"}]},
            }
            with self.assertRaisesRegex(RuntimeError, "REVIEW_TRANSLATION_EMPTY"):
                sc.write_script(story, duration_sec=120, style="normal", language="vi", spoiler="none")
        finally:
            sc.generate_json = orig_g
            sc._translate_beats = orig_t

    def test_translate_mode_keeps_source_order(self):
        from pipeline.review import script as sc
        orig_g, orig_t = sc.generate_json, sc._translate_beats
        sc.generate_json = lambda *a, **k: None
        sc._translate_beats = lambda texts, language, project_id=None: list(texts)
        try:
            story = {
                "movie_context": {"logline": "Một đứa trẻ ba tuổi đảo lộn cả gia tộc."},
                "chapters": [],
                "blocks": [],
                "story_graph": {"events": [
                    {"event_id": "evt_001", "summary": "Cô bé khiến cả nhà phải gọi bằng cô nội.", "start": 0, "end": 20, "scene_ids": [1]},
                    {"event_id": "evt_002", "summary": "Ông nội lần đầu gặp lại người thừa kế thất lạc.", "start": 20, "end": 40, "scene_ids": [2]},
                ]},
            }
            out = sc.write_script(
                story, duration_sec=72, style="normal", language="vi", spoiler="none",
                source_transcript=[
                    {"start": 0, "end": 5, "text": "Cô bé khiến cả nhà phải gọi bằng cô nội."},
                    {"start": 5, "end": 10, "text": "Ông nội lần đầu gặp lại người thừa kế thất lạc."},
                ],
                visuals=[
                    {"scene_id": 1, "start": 0, "end": 5},
                    {"scene_id": 2, "start": 5, "end": 10},
                ],
            )
            joined = " ".join(s["text"] for s in out["segments"])
            self.assertIn("gia tộc", joined)
            self.assertGreaterEqual(len(out["segments"]), 1)
            self.assertEqual(out["segments"][0]["event_refs"], ["src_00000"])
        finally:
            sc.generate_json = orig_g
            sc._translate_beats = orig_t

    def test_llm_script_requires_budget_and_evidence_refs(self):
        from pipeline.review.script import _script_is_usable

        short = {"segments": [{"text": "một hai", "event_refs": ["evt_001"], "preferred_scene_ids": [1]} for _ in range(3)]}
        full = {"segments": [{"text": "một hai ba bốn", "event_refs": ["evt_001"], "preferred_scene_ids": [1]} for _ in range(3)]}
        self.assertFalse(_script_is_usable(short, 10, {"evt_001"}, {1}))
        self.assertTrue(_script_is_usable(full, 10, {"evt_001"}, {1}))
        full["segments"][0]["event_refs"] = ["unknown"]
        self.assertFalse(_script_is_usable(full, 10, {"evt_001"}, {1}))

    def test_slice_story_keeps_window(self):
        from pipeline.review.run import _slice_story
        story = {
            "blocks": [
                {"summary": "a", "start": 0, "end": 100, "scene_ids": [1]},
                {"summary": "b", "start": 900, "end": 1000, "scene_ids": [9]},
            ],
            "chapters": [
                {"summary": "c1", "start": 0, "end": 400, "scene_ids": [1], "index": 0},
                {"summary": "c2", "start": 900, "end": 1400, "scene_ids": [9], "index": 1},
            ],
            "story_graph": {"acts": [], "events": [{"summary": "e", "scene_ids": [9], "start": 900, "end": 910}]},
            "movie_context": {},
        }
        sliced = _slice_story(story, [{"scene_id": 1, "start": 0, "end": 50}])
        self.assertEqual([b["summary"] for b in sliced["blocks"]], ["a"])
        self.assertEqual([c["summary"] for c in sliced["chapters"]], ["c1"])
        self.assertEqual(sliced["story_graph"]["events"], [])

    def test_faithful_story_keeps_source_order_without_llm(self):
        from pipeline.review.run import _faithful_story

        story = _faithful_story([
            {"scene_id": 1, "start": 0, "end": 5, "transcript": "Đầu câu chuyện.", "plot_score": 0.2},
            {"scene_id": 2, "start": 5, "end": 10, "transcript": "Tiếp theo xảy ra biến cố.", "plot_score": 0.6},
        ])
        self.assertEqual(story["story_graph"]["events"][0]["start"], 0)
        self.assertIn("Đầu câu chuyện", story["blocks"][0]["summary"])
        self.assertEqual(story["movie_context"]["logline"], "")

    def test_review_script_prompt_uses_part_evidence(self):
        from pipeline.review import script as sc

        prompts: list[str] = []
        original = sc.generate_json
        sc.generate_json = lambda prompt, **_kwargs: (
            prompts.append(prompt)
            or {"segments": [
                {"id": "a", "text": "Cô bé gặp lại gia đình sau biến cố bất ngờ, khiến mọi người phải nhìn lại những lựa chọn và bí mật đã che giấu trong suốt thời gian dài.", "event_refs": ["evt_000"], "preferred_scene_ids": [9]},
                {"id": "b", "text": "Người thân nhận ra thân phận thật của cô bé và cùng nhau đối diện hậu quả từ quyết định cũ, trong khi mối quan hệ trong nhà bắt đầu thay đổi rõ rệt.", "event_refs": ["evt_000"], "preferred_scene_ids": [9]},
                {"id": "c", "text": "Cuộc gặp khiến cả gia tộc thay đổi cách cư xử, đồng thời mở ra thử thách mới buộc từng người phải lựa chọn giữa lợi ích riêng và sự bảo vệ cô bé.", "event_refs": ["evt_000"], "preferred_scene_ids": [9]},
            ]}
        )
        try:
            sc.write_script(
                {"movie_context": {}, "story_graph": {"events": []}},
                duration_sec=36,
                style="recap",
                language="vi",
                spoiler="none",
                visuals=[{
                    "scene_id": 9, "start": 900, "end": 906,
                    "transcript": "Cô bé trở về gặp gia đình.",
                }],
                use_llm=True,
            )
        finally:
            sc.generate_json = original
        self.assertIn("middle or later section", prompts[0])
        self.assertIn("evt_000 900.0-906.0 scenes=[9]: Cô bé trở về gặp gia đình.", prompts[0])

    def test_short_review_part_uses_one_clean_voice_line(self):
        from pipeline.review import script as sc

        original = sc.generate_json
        sc.generate_json = lambda *_args, **_kwargs: {
            "script": ["Câu 2: Cô bé phát hiện bí mật khiến cả gia đình im lặng."]
        }
        try:
            result = sc.write_script(
                {"movie_context": {}, "story_graph": {"events": []}},
                duration_sec=9,
                style="normal",
                language="vi",
                spoiler="none",
                visuals=[{"scene_id": 1, "start": 0, "end": 9, "transcript": "Một bí mật được hé lộ."}],
                use_llm=True,
            )
        finally:
            sc.generate_json = original
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["segments"][0]["text"], "Cô bé phát hiện bí mật khiến cả gia đình im lặng.")

    def test_llm_script_progress_targets_the_review_job(self):
        from pipeline.review import script as sc

        original = sc.generate_json
        sc.generate_json = lambda *_args, **_kwargs: {
            "script": ["Nội dung lời kể đủ dài để kiểm tra nhật ký tiến trình."]
        }
        try:
            with patch("pipeline.review.run._note") as note:
                sc.write_script(
                    {"movie_context": {}, "story_graph": {"events": []}},
                    duration_sec=9,
                    style="normal",
                    language="vi",
                    spoiler="none",
                    visuals=[{"scene_id": 1, "start": 0, "end": 9, "transcript": "Một cảnh phim."}],
                    job_id="review-job-123",
                    use_llm=True,
                )
        finally:
            sc.generate_json = original
        self.assertTrue(any(
            call.args[0] == "review-job-123" and "LLM đang viết kịch bản" in call.args[1]
            for call in note.call_args_list
        ))

    def test_review_transcript_emits_small_progress_logs(self):
        from pipeline.review import transcript as tr

        with tempfile.TemporaryDirectory() as raw, patch(
            "pipeline.review.transcript.extract_audio",
        ), patch(
            "pipeline.review.transcript.asr_whisper",
            side_effect=lambda *_args, on_progress, **_kwargs: (
                on_progress(3, 17.8) or [{"start": 0, "end": 1, "source": "Xin chào"}]
            )), patch("pipeline.review.run._note") as note:
            tr._whisper_chunks(
                Path(raw) / "input.mp4", Path(raw), job_id="review-job-456",
                duration=20, source_lang="auto",
            )
        messages = [str(call.args[1]) for call in note.call_args_list]
        self.assertTrue(any("chuẩn bị audio" in message for message in messages))
        self.assertTrue(any("đã nhận 3 câu · 89%" in message for message in messages))
        self.assertTrue(any("hoàn tất · 1 câu" in message for message in messages))

    def test_review_heartbeat_reports_ongoing_blocking_stage(self):
        from pipeline.review.run import PROGRESS_HEARTBEAT_PREFIX, _progress_heartbeat

        with patch("pipeline.review.run._note") as note:
            with _progress_heartbeat("review-job-789", "compose", interval_sec=0.01, emit=True):
                time.sleep(0.04)
        messages = [str(call.args[1]) for call in note.call_args_list]
        self.assertTrue(any(
            message.startswith(f"{PROGRESS_HEARTBEAT_PREFIX}|compose|")
            for message in messages
        ))

    def test_remove_job_artifacts_deletes_only_owned_outputs_and_cache(self):
        from pipeline.queue import engine

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.mp4"
            output = root / "exports" / "review.mp4"
            cache = root / "data" / "review_cache" / "movie-key"
            project = root / "public" / "project-1"
            for path in (source, output, cache / "part.mp4", project / "cache" / "work.mp4"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"data")
            with patch.object(engine, "DATA", root / "data"), patch.object(engine, "PUBLIC_DATA", root / "public"):
                removed = engine.remove_job_artifacts({
                    "source": str(source),
                    "output": str(output),
                    "cacheRefs": {"root": str(cache)},
                    "projectId": "project-1",
                })
            self.assertEqual(removed, 3)
            self.assertTrue(source.is_file())
            self.assertFalse(output.exists())
            self.assertFalse(cache.exists())
            self.assertFalse(project.exists())

    def test_review_segment_count_scales_without_fixed_bounds(self):
        from pipeline.review.script import _segment_count

        self.assertEqual(_segment_count(9), 1)
        self.assertEqual(_segment_count(72), 4)
        self.assertEqual(_segment_count(3 * 60 * 60), 600)

    def test_finalize_drops_scene_labels(self):
        from pipeline.review.script import _dedupe_text, _finalize_line, _pad_lines, _strip_scene_marks, scrub_script
        self.assertEqual(_finalize_line("Scene 578", "vi"), "")
        self.assertEqual(_finalize_line("Cảnh 84 Cảnh 1120 Cảnh 1300", "vi"), "")
        self.assertEqual(_finalize_line("ene 83 Cảnh 84", "vi"), "")
        self.assertEqual(_finalize_line("Sang phần 36.", "vi"), "")
        self.assertEqual(_finalize_line("Câu 3: Một bí mật vừa được hé lộ.", "vi"), "Một bí mật vừa được hé lộ")
        cleaned = _finalize_line("Sang phần 36. Câu chuyện tiếp tục với một thử thách mới.", "vi")
        self.assertEqual(cleaned, "Câu chuyện tiếp tục với một thử thách mới")
        self.assertNotIn("phần", " ".join(_pad_lines([], 12, "vi")).lower())
        self.assertEqual(_strip_scene_marks("Scene 0 你是什么员吗 Scene 40"), "你是什么员吗")
        self.assertIn("gia tộc", _finalize_line("Cô bé ba tuổi đảo lộn cả gia tộc nhà họ Thẩm.", "vi"))
        repeated = _dedupe_text(
            "Bạn là ai? Bạn là ai? Tôi là dì cố của bạn. "
            "Tôi là dì cố của bạn. Ông cố đã mất từ lâu. Ông cố đã mất từ lâu."
        )
        self.assertEqual(repeated.count("Bạn là ai"), 1)
        self.assertEqual(repeated.count("Tôi là dì cố của bạn"), 1)
        self.assertEqual(repeated.count("Ông cố đã mất từ lâu"), 1)
        dirty = {"segments": [
            {"id": "voice_001", "text": "Scene 0"},
            {"id": "voice_002", "text": "Cảnh 84 Cảnh 1120"},
            {"id": "voice_003", "text": "Cô bé ba tuổi đảo lộn cả gia tộc nhà họ Thẩm."},
        ]}
        self.assertIsNone(scrub_script(dirty, "vi"))

    def test_voiceover_keeps_target_script(self):
        from pipeline.mt.text import _lang_name
        from pipeline.review.script import _in_voiceover_lang
        from pipeline.review.transcript import _script_matches_source
        self.assertEqual(_lang_name("vi"), "Vietnamese")
        self.assertFalse(_in_voiceover_lang("你好世界测试", "vi"))
        self.assertTrue(_in_voiceover_lang("Từ đây mọi chuyện bắt đầu.", "vi"))
        self.assertTrue(_in_voiceover_lang("The story begins here.", "en"))
        self.assertFalse(_script_matches_source("Hello everyone, welcome back to the show.", "zh"))
        self.assertTrue(_script_matches_source("你好，欢迎来到这个故事。今天我们来看。", "zh"))
        self.assertTrue(_script_matches_source("Hello everyone, welcome back to the show.", "en"))

    def test_resolve_job_file_part_and_output(self):
        from fastapi import HTTPException
        from api.routes.queue import resolve_job_file
        with tempfile.TemporaryDirectory() as raw:
            part = Path(raw) / "p01.mp4"
            full = Path(raw) / "out.mp4"
            part.write_bytes(b"x")
            full.write_bytes(b"y")
            job = {"output": str(full), "parts": [{"index": 1, "output": str(part), "status": "done"}]}
            self.assertEqual(resolve_job_file(job, 1), part)
            self.assertEqual(resolve_job_file(job, None), full)
            with self.assertRaises(HTTPException):
                resolve_job_file(job, 9)


if __name__ == "__main__":
    unittest.main()
