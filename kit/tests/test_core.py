"""studio kit 的回归测试。运行：python3 -m unittest discover -s studio/kit/tests -v"""
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import common  # noqa: E402
import gate  # noqa: E402
import ledger  # noqa: E402
import loop  # noqa: E402
import review_panel  # noqa: E402

# 改动前 gate.py 中的硬编码字典（回归基准）。派生结果必须与它完全一致。
LEGACY_DIM_CAUSES = {
    "soul_fidelity": {"soul_must_keep_missing", "client_requirement_unmet"},
    "product_consistency": {"product_wrong_detail", "product_deformed", "client_requirement_unmet"},
    "person_consistency": {"person_mismatch"},
    "visual_quality": {"motion_artifact", "compliance_fail", "product_deformed"},
    "audio_text": {"keyword_misspoken", "audio_sync"},
    "pacing_structure": {"pacing_off"},
    "platform_signals": {"weak_hook", "pacing_off"},
}
LEGACY_DEFAULT = {"product_consistency": "product_wrong_detail", "person_consistency": "person_mismatch",
                  "audio_text": "audio_sync", "pacing_structure": "pacing_off", "soul_fidelity": "soul_must_keep_missing",
                  "visual_quality": "motion_artifact", "platform_signals": "weak_hook"}


class RubricSingleSource(unittest.TestCase):
    def test_derived_dim_causes_match_legacy(self):
        self.assertEqual(gate.DIM_CAUSES, LEGACY_DIM_CAUSES)

    def test_derived_default_causes_match_legacy(self):
        self.assertEqual(gate.DEFAULT_CAUSE, LEGACY_DEFAULT)

    def test_every_dimension_declares_evidence(self):
        rub = gate.load_rubric()
        for k, v in rub["dimensions"].items():
            self.assertTrue(v.get("evidence"), k)

    def test_expert_causes_exist_in_rubric(self):
        rub = gate.load_rubric()
        known = set(rub["causes"])
        import yaml
        cfg = yaml.safe_load((KIT / "config" / "experts.yaml").read_text(encoding="utf-8"))
        text = json.dumps(cfg, ensure_ascii=False)
        import re
        for group in re.findall(r'"cause": "([^"]+)"', text):
            for c in group.split("|"):
                self.assertIn(c, known)


class LedgerMoney(unittest.TestCase):
    def test_submitted_minimax_is_priced(self):
        self.assertEqual(ledger.estimate_cny("minimax-H3", 10, "submitted"), 1.3)

    def test_terminal_rows_cost_nothing(self):
        self.assertEqual(ledger.estimate_cny("minimax-H3", 10, "saved"), 0.0)
        self.assertEqual(ledger.estimate_cny("minimax-H3", 10, "failed"), 0.0)

    def test_unknown_price_is_none_not_invented(self):
        self.assertIsNone(ledger.estimate_cny("gemini-3.8-flash", 1, "ok"))


class SharedHelpers(unittest.TestCase):
    def test_single_definition(self):
        self.assertIs(loop._read, common._read)
        self.assertIs(loop._write, common._write)
        self.assertIs(review_panel._read, common._read)


class ReviewRouting(unittest.TestCase):
    def test_legacy_review_removed(self):
        self.assertFalse(hasattr(loop, "REVIEW_PROMPT"))
        self.assertFalse(hasattr(loop, "LENSES"))

    def test_cmd_review_delegates_to_panel(self):
        ns = type("A", (), {"project": "p", "take": "T", "refs": "a.png,b.png", "judges": 2})
        with mock.patch.object(review_panel, "review_take", return_value={"decision": "pass", "weighted": 9}) as m:
            loop.cmd_review(ns)
        m.assert_called_once_with("p", "T", ["a.png", "b.png"])


