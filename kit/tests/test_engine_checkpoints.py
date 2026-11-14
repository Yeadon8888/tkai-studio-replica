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
    def test_benchmark_selection_reads_shots_and_tier_override(self):
        batch = products.product_dir("p1") / "research" / "batch-1"
        batch.mkdir(parents=True)
        (batch / "tier_overrides.json").write_text(json.dumps({
            "C023": {"tier": "H", "note": "人工确认可逐镜复刻"}
        }), encoding="utf-8")
        products.select_benchmarks("p1", ["C023"], "batch-1")
        (self.run / "shots.json").write_text(json.dumps({
            "candidate_id": "C023", "reference_url": "https://example.test/reference.mp4"
        }), encoding="utf-8")
        selected = engine._benchmark_selection(products.load("p1"), self.run)
        self.assertEqual(selected[0]["tier"], "H")
        self.assertEqual(selected[0]["tier_reason"], "人工确认可逐镜复刻")

    def test_adopt_tespert_shape_reaches_parent_verdict(self):
        """旧运行含父成片和四段成片时，迁移后只判父成片。"""
        shots = {"candidate_id": "C023", "reference_url": "https://example.test/reference.mp4",
                 "shots": []}
        (self.run / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
        briefs = self.run / "briefs"; briefs.mkdir()
        takes = self.run / "takes"; takes.mkdir()
        reviews = self.run / "reviews"; reviews.mkdir()
        parent = {"id": "hq-c023", "segments": [{"id": f"hq-c023-s{i}"} for i in range(1, 5)]}
        (briefs / "hq-c023.json").write_text(json.dumps(parent), encoding="utf-8")
        (takes / "hq-c023.mp4").write_bytes(b"parent")
        (reviews / "hq-c023.decision.json").write_text(json.dumps({"decision": "pass"}), encoding="utf-8")
        for number in range(1, 5):
            take = f"hq-c023-s{number}"
            (briefs / f"{take}.json").write_text(json.dumps({"id": take, "segment_of": "hq-c023"}), encoding="utf-8")
            (takes / f"{take}.mp4").write_bytes(b"segment")
            (reviews / f"{take}.decision.json").write_text(json.dumps({"decision": "pass"}), encoding="utf-8")

        before = engine.status("p1", self.run.name)
        self.assertEqual(before["action"]["type"], "human")
        self.assertIn("adopt", before["action"]["command"])
        result = engine.adopt("p1", self.run.name)
        self.assertEqual({row["kind"] for row in result["adopted"]}, {"benchmark", "script", "budget"})
        after = engine.status("p1", self.run.name)
        self.assertEqual(after["stage"], "verdict")
        self.assertEqual(after["action"]["subject"]["take"], "hq-c023")
        log = (products.product_dir("p1") / "checkpoints" / "log.jsonl").read_text()
        self.assertIn('"decided_by": "migration"', log)

    def test_review_requires_registered_product_references(self):
        products.select_benchmarks("p1", ["C1"])
        (products.product_dir("p1") / "plan.json").write_text("{}", encoding="utf-8")
        (self.run / "shots.json").write_text(json.dumps({"candidate_id": "C1"}), encoding="utf-8")
        briefs = self.run / "briefs"; briefs.mkdir()
        (briefs / "T1.json").write_text(json.dumps({"estimated_cny": 1}), encoding="utf-8")
        takes = self.run / "takes"; takes.mkdir(); (takes / "T1.mp4").write_bytes(b"video")
        # 直接补迁移批准，模拟已有运行；没有参考图时必须停在人处理动作。
        engine.adopt("p1", self.run.name)
        result = engine.status("p1", self.run.name)
        self.assertEqual(result["stage"], "review")
        self.assertEqual(result["action"]["type"], "human")
        self.assertIn("register-references", result["action"]["command"])

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
