"""视频用途路线、计价、预算与逐段评审。"""
import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import ledger  # noqa: E402
import loop  # noqa: E402
import review_panel  # noqa: E402
import settings  # noqa: E402
import dashboard  # noqa: E402
import production  # noqa: E402


class VideoRequestRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = self.root / "person.png"
        self.image.write_bytes(b"not-decoded-by-request-builder")

    def tearDown(self):
        self.tmp.cleanup()

    def briefs(self):
        common = {"prompt": "slow move", "images": [str(self.image)], "duration": 5,
                  "resolution": "768p", "aspect_ratio": "9:16"}
        return {
            "default": {**common, "duration": 10},
            "quality": {**common, "video_route": "quality", "resolution": "960p"},
            "fast": {**common, "video_route": "fast", "resolution": "480p"},
            "keyframe": {**common, "video_route": "keyframe", "first_frame": str(self.image),
                         "last_frame": str(self.image)},
            "replace_person": {**common, "video_route": "replace_person", "tier": "H",
                               "reference_has_person": True, "product_in_frame_not_important": True,
                               "ref_image": str(self.image), "ref_video": "https://example.com/ref.mp4",
                               "ref_video_duration": 8},
            "motion": {"video_route": "motion", "ref_image": str(self.image),
                       "ref_video": "https://example.com/ref.mp4", "resolution": "464*832px(竖版)"},
        }

    def test_each_route_builds_exact_contract(self):
        expected = {
            "default": {"prompt", "images", "duration", "resolution", "aspect_ratio", "model"},
            "quality": {"prompt", "images", "duration", "resolution", "aspect_ratio", "model"},
            "fast": {"prompt", "images", "duration", "resolution", "aspect_ratio", "model"},
            "keyframe": {"prompt", "first_frame", "last_frame", "duration", "resolution", "model"},
            "replace_person": {"prompt", "ref_image", "ref_video", "duration", "resolution", "model"},
            "motion": {"ref_image", "ref_video", "resolution", "model"},
        }
        for route, brief in self.briefs().items():
            with self.subTest(route=route):
                actual_route, body = loop.build_video_request(brief)
                self.assertEqual(actual_route, route)
                self.assertEqual(set(body), expected[route])
                self.assertEqual(body["model"], settings.video_route_model(route))

    def test_each_route_reaches_fake_api_once(self):
        for route, brief in self.briefs().items():
            with self.subTest(route=route), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "run"
                (project / "briefs").mkdir(parents=True)
                brief["id"] = route
                path = project / "briefs" / f"{route}.json"
                path.write_text(json.dumps(brief), encoding="utf-8")
                args = argparse.Namespace(project=str(project), brief=str(path), cap=1,
                                          interval=0, max_polls=0, no_review=True)
                with mock.patch.object(loop, "_api", return_value={"id": "task-1"}) as api, \
                     mock.patch.object(loop, "_check_generation_budget"), \
                     mock.patch.object(loop.ledger, "append"):
                    loop._generate_single(args)
                api.assert_called_once()
                self.assertEqual(api.call_args.args[:2], ("POST", "/v1/videos"))
                self.assertEqual(api.call_args.args[2]["model"], settings.video_route_model(route))

    def test_invalid_inputs_stop_before_api(self):
        invalid = [
            {"id": "bad-images", "prompt": "p", "images": [], "duration": 10},
            {"id": "bad-quality", "video_route": "quality", "prompt": "p", "images": [str(self.image)],
             "duration": 5, "resolution": "1080p"},
            {"id": "bad-keyframe", "video_route": "keyframe", "prompt": "p", "first_frame": str(self.image),
             "duration": 5},
            {"id": "bad-replace", "video_route": "replace_person", "prompt": "p", "tier": "H",
             "reference_has_person": True, "product_in_frame_not_important": True, "ref_image": str(self.image),
             "ref_video": "https://example.com/ref.mp4", "ref_video_duration": 4, "duration": 5},
            {"id": "bad-motion", "video_route": "motion", "ref_image": str(self.image), "ref_video": "private.mp4"},
        ]
        for brief in invalid:
            with self.subTest(brief=brief["id"]), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "run"
                (project / "briefs").mkdir(parents=True)
                path = project / "briefs" / "brief.json"
                path.write_text(json.dumps(brief), encoding="utf-8")
                args = argparse.Namespace(project=str(project), brief=str(path), cap=1,
                                          interval=0, max_polls=0, no_review=True)
                with mock.patch.object(loop, "_api") as api, self.assertRaises(SystemExit):
                    loop._generate_single(args)
                api.assert_not_called()
                self.assertFalse((project / "jobs" / f"{brief['id']}.claim").exists())

    def test_h3_audio_accepts_documented_data_uri(self):
        brief = self.briefs()["fast"]
        brief["audio_urls"] = ["data:audio/wav;base64,UklGRg=="]
        _, body = loop.build_video_request(brief)
        self.assertEqual(body["audio_urls"], brief["audio_urls"])