class PollResilience(unittest.TestCase):
    def _project(self, tmp, status_seq):
        proj = Path(tmp) / "proj"
        (proj / "briefs").mkdir(parents=True)
        (proj / "jobs").mkdir()
        brief = {"id": "B1", "images": [], "prompt": "x", "duration": 10}
        (proj / "briefs" / "B1.json").write_text(json.dumps(brief), encoding="utf-8")
        job = {"id": "B1", "task_id": "task-1", "status": "submitted", "submitted_at": "2026-11-12T00:00:00+08:00"}
        (proj / "jobs" / "B1.json").write_text(json.dumps(job), encoding="utf-8")
        return proj

    def test_network_error_does_not_kill_job_and_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(tmp, None)
            responses = [urllib.error.URLError("EOF"), {"status": "failed", "error": "boom"}]

            def fake_api(method, path, body=None):
                r = responses.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
            appended = []
            args = type("A", (), {"project": str(proj), "brief": str(proj / "briefs" / "B1.json"),
                                  "cap": 3, "interval": 0, "max_polls": 5})
            with mock.patch.object(loop, "_api", side_effect=fake_api), \
                 mock.patch.object(loop.time, "sleep", lambda s: None), \
                 mock.patch.object(loop.ledger, "append", side_effect=lambda *a, **k: appended.append((a, k))):
                loop.cmd_gen(args)
            statuses = [a[2] for a, _ in appended]
            self.assertIn("failed", statuses)
            job = json.loads((proj / "jobs" / "B1.json").read_text(encoding="utf-8"))
            self.assertEqual(job["status"], "failed")

    def test_gives_up_only_after_many_transient_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(tmp, None)
            args = type("A", (), {"project": str(proj), "brief": str(proj / "briefs" / "B1.json"),
                                  "cap": 3, "interval": 0, "max_polls": 100})
            with mock.patch.object(loop, "_api", side_effect=urllib.error.URLError("down")), \
                 mock.patch.object(loop.time, "sleep", lambda s: None):
                loop.cmd_gen(args)  # 不应抛出异常
            job = json.loads((proj / "jobs" / "B1.json").read_text(encoding="utf-8"))
            self.assertEqual(job["task_id"], "task-1")  # 任务号保留，不重提


class MachineChecks(unittest.TestCase):
    def test_wrong_duration_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Path(tmp) / "v.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=768x1344:rate=24",
                            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True)
            res = review_panel.machine_checks(v, {"duration": 10, "resolution": "768p", "aspect_ratio": "9:16"})
            self.assertTrue(any("时长" in b for b in res["blocking"]))
            self.assertEqual(res["facts"]["width"], 768)


if __name__ == "__main__":
    unittest.main()


class TierAllocation(unittest.TestCase):
    def test_ten_split_is_proportional_including_x(self):
        import fission
        alloc, gap = fission.allocate(10, {"H": 9, "M": 9, "L": 9})
        self.assertEqual(alloc, {"H": 4, "M": 3, "L": 2, "X": 1})
        self.assertEqual(gap, 0)

    def test_missing_tier_redistributes_and_x_absorbs_rest(self):
        import fission
        alloc, gap = fission.allocate(10, {"H": 0, "M": 2, "L": 2})
        self.assertEqual(alloc["H"], 0)
        self.assertEqual(sum(alloc.values()), 10)
        self.assertEqual(gap, 0)

    def test_x_has_no_capacity_limit(self):
        import fission
        alloc, gap = fission.allocate(10, {"H": 0, "M": 0, "L": 0})
        self.assertEqual(alloc, {"H": 0, "M": 0, "L": 0, "X": 10})
        self.assertEqual(gap, 0)


class ProductBudget(unittest.TestCase):
    def test_default_limit_is_five_and_spend_is_counted_from_ledger(self):
        import json as _j
        import tempfile
        import products
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            products.PRODUCTS = tmp / "products"
            products.QUEUE = products.PRODUCTS / "_queue.json"
            products.create("p1", "测试品")
            products.register_run("p1", "run-a")
            led = tmp / "costs.jsonl"
            led.write_text("\n".join([
                _j.dumps({"project": "run-a", "estimate_cny": 3.9}),
                _j.dumps({"project": "run-other", "estimate_cny": 99}),
                _j.dumps({"project": "run-a", "estimate_cny": None}),
            ]) + "\n", encoding="utf-8")
            s = products.spent_cny("p1", led)
            self.assertEqual(s["limit_cny"], 5.0)
            self.assertEqual(s["spent_cny"], 3.9)
            self.assertEqual(s["unknown_rows"], 1)
            ok, _ = products.can_spend("p1", 1.1, led)  # 3.9 + 1.1 = 5.0，刚好不超
            self.assertTrue(ok)
            ok2, _ = products.can_spend("p1", 1.2, led)  # 3.9 + 1.2 = 5.1，超出
            self.assertFalse(ok2)

    def test_override_requires_note(self):
        import tempfile
        import products
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            products.PRODUCTS = tmp / "products"
            products.QUEUE = products.PRODUCTS / "_queue.json"
            products.create("p2", "测试品")
            with self.assertRaises(SystemExit):
                products.approve_budget("p2", 13, "  ")
            m = products.approve_budget("p2", 13, "用户确认 10 条批次")
            self.assertEqual(products.limit_cny(m), 13.0)


