"""R11a 流程引擎与人工确认节点的离线测试。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import checkpoints  # noqa: E402
import engine  # noqa: E402
import human  # noqa: E402
import products  # noqa: E402
import settings  # noqa: E402


class StudioCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"STUDIO_HOME": str(self.home), "AUTO_APPROVE_CNY": "0"})
        self.env.start(); settings.load(force=True)
        self.old = products.STUDIO, products.PRODUCTS, products.QUEUE
        products.STUDIO = self.home
        products.PRODUCTS = self.home / "products"
        products.QUEUE = products.PRODUCTS / "_queue.json"
        products.create("p1", "测试品", budget_cny=5, anchor="TESPERT 16-in-1 test strips")
        self.run = products.new_run("p1", "test", date="20261114")

    def tearDown(self):
        products.STUDIO, products.PRODUCTS, products.QUEUE = self.old
        self.env.stop(); settings.load(force=True); self.tmp.cleanup()


class CheckpointRules(StudioCase):
    def test_six_kinds_are_idempotent(self):
        for kind in checkpoints.KINDS:
            with self.subTest(kind=kind):
                subject = {"kind": kind}
                a = checkpoints.create("p1", self.run.name, kind, "摘要", subject, ["批准"])
                b = checkpoints.create("p1", self.run.name, kind, "另一摘要", subject)
                self.assertEqual(a["id"], b["id"])
        files = list((products.product_dir("p1") / "checkpoints").glob("cp-*.json"))
        self.assertEqual(len(files), 6)

    def test_reject_and_changes_require_note(self):
        cp = checkpoints.create("p1", self.run.name, "benchmark", "确认", {}, [])
        for status in ("rejected", "changes_requested"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "note"):
                checkpoints.resolve(cp["id"], status, "", "user")
        self.assertEqual(checkpoints.resolve(cp["id"], "approved", "", "user")["status"], "approved")

    def test_script_edit_validation_failure_does_not_write(self):
        shots = {"duration": 4, "structure": [{"beat": "证明", "start": 0, "end": 4}], "shots": [{
            "id": "S01", "start": 0, "end": 4, "voiceover": "source"}]}
        script = {"scene": "A kitchen", "shots": [{"id": "S01", "visual_prompt": "A hand moves the product",
                  "voiceover": "Test 16 parameters", "caption": "16 parameters", "onscreen_copy": "16 parameters",
                  "end_state": "Product centered"}]}
        (self.run / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
        path = self.run / "script.json"; path.write_text(json.dumps(script), encoding="utf-8")
        subject = {"script": str(path.relative_to(self.home))}
        cp = checkpoints.create("p1", self.run.name, "script", "脚本", subject)
        with self.assertRaisesRegex(ValueError, "未通过校验"):
            checkpoints.resolve(cp["id"], "changes_requested", "改数字", "user",
                                [{"segment": 1, "field": "voiceover", "value": "Test 99 parameters"}])
        self.assertEqual(json.loads(path.read_text())["shots"][0]["voiceover"], "Test 16 parameters")

    def test_budget_auto_approval_boundaries(self):
        cp = checkpoints.create("p1", self.run.name, "budget", "预算", {"estimated_cny": 0})
        self.assertEqual(cp["status"], "pending")  # AUTO_APPROVE_CNY=0 永不自动
        with mock.patch.dict(os.environ, {"AUTO_APPROVE_CNY": "2"}):
            settings.load(force=True)
            exact = checkpoints.create("p1", self.run.name, "budget", "预算", {"estimated_cny": 2})
            over_auto = checkpoints.create("p1", self.run.name, "budget", "预算", {"estimated_cny": 2.01})
            over_balance = checkpoints.create("p1", self.run.name, "budget", "预算", {"estimated_cny": 5.01})
        self.assertEqual(exact["status"], "approved")
        self.assertEqual(over_auto["status"], "pending")
        self.assertEqual(over_balance["status"], "pending")


class EngineRules(StudioCase):
    def test_missing_selection_creates_checkpoint_and_returns_three(self):
        code, result = engine.advance("p1")
        self.assertEqual(code, 3)
        self.assertEqual(result["blocking_checkpoint"]["kind"], "benchmark")

    def test_dry_run_does_not_create_checkpoint(self):
        code, result = engine.advance("p1", dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("dry-run", result["message"])
        self.assertEqual(checkpoints.pending("p1"), [])

    def test_local_step_executes_and_paid_step_only_once(self):
        local = {"product": "p1", "run": self.run.name, "stage": "plan", "stage_label": "计划", "budget": {},
                 "action": {"type": "local", "command": ["local"], "summary": "本地"}}
        calls = []
        with mock.patch.object(engine, "status", return_value=local):
            code, _ = engine.advance("p1", runner=lambda cmd: calls.append(cmd) or 0)
        self.assertEqual((code, calls), (0, [["local"]]))

        cp = checkpoints.create("p1", self.run.name, "budget", "预算", {"estimated_cny": 1})
        checkpoints.resolve(cp["id"], "approved", "", "user")
        paid = {**local, "stage": "generation", "action": {"type": "paid", "command": ["paid"],
                "summary": "付费", "estimated_cny": 1, "approval": cp["id"]}}
        with mock.patch.object(engine, "status", return_value=paid):
            engine.advance("p1", runner=lambda cmd: calls.append(cmd) or 0)
        self.assertEqual(calls.count(["paid"]), 1)

    def test_paid_step_needs_approval_and_budget(self):
        cp = checkpoints.create("p1", self.run.name, "budget", "预算", {"estimated_cny": 1})
        current = {"product": "p1", "run": self.run.name, "stage": "generation", "stage_label": "生成", "budget": {},
                   "action": {"type": "paid", "command": ["paid"], "summary": "付费",
                              "estimated_cny": 1, "approval": cp["id"]}}
        with mock.patch.object(engine, "status", return_value=current):
            code, _ = engine.advance("p1", runner=lambda cmd: self.fail("不应执行"))
        self.assertEqual(code, 3)


class HumanCompatibility(StudioCase):
    def test_record_resolves_verdict_and_keeps_jsonl(self):
        (self.run / "reviews").mkdir()
        (self.run / "reviews" / "T1.decision.json").write_text(json.dumps({"decision": "pass"}), encoding="utf-8")
        row = human.record(self.run, "T1", "accept", "人工复查确认可以发布", "测试人")
        self.assertEqual(row["decision"], "accept")
        self.assertTrue((self.run / "human_decisions.jsonl").is_file())
        cps = list((products.product_dir("p1") / "checkpoints").glob("cp-verdict-*.json"))
        self.assertEqual(json.loads(cps[0].read_text())["status"], "approved")


if __name__ == "__main__":
    unittest.main()
