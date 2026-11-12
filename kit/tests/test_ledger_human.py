"""账本回填、人工判定、校准和看板统计。"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import calibrate  # noqa: E402
import dashboard  # noqa: E402
import human  # noqa: E402
import ledger  # noqa: E402
import ledger_backfill  # noqa: E402
import production  # noqa: E402
import schema  # noqa: E402


def write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class LedgerBackfillTest(unittest.TestCase):
    def test_dry_run_and_apply_only_fill_missing_video_amount(self):
        rows = [
            {"endpoint": "minimax-H3", "status": "submitted", "project": "p1", "ref": "A", "units": 10,
             "estimate_cny": None},
            {"endpoint": "minimax-H3", "status": "submitted", "project": "p1", "ref": "B", "units": 10,
             "estimate_cny": 1.3},
            {"endpoint": "gemini-3.8-flash", "status": "ok", "project": "p1", "ref": "C", "units": 1,
             "estimate_cny": None, "kind": "text"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.jsonl"
            write_jsonl(path, rows)
            original = path.read_text(encoding="utf-8")
            messages = []
            ledger_backfill.run(path, out=messages.append)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertIn("回填后视频金额合计：2.6000 元", messages)
            backup = ledger_backfill.run(path, apply=True, out=lambda _: None)
            changed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(changed[0]["estimate_cny"], 1.3)
            self.assertEqual(changed[1:], rows[1:])
            self.assertEqual(backup.read_text(encoding="utf-8"), original)


class LedgerUsageTest(unittest.TestCase):
    def test_usage_sums_video_text_tokens_tikhub_and_duplicates(self):
        rows = [
            {"provider": "tokensfactory", "endpoint": "minimax-H3", "status": "submitted", "project": "p",
             "ref": "T1", "units": 10, "estimate_cny": 1.3},
            {"provider": "tokensfactory", "endpoint": "minimax-H3", "status": "submitted", "project": "p",
             "ref": "T1", "units": 10, "estimate_cny": 1.3},
            {"provider": "tokensfactory", "endpoint": "gemini-3.8-flash", "status": "ok", "project": "p",
             "units": 1, "kind": "text", "usage": {"prompt_tokens": 12, "completion_tokens": 4}},
            {"provider": "tokensfactory", "endpoint": "gemini-3.8-flash", "status": "503", "project": "p",
             "units": 1, "kind": "text", "usage": {}},
            {"provider": "tikhub", "endpoint": "/search", "status": "200", "project": "p", "units": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.jsonl"
            write_jsonl(path, rows)
            with mock.patch.object(ledger, "LEDGER_FILE", path):
                usage = ledger.usage_by_project("p")
        self.assertEqual((usage["video_cny"], usage["video_seconds"]), (2.6, 20.0))
        self.assertEqual(usage["duplicate_submit_count"], 1)
        self.assertEqual(usage["duplicate_submissions"], [{"ref": "T1", "submits": 2}])
        self.assertEqual((usage["text_calls"], usage["text_failures"]), (2, 1))
        self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"]), (12, 4))
        self.assertEqual(usage["tikhub_calls"], 1)


class HumanDecisionTest(unittest.TestCase):
    def test_record_is_schema_valid_and_latest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "run-a"
            (project / "reviews").mkdir(parents=True)
            (project / "reviews" / "T1.decision.json").write_text(json.dumps({"decision": "repair"}), encoding="utf-8")
            first = human.record(project, "T1", "iterate", "需要修正产品标签细节", "甲",
                                 dt.datetime(2026, 11, 13, 1, 2, 3, tzinfo=dt.timezone.utc))
            second = human.record(project, "T1", "accept", "复查后确认已经可以接受", "乙",
                                  dt.datetime(2026, 11, 13, 1, 3, 4, tzinfo=dt.timezone.utc))
            definition = json.loads(human.SCHEMA_PATH.read_text(encoding="utf-8"))
            self.assertEqual(schema.check(first, definition), [])
            self.assertEqual(first["expert_decision"], "repair")
            self.assertEqual(human.latest_by_take(project / "human_decisions.jsonl")["T1"], second)


class CalibrationTest(unittest.TestCase):
    def test_runs_with_and_without_decisions_and_reports_error_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "run-a"
            (project / "reviews").mkdir(parents=True)
            self.assertEqual(calibrate.analyze([project])["total"], 0)
            for take, decision in (("PASS", "pass"), ("REPAIR", "repair"), ("AGREE", "repair")):
                (project / "reviews" / f"{take}.decision.json").write_text(
                    json.dumps({"decision": decision, "reason": "专家理由"}), encoding="utf-8")
            write_jsonl(project / "human_decisions.jsonl", [
                {"project": "run-a", "take": "PASS", "decision": "redo"},
                {"project": "run-a", "take": "REPAIR", "decision": "accept"},
                {"project": "run-a", "take": "AGREE", "decision": "iterate"},
            ])
            result = calibrate.analyze([project])
        self.assertEqual((result["total"], result["agree"]), (3, 1))
        self.assertEqual(len(result["false_release"]), 1)
        self.assertEqual(len(result["false_block"]), 1)


class ProductionHumanBoardTest(unittest.TestCase):
    def test_latest_human_and_ledger_usage_appear_in_board(self):
        scores = {name: 9 for name in ("soul_fidelity", "product_consistency", "person_consistency",
                                       "visual_quality", "audio_text", "pacing_structure", "platform_signals")}
        takes = {"briefs": [{"id": "A", "takes": [{"id": "T1", "attempt": 1, "cost_cny": 1.3,
                                                        "reviews": [{"scores": scores, "vetoes": [], "issues": []}]}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "run-a"
            project.mkdir()
            (project / "takes.json").write_text(json.dumps(takes), encoding="utf-8")
            write_jsonl(project / "human_decisions.jsonl", [
                {"take": "T1", "decision": "redo", "reason": "旧的人工判断理由", "decided_at": "2026-11-13T01:00:00+00:00"},
                {"take": "T1", "decision": "accept", "reason": "最新人工判断可以接受", "decided_at": "2026-11-13T02:00:00+00:00"},
            ])
            ledger_path = Path(tmp) / "costs.jsonl"
            write_jsonl(ledger_path, [
                {"provider": "tokensfactory", "endpoint": "minimax-H3", "status": "submitted", "project": "run-a",
                 "ref": "T1", "units": 10, "estimate_cny": 1.3},
                {"provider": "tokensfactory", "endpoint": "gemini-3.8-flash", "status": "ok", "project": "run-a",
                 "units": 1, "kind": "text", "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            ])
            with mock.patch.object(ledger, "LEDGER_FILE", ledger_path):
                result = production.build(project / "takes.json")
            html = dashboard.render({"production": result}, "测试")
        take = result["briefs"][0]["takes"][0]
        self.assertEqual(take["human"]["decision"], "accept")
        self.assertEqual(result["stats"]["human_agreement_rate"], 1.0)
        self.assertEqual(result["stats"]["text_calls"], 1)
        for text in ("人工判定数", "专家与人工一致率", "视频（元）", "文本模型（次数 / token，价格未配置）", "重复提交次数"):
            self.assertIn(text, html)
        self.assertIn("未人工判定", html)


if __name__ == "__main__":
    unittest.main()