class CallLimits(unittest.TestCase):
    def test_tikhub_budget_is_clamped_to_100(self):
        import tempfile
        import tt_research as tt
        with tempfile.TemporaryDirectory() as tmp:
            c = tt.Client("k", tmp, budget_calls=500)
            self.assertEqual(c.budget, 100)

    def test_gemini_count_reads_ledger_per_project(self):
        import json as _j
        import tempfile
        import ledger
        with tempfile.TemporaryDirectory() as tmp:
            old = ledger.LEDGER_FILE
            ledger.LEDGER_FILE = Path(tmp) / "costs.jsonl"
            try:
                rows = [{"project": "r1", "endpoint": "gemini-3.8-flash", "units": 1},
                        {"project": "r2", "endpoint": "gemini-3.8-flash", "units": 1},
                        {"project": "r1", "endpoint": "gemini-3.8-flash", "units": 1}]
                ledger.LEDGER_FILE.write_text("\n".join(_j.dumps(r) for r in rows) + "\n", encoding="utf-8")
                self.assertEqual(ledger.count_calls("r1", "gemini-3.8-flash"), 2)
            finally:
                ledger.LEDGER_FILE = old


class PromptReview(unittest.TestCase):
    def _ok_script(self):
        from test_core_fixtures import good_script, ANCHOR  # noqa
        return good_script(), ANCHOR

    def test_clean_script_stops_after_one_round(self):
        import json as _j
        import prompt_review as pr
        script, anchor = self._ok_script()
        calls = []
        def llm(prompt):
            calls.append(prompt)
            return _j.dumps({"issues": [], "verdict": "pass"})
        out, hist = pr.run(script, anchor, llm, rounds=3)
        self.assertEqual(len(hist), 1)
        self.assertEqual(len(calls), 1)  # 没有问题就不修改

    def test_block_issue_triggers_revision_then_pass(self):
        import json as _j
        import prompt_review as pr
        script, anchor = self._ok_script()
        seq = [_j.dumps({"issues": [{"id": "enemy_first", "severity": "block", "shot": 1, "problem": "x", "fix": "y"}],
                         "verdict": "revise"}),
               _j.dumps(script),                      # revise 的返回
               _j.dumps({"issues": [], "verdict": "pass"})]
        it = iter(seq)
        out, hist = pr.run(script, anchor, lambda p: next(it), rounds=3)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[-1]["verdict"], "pass")

    def test_stops_at_round_limit(self):
        import json as _j
        import prompt_review as pr
        script, anchor = self._ok_script()
        bad = _j.dumps({"issues": [{"id": "x", "severity": "block", "shot": 1, "problem": "p", "fix": "f"}], "verdict": "revise"})
        out, hist = pr.run(script, anchor, lambda p: bad if "质检员" in p else _j.dumps(script), rounds=2)
        self.assertEqual(len(hist), 2)


