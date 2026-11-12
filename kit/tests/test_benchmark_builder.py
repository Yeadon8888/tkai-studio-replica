import json
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class BenchmarkBuilderTest(unittest.TestCase):
    def setUp(self):
        import settings
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_studio_home = settings.studio_home
        settings.studio_home = lambda: self.root
        anchor = self.root / "products" / "p" / "product_anchor.txt"
        anchor.parent.mkdir(parents=True)
        anchor.write_text("exact blue test strip bottle", encoding="utf-8")

    def tearDown(self):
        import settings
        settings.studio_home = self.old_studio_home
        self.tmp.cleanup()

    def make_batch(self, local=False):
        batch = self.root / "batch"
        candidate = {"aweme_id": "a1", "candidate_id": "M001", "desc": "demo",
                     "play_urls": ["bad", "good"]}
        if local:
            candidate["local_file"] = str(self.root / "clip.mp4")
            Path(candidate["local_file"]).write_bytes(b"clip")
        write_json(batch / "candidates.json", [candidate])
        write_json(batch / "breakdown.json", [{"aweme_id": "a1", "candidate_id": "M001",
                                                 "dims": {"可复制": 2}, "has_person": True}])
        return batch

    @staticmethod
    def h_response(**overrides):
        obj = {
            "fixed": {"style": "handheld UGC", "action": "show and dip", "scene": "poolside",
                      "camera": "vertical close-up", "rhythm": "show, dip, compare"},
            "must_keep": ["show bottle", "dip strip", "compare result"],
            "prompt_fixed": "Vertical handheld UGC. Show the bottle, dip the strip, then compare the result.",
            "fixed_keywords": ["handheld", "poolside"], "has_person": True,
        }
        obj.update(overrides)
        return json.dumps(obj)

    def test_link_uses_first_reachable_url_and_writes_valid_h_file(self):
        import benchmark_builder as bb
        seen = {}

        def model(parts, **kwargs):
            seen["url"] = parts[1]["video_url"]
            seen["role"] = kwargs["role"]
            return self.h_response()

        run = self.root / "run"
        result = bb.build(self.make_batch(), "M001", "p", "H", run, model_call=model,
                          reachable=lambda url: url == "good")
        self.assertTrue(result["ok"])
        self.assertEqual(seen, {"url": "good", "role": "breakdown"})
        bench = json.loads((run / "benchmarks" / "M001.json").read_text(encoding="utf-8"))
        self.assertEqual(bench["product_anchor"], "exact blue test strip bottle")
        self.assertEqual(bench["tier"], "H")

    def test_expired_play_urls_are_refreshed_once(self):
        import benchmark_builder as bb
        seen, refreshed = {}, []

        def model(parts, **kwargs):
            seen["url"] = parts[1]["video_url"]
            return self.h_response()

        def refresh(candidate, batch):
            refreshed.append(candidate["candidate_id"])
            return ["fresh"]

        result = bb.build(self.make_batch(), "M001", "p", "H", self.root / "run2", model_call=model,
                          reachable=lambda url: url == "fresh", refresh=refresh)
        self.assertTrue(result["ok"])
        self.assertEqual(refreshed, ["M001"])
        self.assertEqual(seen["url"], "fresh")

    def test_invalid_model_fields_do_not_write_file(self):
        import benchmark_builder as bb
        run = self.root / "run"
        result = bb.build(self.make_batch(), "M001", "p", "H", run,
                          model_call=lambda *a, **k: json.dumps({"fixed": {"style": "UGC"}}),
                          reachable=lambda _: True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("must_keep" in e for e in result["errors"]))
        self.assertFalse((run / "benchmarks" / "M001.json").exists())

    def test_local_upload_is_deleted_even_when_validation_fails(self):
        import benchmark_builder as bb
        deleted = []
        result = bb.build(self.make_batch(local=True), "M001", "p", "H", self.root / "run",
                          model_call=lambda *a, **k: json.dumps({}),
                          upload=lambda _: {"url": "https://example.test/clip.mp4", "path": "clip.mp4"},
                          delete=lambda info: deleted.append(info["path"]) or True,
                          media_log=lambda *a: None)
        self.assertFalse(result["ok"])
        self.assertEqual(deleted, ["clip.mp4"])
        self.assertTrue(result["media_deleted"])


class IntakeBuildTest(unittest.TestCase):
    def test_h_is_marked_and_skipped_until_confirmed(self):
        import intake
        import products
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_batch = intake.batch_dir
            old_products = products.PRODUCTS
            intake.batch_dir = lambda pid: root / f"manual-{pid}"
            products.PRODUCTS = root / "products"
            try:
                batch = intake.batch_dir("p")
                write_json(batch / "candidates.json", [{"candidate_id": "M001"}])
                plan = intake.make_plan([{"id": "M001", "tier": "H"}], 1, use_all=True,
                                        price=0.1, duration=10)
                self.assertTrue(plan["items"][0]["needs_confirm"])
                write_json(products.PRODUCTS / "p" / "plan.json", plan)
                calls = []
                fake = lambda *args: calls.append(args) or {"ok": True, "path": "x"}
                first = intake.build_plan("p", root / "run", build_func=fake)
                self.assertTrue(first[0]["skipped"])
                self.assertEqual(calls, [])

                intake.confirm_tier("p", "M001", "H", "逐镜复核后确认")
                second = intake.build_plan("p", root / "run", build_func=fake)
                self.assertTrue(second[0]["ok"])
                self.assertEqual(calls[0][3], "H")
            finally:
                intake.batch_dir = old_batch
                products.PRODUCTS = old_products


class TierOverrideTest(unittest.TestCase):
    def test_research_build_applies_human_override(self):
        import research
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            write_json(batch / "candidates.json", [{"aweme_id": "a1", "candidate_id": "M001",
                       "hard_pass": True, "final_pass": True, "topic_level": "strong", "desc": "x"}])
            write_json(batch / "verify.json", [{"aweme_id": "a1", "verdict": "A",
                                                 "product_relation": "same"}])
            write_json(batch / "breakdown.json", [{"aweme_id": "a1", "dims": {"钩子": 2, "人": 1, "货": 2,
                       "场": 2, "证明": 2, "行动": 1, "可复制": 2}}])
            write_json(batch / "tier_overrides.json", {"M001": {"tier": "L", "note": "精细动作风险高"}})
            case = research.build(batch)["cases"][0]
        self.assertEqual(case["tier"], "L")
        self.assertIn("人工确认", case["tier_reasons"][0])
        self.assertIn("精细动作风险高", case["tier_reasons"][0])


if __name__ == "__main__":
    unittest.main()