class PriceAndBudget(unittest.TestCase):
    def test_prices_use_model_and_resolution_and_unknown_stays_unknown(self):
        self.assertEqual(ledger.estimate_cny(settings.video_route_model("quality"), 5, "submitted", "960p"), 1.15)
        self.assertEqual(ledger.estimate_cny(settings.video_route_model("fast"), 5, "submitted", "480p"), 0.5)
        self.assertEqual(ledger.estimate_cny(settings.video_route_model("replace_person"), 5, "submitted", "768p竖"), 1.25)
        self.assertIsNone(ledger.estimate_cny(settings.video_route_model("motion"), 5, "submitted", "464*832px(竖版)"))
        self.assertEqual(ledger.estimate_cny("minimax-H3", 10, "submitted"), 1.3)

    def test_registered_product_budget_blocks_before_post(self):
        body = {"model": settings.video_route_model("quality"), "duration": 5, "resolution": "960p"}
        state = {"spent_cny": 18.8, "limit_cny": 19.5}
        with mock.patch.object(loop, "_registered_product", return_value="water-test-strips"), \
             mock.patch.object(loop.products, "can_spend", return_value=(False, state)) as can_spend:
            with self.assertRaises(SystemExit):
                loop._check_generation_budget(Path("run"), body)
        can_spend.assert_called_once_with("water-test-strips", 1.15)

    def test_motion_resolution_is_machine_checked_without_fake_duration(self):
        brief = {"video_route": "motion", "resolution": "464*832px(竖版)", "aspect_ratio": "9:16"}
        with mock.patch.object(review_panel, "_probe", return_value={
            "streams": [{"codec_type": "video", "width": 464, "height": 832, "codec_name": "h264"}],
            "format": {"duration": "17.0"},
        }), tempfile.NamedTemporaryFile() as video:
            Path(video.name).write_bytes(b"x" * 50_001)
            result = review_panel.machine_checks(video.name, brief)
        self.assertEqual(result["blocking"], [])


class SegmentAggregation(unittest.TestCase):
    def _write_segment(self, project, sid, decision, issue_at=None):
        issue = ([{"cause": "motion_artifact", "at": issue_at, "detail": "动作异常"}] if issue_at else [])
        record = {"take": sid, "mode": "video", "experts": [
            {"judge": {"id": "visual"}, "scores": {"visual_quality": 9}, "vetoes": [], "issues": issue}
        ], "decision": {"decision": decision, "weighted": 9 if decision == "pass" else 6,
                         "vetoes": ["motion_artifact"] if decision != "pass" else [], "low_dimensions": []}}
        (project / "reviews" / f"{sid}.json").write_text(json.dumps(record), encoding="utf-8")

    def test_failure_propagates_and_timecode_is_shifted(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "reviews").mkdir()
            (project / "takes").mkdir()
            (project / "takes/P.mp4").write_bytes(b"parent")
            self._write_segment(project, "P-s1", "pass")
            self._write_segment(project, "P-s2", "repair", "00:02–00:04")
            brief = {"id": "P", "segments": [{"duration": 10}, {"duration": 10}]}
            with mock.patch.object(review_panel, "concat_machine_checks",
                                   return_value={"blocking": [], "info": [], "facts": {}}):
                decision = review_panel.aggregate_segment_reviews(project, "P", ["P-s1", "P-s2"], brief)
            record = json.loads((project / "reviews/P.json").read_text(encoding="utf-8"))
        self.assertEqual(decision["decision"], "repair")
        self.assertEqual(record["experts"][1]["issues"][0]["at"], "00:12–00:14")

    def test_all_segments_and_machine_check_must_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "reviews").mkdir()
            (project / "takes").mkdir()
            (project / "takes/P.mp4").write_bytes(b"parent")
            self._write_segment(project, "P-s1", "pass")
            self._write_segment(project, "P-s2", "pass")
            brief = {"id": "P", "segments": [{"duration": 5}, {"duration": 5}]}
            with mock.patch.object(review_panel, "concat_machine_checks",
                                   return_value={"blocking": [], "info": [], "facts": {}}):
                decision = review_panel.aggregate_segment_reviews(project, "P", ["P-s1", "P-s2"], brief)
            self.assertEqual(decision["decision"], "pass")


