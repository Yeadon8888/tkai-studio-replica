"""模型目录、自动选择、健康状态与请求校验。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import doctor  # noqa: E402
import loop  # noqa: E402
import models  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_catalog_schema_and_required_facts(self):
        catalog = models.load(force=True)
        ids = {item["id"] for item in catalog}
        self.assertGreaterEqual(len(catalog), 50)
        self.assertEqual(models.get("gemini-3.8-flash")["reads_video"], False)
        self.assertEqual(models.get("gemini-3.8-flash-low")["reads_video"], True)
        for model_id in ("minimax-H3", "minimax-H3-fast", "minimax-H3-cinema",
                         "minimax-H3-first-last", "minimax_h3_zm_titi"):
            self.assertEqual(models.get(model_id)["policy"], "allowed")
        for model_id in ("wan3", "muse-video", "seedance2.5", "minimax_h3_zm_titim"):
            self.assertEqual(models.get(model_id)["policy"], "ask_first")
        for model_id in ("nano-banana-2", "veo-3-1", "veo-omni-flash", "seedance2.0-900"):
            self.assertIn(model_id, ids)
            self.assertEqual(models.get(model_id)["policy"], "retired")


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.home_patch = mock.patch("settings.studio_home", return_value=self.home)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.tmp.cleanup()

    def test_selects_lowest_price_and_filters_policy(self):
        with mock.patch.object(models, "_allowed_ids", return_value=None), \
             mock.patch("settings.explicit_value", return_value=None):
            chosen = models.select("video", {"duration": 5, "resolution": "960p", "aspect_ratio": "9:16",
                                               "images": 1, "audio_urls": 0,
                                               "first_last": False, "ref_video": False})
        self.assertEqual(chosen["model"], "minimax-H3-cinema")
        self.assertEqual(chosen["estimate_cny"], 1.15)

    def test_whitelist_and_health_failure_are_explained(self):
        health = self.home / ".cache/model_health.json"
        health.parent.mkdir(parents=True)
        health.write_text(json.dumps({"models": {"minimax-H3-cinema": {
            "healthy": False, "reason": "探针失败"}}}), encoding="utf-8")
        with mock.patch.object(models, "_allowed_ids", return_value={"minimax-H3-cinema"}), \
             mock.patch("settings.explicit_value", return_value=None):
            with self.assertRaisesRegex(ValueError, "体检异常：探针失败"):
                models.select("video", {"duration": 5, "resolution": "960p", "images": 1})

    def test_explicit_model_mismatch_does_not_fallback(self):
        with mock.patch.object(models, "_allowed_ids", return_value=None), \
             mock.patch("settings.explicit_value", return_value=None):
            with self.assertRaisesRegex(ValueError, "显式指定.*不满足需求"):
                models.select("video", {"model": "minimax-H3", "duration": 5,
                                        "resolution": "960p", "images": 1})

    def test_ask_first_requires_explicit_consent(self):
        need = {"model": "muse-video", "duration": 10, "resolution": "720p", "images": 1,
                "aspect_ratio": "9:16", "first_last": False, "ref_video": False}
        with mock.patch.object(models, "_allowed_ids", return_value=None), \
             mock.patch("settings.explicit_value", return_value=None):
            with self.assertRaisesRegex(ValueError, "需要人先确认"):
                models.select("video", need)
            chosen = models.select("video", {**need, "allow_ask_first": True})
        self.assertEqual(chosen["model"], "muse-video")


class RequestAndDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = self.root / "image.png"
        self.image.write_bytes(b"image")

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_request_reports_all_catalog_violations(self):
        with self.assertRaisesRegex(ValueError, "时长 5s 不支持.*分辨率 960p 不支持.*图片数 0 不支持"):
            models.check_request("minimax-H3", {"duration": 5, "resolution": "960p",
                                                "aspect_ratio": "9:16", "images": []})

    def test_video_route_auto_selects_by_need(self):
        brief = {"video_route": "auto", "prompt": "move", "images": [str(self.image)],
                 "duration": 5, "resolution": "960p", "aspect_ratio": "9:16"}
        with mock.patch("settings.explicit_value", return_value=None), \
             mock.patch.object(models, "_allowed_ids", return_value=None):
            route, body = loop.build_video_request(brief)
        self.assertEqual(route, "auto")
        self.assertEqual(body["model"], "minimax-H3-cinema")

    def test_probe_result_is_written_and_failed_model_is_skipped(self):
        with mock.patch("settings.studio_home", return_value=self.root):
            doctor._record("minimax-H3-cinema", "availability", False, "清单缺失")
            with mock.patch.object(models, "_allowed_ids", return_value={"minimax-H3-cinema"}), \
                 mock.patch("settings.explicit_value", return_value=None):
                with self.assertRaisesRegex(ValueError, "体检异常：清单缺失"):
                    models.select("video", {"duration": 5, "resolution": "960p", "images": 1})
        self.assertTrue((self.root / "ledger/probes.jsonl").is_file())
        health = json.loads((self.root / ".cache/model_health.json").read_text(encoding="utf-8"))
        self.assertFalse(health["models"]["minimax-H3-cinema"]["healthy"])


if __name__ == "__main__":
    unittest.main()
