"""R10：无证据低分先补问，仍无证据时交给人。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import dashboard  # noqa: E402
import gate  # noqa: E402
import production  # noqa: E402
import review_panel  # noqa: E402


def all_scores(value=9):
    return {name: value for name in gate.load_rubric()["dimensions"]}


def expert(score=5, issues=None):
    scores = all_scores()
    scores["product_consistency"] = score
    return {"judge": {"id": "P", "name": "产品专家"}, "scores": scores,
            "checks": {}, "vetoes": [], "issues": issues or [], "notes": ""}


class EvidenceRetry(unittest.TestCase):
    def setUp(self):
        self.exp = {"id": "P", "name": "产品专家", "dimensions": ["product_consistency"],
                    "prompt": "{shared_rules}{focus}", "shared_rules": "规则", "focus": "检查"}

    def test_uncited_low_is_retried_once_and_replaced(self):
        initial = expert(5)
        revised = expert(9)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(review_panel, "run_expert", side_effect=[initial, revised]) as run:
            judges, failed, not_ingested = review_panel._run_panel(
                {"experts": [self.exp]}, Path(tmp), "T", {}, "锚点", [], "https://video", [], None)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(failed, [])
        self.assertFalse(not_ingested)
        self.assertEqual(judges[0]["scores"]["product_consistency"], 9)
        self.assertEqual(judges[0]["evidence_retry"], [{
            "dimension": "product_consistency", "original_score": 5,
            "new_score": 9, "evidence_added": False,
        }])
        self.assertIsNotNone(run.call_args.kwargs.get("evidence_request"))

    def test_retry_with_evidence_returns_to_normal_gate_decision(self):
        issue = {"cause": "product_wrong_detail", "detail": "00:03 标签变形", "at": "00:03"}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(review_panel, "run_expert", return_value=expert(5, [issue])):
            revised = review_panel._retry_evidence_once(
                self.exp, expert(5), Path(tmp), "T", {}, "锚点", [], "https://video", [], None)
        merged = gate.merge_judges([revised])
        self.assertEqual(merged["uncited_low"], [])
        self.assertEqual(gate.evaluate(merged, 1)["decision"], "repair")
        self.assertTrue(revised["evidence_retry"][0]["evidence_added"])

    def test_retry_still_uncited_escalates_with_expert_and_dimension(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(review_panel, "run_expert", return_value=expert(5)):
            revised = review_panel._retry_evidence_once(
                self.exp, expert(5), Path(tmp), "T", {}, "锚点", [], "https://video", [], None)
        result = gate.evaluate(gate.merge_judges([revised]), 1)
        self.assertEqual(result["decision"], "escalate")
        self.assertNotIn("needs_human_look", result)
        self.assertIn("产品专家", result["reason"])
        self.assertIn("product_consistency", result["reason"])

    def test_no_low_score_does_not_retry(self):
        clean = expert(9)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(review_panel, "run_expert", return_value=clean) as run:
            judges, failed, _ = review_panel._run_panel(
                {"experts": [self.exp]}, Path(tmp), "T", {}, "锚点", [], "https://video", [], None)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(failed, [])
        self.assertNotIn("evidence_retry", judges[0])

    def test_retry_prompt_names_score_and_demands_timecode(self):
        response = json.dumps(expert(9), ensure_ascii=False)
        with mock.patch.object(review_panel.loop, "gemini", return_value=response) as gemini:
            review_panel.run_expert(self.exp, "p", "T", {}, "锚点", [],
                                    video_url="https://video", evidence_request=[{
                                        "dim": "product_consistency", "score": 5,
                                    }])
        prompt = gemini.call_args.args[0][0]["text"]
        self.assertIn("product_consistency 5 分", prompt)
        self.assertIn("带实际时间码", prompt)

    def test_new_review_id_does_not_overwrite_old_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for name in ("briefs", "takes", "reviews"):
                (project / name).mkdir()
            (project / "briefs/T.json").write_text(json.dumps({"id": "T"}), encoding="utf-8")
            (project / "takes/T.mp4").write_bytes(b"video")
            old = '{"old": true}'
            (project / "reviews/T.json").write_text(old, encoding="utf-8")
            with mock.patch.object(review_panel, "machine_checks", return_value={
                "blocking": ["测试阻塞"], "info": [], "facts": {},
            }):
                review_panel.review_take(project, "T", [], review_id="T-r10")
            self.assertEqual((project / "reviews/T.json").read_text(encoding="utf-8"), old)
            saved = json.loads((project / "reviews/T-r10.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["source_take"], "T")
            with self.assertRaises(FileExistsError):
                review_panel.review_take(project, "T", [], review_id="T-r10")


class SegmentEscalation(unittest.TestCase):
    def test_segment_escalation_propagates_to_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "reviews").mkdir()
            (project / "takes").mkdir()
            (project / "takes/P.mp4").write_bytes(b"parent")
            uncited = [{"dim": "product_consistency", "score": 5,
                        "judge_id": "P", "judge_name": "产品专家"}]
            rows = [
                ("P-s1", {"decision": "pass", "weighted": 9}),
                ("P-s2", {"decision": "escalate", "weighted": 8, "uncited_low": uncited,
                           "reason": "需人工看：专家给了低分但没写原因"}),
            ]
            for sid, decision in rows:
                (project / f"reviews/{sid}.json").write_text(json.dumps({
                    "experts": [], "decision": decision,
                }), encoding="utf-8")
            brief = {"segments": [{"duration": 5}, {"duration": 5}]}
            with mock.patch.object(review_panel, "concat_machine_checks",
                                   return_value={"blocking": [], "info": [], "facts": {}}):
                result = review_panel.aggregate_segment_reviews(project, "P", ["P-s1", "P-s2"], brief)
        self.assertEqual(result["decision"], "escalate")
        self.assertEqual(result["uncited_low"][0]["segment"], "P-s2")


class EvidenceDashboard(unittest.TestCase):
    def test_dashboard_shows_human_notice_and_expert_dimension(self):
        review = expert(5)
        data = {"briefs": [{"id": "B", "takes": [{"id": "T", "attempt": 1,
                                                     "reviews": [review]}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "takes.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            built = production.build(path)
        take = built["briefs"][0]["takes"][0]
        self.assertEqual(take["decision"], "escalate")
        html = dashboard.render({"production": built})
        self.assertIn("需人工看：专家给了低分但没写原因", html)
        self.assertIn("product_consistency", html)


if __name__ == "__main__":
    unittest.main()