class RefineLoop(unittest.TestCase):
    ANCHOR = "a small clear vial with a plain white label"

    def _setup(self, tmp, budget=13.0):
        import products
        products.PRODUCTS = Path(tmp) / "products"
        products.QUEUE = products.PRODUCTS / "_queue.json"
        products.create("pz", "测试品")
        products.approve_budget("pz", budget, "测试")
        proj = Path(tmp) / "run"
        (proj / "briefs").mkdir(parents=True)
        return proj

    def test_pass_on_second_attempt_and_prompt_revised_with_anchor(self):
        import refine_loop as rl
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._setup(tmp)
            base = {"id": "B1", "prompt": f"Use image 1. {self.ANCHOR}. crouch and lift", "images": [], "duration": 10}
            gen_calls, llm_calls = [], []
            def gen(p, bp):
                gen_calls.append(bp.name)
                (p / "takes").mkdir(exist_ok=True)
                (p / "takes" / (bp.stem + ".mp4")).write_bytes(b"x")
            def review(p, tid, refs):
                if tid.endswith("a1"):
                    return {"decision": "repair", "weighted": 6.0, "repair": [{"cause": "product_wrong_detail", "fix": "x"}]}
                return {"decision": "pass", "weighted": 8.5}
            def llm(prompt):
                llm_calls.append(prompt)
                return json.dumps({"prompt": f"Use image 1. {self.ANCHOR}. crouch, lift, label clean"})
            res = rl.run(base, proj, "pz", [], gen, review, llm, self.ANCHOR, max_attempts=3, log_dir=proj / "refine")
            self.assertEqual(res["status"], "succeeded")
            self.assertEqual(len(gen_calls), 2)
            self.assertEqual(len(llm_calls), 1)

    def test_revision_without_anchor_is_rejected(self):
        import refine_loop as rl
        new, errs = rl.revise("p", self.ANCHOR, "", [], lambda _: json.dumps({"prompt": "no anchor here"}))
        self.assertIsNone(new)
        self.assertTrue(any("锚点" in e for e in errs))

    def test_budget_stops_before_generation(self):
        import refine_loop as rl
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._setup(tmp, budget=0.5)
            base = {"id": "B2", "prompt": self.ANCHOR, "images": [], "duration": 10}
            called = []
            res = rl.run(base, proj, "pz", [], lambda p, b: called.append(1), lambda *a: {}, lambda _: "", self.ANCHOR,
                         max_attempts=3, log_dir=proj / "refine")
            self.assertEqual(res["status"], "budget_stop")
            self.assertEqual(called, [])

    def test_escalate_stops_without_revision(self):
        import refine_loop as rl
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._setup(tmp)
            base = {"id": "B3", "prompt": self.ANCHOR, "images": [], "duration": 10}
            def gen(p, bp):
                (p / "takes").mkdir(exist_ok=True)
                (p / "takes" / (bp.stem + ".mp4")).write_bytes(b"x")
            llm_calls = []
            res = rl.run(base, proj, "pz", [], gen, lambda *a: {"decision": "escalate"}, lambda p: llm_calls.append(p) or "",
                         self.ANCHOR, max_attempts=3, log_dir=proj / "refine")
            self.assertEqual(res["status"], "escalated")
            self.assertEqual(llm_calls, [])


class DraftPrompt(unittest.TestCase):
    ANCHOR = "a small clear vial of water test strips with a plain white label and NO text"
    BASE = "Use image 1 as the exact product reference: " + ANCHOR + ". crouch at the pool edge, dip, compare. daylight, handheld, vertical 9:16."

    def test_accepts_draft_that_keeps_anchor_and_fixed_items(self):
        import json as _j
        import draft_prompt as dp
        good = "Use image 1 as the exact product reference: " + self.ANCHOR + ". crouch at the pool edge, dip the strip, compare colour. daylight, handheld, vertical 9:16."
        out, errs = dp.draft({"id": "H1", "prompt": self.BASE}, self.ANCHOR, "", "", "",
                             lambda p: _j.dumps({"fit": "adapt", "fit_notes": "形态不同", "prompt": good}))
        self.assertEqual(errs, [])
        self.assertEqual(out["draft_fit"], "adapt")
        self.assertEqual(out["prompt_baseline"], self.BASE)

    def test_rejects_draft_that_drops_anchor(self):
        import json as _j
        import draft_prompt as dp
        out, errs = dp.draft({"id": "H1", "prompt": self.BASE}, self.ANCHOR, "", "", "",
                             lambda p: _j.dumps({"fit": "fit", "fit_notes": "", "prompt": "crouch, dip, compare, daylight, handheld, vertical"}))
        self.assertIsNone(out)
        self.assertTrue(any("锚点" in e for e in errs))

    def test_rejects_draft_that_drops_fixed_action(self):
        import draft_prompt as dp
        errs = dp.check_draft("Use image 1: " + self.ANCHOR + ". stand still and talk. daylight, handheld, vertical", self.ANCHOR, self.BASE)
        self.assertTrue(any("固定项" in e for e in errs))