class SegmentBoard(unittest.TestCase):
    def test_segments_stay_under_parent_and_parent_cost_is_sum_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "run"
            for name in ("briefs", "takes", "jobs", "reviews"):
                (project / name).mkdir(parents=True)
            parent = {"id": "P", "title": "父成片", "resolution": "768p", "segments": [
                {"prompt": "one", "duration": 10, "images": []},
                {"prompt": "two", "duration": 10, "images": []},
            ]}
            (project / "briefs/P.json").write_text(json.dumps(parent), encoding="utf-8")
            for index in (1, 2):
                sid = f"P-s{index}"
                (project / f"briefs/{sid}.json").write_text(json.dumps({"id": sid, "prompt": "x"}), encoding="utf-8")
                (project / f"takes/{sid}.mp4").write_bytes(b"segment")
                (project / f"jobs/{sid}.json").write_text(json.dumps({
                    "id": sid, "model": "route-model", "video_route": "default", "resolution": "768p"
                }), encoding="utf-8")
                (project / f"reviews/{sid}.json").write_text(json.dumps({
                    "mode": "video", "experts": [], "decision": {"decision": "pass"}
                }), encoding="utf-8")
            (project / "takes/P.mp4").write_bytes(b"parent")
            (project / "jobs/P.json").write_text(json.dumps({
                "id": "P", "model": "route-model", "resolution": "768p", "segments": ["P-s1", "P-s2"]
            }), encoding="utf-8")
            (project / "reviews/P.json").write_text(json.dumps({
                "experts": [], "machine": {"blocking": []}, "decision": {"decision": "pass"}
            }), encoding="utf-8")
            ledger_path = Path(tmp) / "costs.jsonl"
            ledger_path.write_text("".join(json.dumps({
                "project": "run", "ref": f"P-s{i}", "status": "submitted", "estimate_cny": 1.3
            }) + "\n" for i in (1, 2)), encoding="utf-8")
            with mock.patch.object(loop.ledger, "LEDGER_FILE", ledger_path):
                loop.cmd_assemble(argparse.Namespace(project=str(project)))
            data = json.loads((project / "takes.json").read_text(encoding="utf-8"))
            take = data["briefs"][0]["takes"][0]
            html = dashboard.render({"production": {"briefs": [{"id": "P", "takes": [{
                **take, "grade": "good", "grade_text": "通过", "issues": [], "human": None
            }]}], "series": [], "stats": {}, "rule": {}}}, "测试")
        self.assertEqual([b["id"] for b in data["briefs"]], ["P"])
        self.assertEqual(take["cost_cny"], 2.6)
        self.assertEqual([s["id"] for s in take["segments"]], ["P-s1", "P-s2"])
        self.assertIn("P-s1", html)
        self.assertIn('"model": "route-model"', html)

    def test_explicit_parent_segment_decision_is_not_overridden_by_partial_experts(self):
        scores = {name: 9 for name in ("soul_fidelity", "product_consistency", "person_consistency",
                                       "visual_quality", "audio_text", "pacing_structure", "platform_signals")}
        data = {"briefs": [{"id": "P", "takes": [{"id": "P", "attempt": 1, "segments": [{"id": "P-s1"}],
                 "segment_decision": {"decision": None, "reason": "第二段专家未完成"},
                 "reviews": [{"scores": scores, "vetoes": [], "issues": []}]}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "takes.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = production.build(path)
        take = result["briefs"][0]["takes"][0]
        self.assertIsNone(take["decision"])
        self.assertEqual(take["grade"], "unreviewed")

    def test_expired_segment_remote_url_falls_back_without_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "jobs").mkdir()
            (project / "jobs/S-s1.json").write_text(json.dumps({
                "remote_url": "https://example.com/expired.mp4"
            }), encoding="utf-8")
            with mock.patch.object(review_panel, "_reachable", return_value=False), \
                 mock.patch.object(review_panel.media_host, "upload") as upload:
                mode, url, info = review_panel._video_source(
                    project, "S-s1", project / "takes/S-s1.mp4", {"segment_of": "S"})
        self.assertEqual((mode, url, info), ("frames", None, None))
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
