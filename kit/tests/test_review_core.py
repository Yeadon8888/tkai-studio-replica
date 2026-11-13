"""review-core 任务的回归测试。"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import common  # noqa: E402
import gate  # noqa: E402
import loop  # noqa: E402
import refine_loop  # noqa: E402
import review_panel  # noqa: E402
import tt_research  # noqa: E402


class JsonParsing(unittest.TestCase):
    def test_fence_and_surrounding_text(self):
        text = '说明在前\n```json\n{"ok": true, "text": "括号 } 不截断"}\n```\n说明在后'
        self.assertEqual(common.parse_json(text), {"ok": True, "text": "括号 } 不截断"})

    def test_failure_returns_none_and_compat_wrapper_uses_common(self):
        self.assertIsNone(common.parse_json("没有 JSON"))
        # 输出被截断：外层对象不完整时，不能把内层的 scores 子对象当成整份结果
        truncated = '```json\n{"judge": "V", "scores": {"visual_quality": 3}, "checks": {"visual_quality": {"f'
        self.assertIsNone(common.parse_json(truncated, require=("scores",)))
        self.assertEqual(common.parse_json(truncated), {"visual_quality": 3})
        with mock.patch.object(common, "parse_json", return_value={"x": 1}) as parser:
            self.assertEqual(tt_research.parse_json_obj("任意"), {"x": 1})
        parser.assert_called_once_with("任意")


class GenerationReview(unittest.TestCase):
    def _project(self, root):
        project = Path(root) / "project"
        (project / "briefs").mkdir(parents=True)
        (project / "jobs").mkdir()
        brief = {"id": "T1", "prompt": "p", "images": ["ref.png"], "duration": 10}
        (project / "briefs" / "T1.json").write_text(json.dumps(brief), encoding="utf-8")
        job = {"id": "T1", "task_id": "task-1", "status": "submitted"}
        (project / "jobs" / "T1.json").write_text(json.dumps(job), encoding="utf-8")
        return project

    def test_remote_url_saved_and_review_runs_without_printing_url(self):
        with tempfile.TemporaryDirectory() as root:
            project = self._project(root)
            args = type("A", (), {"project": str(project), "brief": str(project / "briefs/T1.json"),
                                  "cap": 1, "interval": 0, "max_polls": 1, "no_review": False})
            response = {"status": "completed", "video_url": "https://example.test/private.mp4"}
            output = io.StringIO()
            with mock.patch.object(loop, "_api", return_value=response), \
                 mock.patch.object(loop.time, "sleep"), \
                 mock.patch.object(loop.urllib.request, "urlopen", return_value=io.BytesIO(b"video")), \
                 mock.patch("review_panel.review_take", return_value={"decision": "pass"}) as review, \
                 mock.patch("sys.stdout", output), mock.patch.object(loop.ledger, "append"):
                loop.cmd_gen(args)
            job = json.loads((project / "jobs/T1.json").read_text(encoding="utf-8"))
            self.assertEqual(job["remote_url"], response["video_url"])
            self.assertIn("remote_url_at", job)
            self.assertNotIn(response["video_url"], output.getvalue())
            review.assert_called_once_with(project, "T1", ["ref.png"])

    def test_no_review_is_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            project = self._project(root)
            args = type("A", (), {"project": str(project), "brief": str(project / "briefs/T1.json"),
                                  "cap": 1, "interval": 0, "max_polls": 1, "no_review": True})
            response = {"status": "completed", "url": "https://example.test/v.mp4"}
            with mock.patch.object(loop, "_api", return_value=response), mock.patch.object(loop.time, "sleep"), \
                 mock.patch.object(loop.urllib.request, "urlopen", return_value=io.BytesIO(b"video")), \
                 mock.patch.object(loop.ledger, "append"):
                loop.cmd_gen(args)
            job = json.loads((project / "jobs/T1.json").read_text(encoding="utf-8"))
            self.assertTrue(job["review_skipped"])
            self.assertIn("--no-review", job["review_skip_reason"])


class ReviewInput(unittest.TestCase):
    def test_expert_receives_reference_and_video_without_frames(self):
        with tempfile.TemporaryDirectory() as root:
            ref = Path(root) / "ref.png"
            ref.write_bytes(b"png")
            exp = {"id": "P", "name": "产品", "prompt": "{shared_rules}\n{focus}",
                   "shared_rules": "规则", "focus": "检查", "temperature": 0.1}
            response = json.dumps({"scores": {"product_consistency": 9},
                                   "checks": {"product_consistency": {"shape": "通过，形状一致"}},
                                   "issues": [], "vetoes": [], "notes": ""})
            with mock.patch.object(review_panel.loop, "gemini", return_value=response) as gemini:
                result = review_panel.run_expert(exp, "p", "T1", {}, "anchor", [ref],
                                                 video_url="https://example.test/v.mp4")
            parts = gemini.call_args.args[0]
            self.assertEqual([p["type"] for p in parts], ["text", "image_url", "video_url"])
            self.assertEqual(result["checks"]["product_consistency"]["shape"], "通过")
            self.assertIn("形状一致", result["notes"])

    def test_remote_url_is_preferred(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / "jobs").mkdir()
            (project / "jobs/T1.json").write_text(json.dumps({"remote_url": "https://remote/v.mp4"}), encoding="utf-8")
            with mock.patch.object(review_panel, "_reachable", return_value=True), \
                 mock.patch.object(review_panel.media_host, "upload") as upload:
                mode, url, info = review_panel._video_source(project, "T1", project / "takes/T1.mp4")
            self.assertEqual((mode, url, info), ("video", "https://remote/v.mp4", None))
            upload.assert_not_called()

    def test_media_upload_is_deleted_and_logged(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / "briefs").mkdir()
            (project / "takes").mkdir()
            (project / "briefs/T1.json").write_text(json.dumps({"id": "T1"}), encoding="utf-8")
            (project / "takes/T1.mp4").write_bytes(b"x")
            info = {"url": "https://temp/v.mp4", "repo": "r", "path": "p", "sha": "s"}
            expert = {"scores": {}, "checks": {}, "issues": [], "vetoes": []}
            with mock.patch.object(review_panel, "machine_checks", return_value={"blocking": [], "info": [], "facts": {}}), \
                 mock.patch.object(review_panel, "_video_source", return_value=("video", info["url"], info)), \
                 mock.patch.object(review_panel, "run_expert", return_value=expert), \
                 mock.patch.object(review_panel.gate, "merge_judges", return_value={"scores": {}}), \
                 mock.patch.object(review_panel.gate, "evaluate", return_value={"decision": "pass"}), \
                 mock.patch.object(review_panel.media_host, "delete", return_value=True) as delete, \
                 mock.patch.object(review_panel.media_host, "log") as log:
                review_panel.review_take(project, "T1", [])
            delete.assert_called_once_with(info)
            self.assertTrue(log.call_args.args[1]["deleted"])
            record = json.loads((project / "reviews/T1.json").read_text(encoding="utf-8"))
            self.assertEqual(record["mode"], "video")

    def test_upload_off_falls_back_to_frames(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / "jobs").mkdir()
            with mock.patch.object(review_panel, "_reachable", return_value=False), \
                 mock.patch.object(review_panel.settings, "get", return_value="off"):
                self.assertEqual(review_panel._video_source(project, "T1", Path("v.mp4")),
                                 ("frames", None, None))


class StopRules(unittest.TestCase):
    def test_gate_reads_max_attempts_from_settings(self):
        review = {"scores": {k: 0 for k in gate.load_rubric()["dimensions"]}, "vetoes": [], "issues": []}
        with mock.patch.object(gate.settings, "get_int", return_value=2):
            self.assertEqual(gate.evaluate(review, 2)["decision"], "escalate")

    def test_repeat_defect_stops_before_second_revision(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "run"
            (project / "briefs").mkdir(parents=True)
            with mock.patch.object(refine_loop.products, "can_spend", return_value=(True, {})):
                def gen(p, brief_path):
                    (p / "takes").mkdir(exist_ok=True)
                    (p / "takes" / f"{brief_path.stem}.mp4").write_bytes(b"x")

                decision = {"decision": "repair", "weighted": 5,
                            "repair": [{"cause": "motion_artifact", "fix": "x"}]}
                llm = mock.Mock(return_value=json.dumps({"prompt": "anchor revised"}))
                result = refine_loop.run({"id": "B", "prompt": "anchor", "duration": 10}, project, "p", [],
                                         gen, lambda *args: decision, llm, "anchor", max_attempts=3)
            self.assertEqual(result["status"], "repeat_defect")
            self.assertEqual(result["repeat_causes"], ["motion_artifact"])
            self.assertEqual(llm.call_count, 1)


if __name__ == "__main__":
    unittest.main()