class OwnScriptChecks(unittest.TestCase):
    ANCHOR = "a small clear vial with a plain white label"

    def _script(self, **over):
        s = {"id": "S1", "enemy": "guess pool safety by eye", "symptom": "cloudy water worry",
             "mechanism": "colour strip reads chemistry exactly", "macro": False,
             "macro_reason": "plain physical product", "hands": "single", "duration": 10,
             "reveal_product_at_s": 3,
             "shots": [{"start": 0, "end": 3, "scene_zh": "泳池边", "action_zh": "抱怨水质", "line": "...",
                        "tone": "疲惫", "image_prompt": self.ANCHOR, "video_prompt": f"{self.ANCHOR}, one hand holding the camera"},
                       {"start": 3, "end": 10, "scene_zh": "泳池边", "action_zh": "展示试纸", "line": "—",
                        "tone": "笃定", "image_prompt": self.ANCHOR, "video_prompt": f"{self.ANCHOR}, one hand holding the camera"}]}
        s.update(over)
        return s

    def test_valid_script_passes(self):
        import own_script
        self.assertEqual(own_script.check(self._script(), self.ANCHOR)["blocking"], [])

    def test_early_product_reveal_is_blocking(self):
        import own_script
        res = own_script.check(self._script(reveal_product_at_s=1), self.ANCHOR)
        self.assertTrue(any("28%" in m for m in res["blocking"]))

    def test_missing_anchor_and_gap_are_blocking(self):
        import own_script
        s = self._script()
        s["shots"][1]["start"] = 4  # 留出空隙
        res = own_script.check(s, self.ANCHOR)
        self.assertTrue(any("不连续" in m for m in res["blocking"]))
        s2 = self._script()
        s2["shots"][0]["video_prompt"] = "one hand holding the camera"
        res2 = own_script.check(s2, self.ANCHOR)
        self.assertTrue(any("锚点" in m for m in res2["blocking"]))

    def test_skeleton_is_not_ready(self):
        import own_script
        res = own_script.check(own_script.skeleton("S2"), self.ANCHOR)
        self.assertTrue(res["blocking"])


class EvidenceFloor(unittest.TestCase):
    """回归：证据门槛必须是 6.5（与专家提示词一致），不能是 8.0。"""

    def _review(self, score):
        return {"scores": {"pacing_structure": score}, "issues": [], "vetoes": []}

    def test_seven_without_evidence_is_kept(self):
        n, u = gate.normalize_review(self._review(7))
        self.assertEqual(n["scores"]["pacing_structure"], 7)
        self.assertEqual(u, [])

    def test_below_floor_without_evidence_becomes_neutral(self):
        n, u = gate.normalize_review(self._review(6))
        self.assertEqual(n["scores"]["pacing_structure"], gate.NEUTRAL)
        self.assertEqual(len(u), 1)

    def test_reevaluate_uses_saved_experts_offline(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "reviews").mkdir()
            experts = [
                {"judge": {"id": "E1"}, "scores": {"visual_quality": 9, "pacing_structure": 7, "platform_signals": 7, "audio_text": 10}, "vetoes": [], "issues": []},
                {"judge": {"id": "E2"}, "scores": {"product_consistency": 9, "soul_fidelity": 9}, "vetoes": [], "issues": []},
                {"judge": {"id": "E3"}, "scores": {"person_consistency": 9, "visual_quality": 9}, "vetoes": [], "issues": []},
            ]
            rec = {"take": "T", "attempt": 1, "experts": experts}
            (proj / "reviews" / "T.json").write_text(json.dumps(rec), encoding="utf-8")
            res = review_panel.reevaluate(proj, "T")
            # 无证据的 7 分必须保持 7，不能被抬成 8
            self.assertIsNotNone(res.get("weighted"))
            saved = json.loads((proj / "reviews" / "T.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["merged"]["scores"]["pacing_structure"], 7)
            self.assertTrue(saved["reevaluated"])
