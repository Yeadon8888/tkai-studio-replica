"""R12 本地确认台接口、安全边界与写入透传。"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import checkpoints  # noqa: E402
import console  # noqa: E402
import products  # noqa: E402
import settings  # noqa: E402


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"STUDIO_HOME": str(self.home), "AUTO_APPROVE_CNY": "0"})
        self.env.start(); settings.load(force=True)
        self.old = products.STUDIO, products.PRODUCTS, products.QUEUE
        products.STUDIO = self.home; products.PRODUCTS = self.home / "products"
        products.QUEUE = products.PRODUCTS / "_queue.json"
        products.create("p1", "测试品", budget_cny=5, anchor="anchor")
        self.run = products.new_run("p1", "console", date="20261114")
        self.cp = checkpoints.create("p1", self.run.name, "benchmark", "确认对标", {"selected": []})
        self.server = console.make_server(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        products.STUDIO, products.PRODUCTS, products.QUEUE = self.old
        self.env.stop(); settings.load(force=True); self.tmp.cleanup()

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.headers, exc.read()
            finally:
                exc.close()

    def test_read_interfaces_and_media(self):
        status, _, body = self.request("/api/status")
        self.assertEqual(status, 200); self.assertEqual(json.loads(body)[0]["product"], "p1")
        self.assertEqual(json.loads(self.request("/api/pending")[2])[0]["id"], self.cp["id"])
        detail = json.loads(self.request(f"/api/checkpoint/{self.cp['id']}")[2])
        self.assertEqual(detail["kind"], "benchmark")
        media = self.run / "takes" / "T1.mp4"; media.parent.mkdir(); media.write_bytes(b"012345")
        code, headers, body = self.request("/media/p1/runs/20261114-console/takes/T1.mp4")
        self.assertEqual((code, body), (200, b"012345")); self.assertEqual(headers["Accept-Ranges"], "bytes")

    def test_status_materializes_current_checkpoint_without_paid_call(self):
        products.create("p2", "第二个测试品", budget_cny=5, anchor="anchor")
        products.new_run("p2", "console", date="20261114")
        ledger_path = self.home / "ledger" / "costs.jsonl"
        self.assertFalse(ledger_path.exists())
        status, _, body = self.request("/api/status")
        self.assertEqual(status, 200)
        p2 = next(row for row in json.loads(body) if row["product"] == "p2")
        self.assertEqual(p2["blocking_checkpoint"]["kind"], "benchmark")
        pending = json.loads(self.request("/api/pending")[2])
        self.assertEqual(len([row for row in pending if row["product"] == "p2"]), 1)
        self.request("/api/status")
        pending = json.loads(self.request("/api/pending")[2])
        self.assertEqual(len([row for row in pending if row["product"] == "p2"]), 1)
        self.assertFalse(ledger_path.exists())

    def test_uncited_low_is_human_readable_and_maps_legacy_sequence(self):
        text = console._uncited_low_text([
            {"judge": 1, "dim": "product_consistency", "score": 5},
            {"judge": 1, "dim": "soul_fidelity", "score": 5},
        ])
        self.assertEqual(text, ["产品专家：产品一致性 5 分、灵魂还原 5 分——未写原因"])

    def test_reference_fallback_order(self):
        run = self.run
        local = run / "benchmark" / "video.mp4"; local.parent.mkdir(); local.write_bytes(b"video")
        brief = {"replication_reference": "https://cdn.test/ref.mp4"}
        shots = {"candidate_id": "C023", "aweme_id": "123", "reference_url": "https://tiktok.test/original"}
        with mock.patch.object(console, "_remote_video", return_value=True):
            detail = console._reference_detail(run, brief, shots)
        self.assertEqual(detail["reference_source"], "local")
        local.unlink()
        with mock.patch.object(console, "_remote_video", return_value=True):
            detail = console._reference_detail(run, brief, shots)
        self.assertEqual((detail["reference_source"], detail["reference_video"]),
                         ("remote", "https://cdn.test/ref.mp4"))
        cover = run / "benchmark" / "cover.jpg"; cover.write_bytes(b"jpg")
        with mock.patch.object(console, "_remote_video", return_value=False):
            detail = console._reference_detail(run, brief, shots)
        self.assertEqual(detail["reference_source"], "cover")
        self.assertTrue(detail["reference_cover"].endswith("/benchmark/cover.jpg"))
        self.assertTrue(detail["reference_expired"])
        self.assertEqual(detail["reference_original"], "https://tiktok.test/original")

    def test_reference_does_not_serve_research_batch_outside_products(self):
        batch = self.home / "research" / "legacy-batch"
        (batch / "covers").mkdir(parents=True)
        (batch / "candidates.json").write_text(json.dumps([
            {"candidate_id": "C023", "aweme_id": "123"}
        ]), encoding="utf-8")
        (batch / "covers" / "123.jpg").write_bytes(b"jpg")
        shots = {"candidate_id": "C023", "aweme_id": "123"}
        with mock.patch.object(console, "_remote_video", return_value=False):
            detail = console._reference_detail(self.run, {}, shots)
        self.assertEqual(detail["reference_source"], "missing")
        self.assertEqual(detail["reference_cover"], "")

    def test_resolve_rules_are_passed_through(self):
        code, _, body = self.request(f"/api/checkpoint/{self.cp['id']}/resolve",
                                     {"status": "rejected", "note": "", "by": "测试",
                                      "fields": {"tier": "H"}})
        self.assertEqual(code, 400); self.assertIn("必须填写", json.loads(body)["error"])
        code, _, _ = self.request(f"/api/checkpoint/{self.cp['id']}/resolve",
                                  {"status": "rejected", "note": "不采用", "by": "测试"})
        self.assertEqual(code, 200)
        self.assertEqual(checkpoints.get(self.cp["id"])["status"], "rejected")

    def test_script_edit_validation_errors_are_structured(self):
        shots = {"duration": 4, "structure": [{"beat": "证明", "start": 0, "end": 4}],
                 "shots": [{"id": "S01", "start": 0, "end": 4, "voiceover": "source"}]}
        script = {"scene": "kitchen", "shots": [{"id": "S01", "visual_prompt": "hand and product",
                  "voiceover": "Test 16 parameters", "caption": "16 parameters",
                  "onscreen_copy": "16 parameters", "end_state": "centered"}]}
        (self.run / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
        script_path = self.run / "script.json"; script_path.write_text(json.dumps(script), encoding="utf-8")
        cp = checkpoints.create("p1", self.run.name, "script", "确认脚本",
                                {"script": str(script_path.relative_to(self.home))})
        code, _, body = self.request(f"/api/checkpoint/{cp['id']}/edit-script", {
            "by": "测试", "edits": [{"segment": 1, "field": "voiceover", "value": "Test 99 parameters"}]})
        result = json.loads(body)
        self.assertEqual(code, 400); self.assertTrue(result["errors"])
        self.assertEqual(json.loads(script_path.read_text())["shots"][0]["voiceover"], "Test 16 parameters")

    def test_directory_traversal_is_forbidden(self):
        secret = self.home / "secret.mp4"; secret.write_bytes(b"secret")
        code, _, body = self.request("/media/%2e%2e/secret.mp4")
        self.assertEqual(code, 403); self.assertNotIn(b"secret", body)
        code, _, _ = self.request("/research-media/legacy-batch/covers/123.jpg")
        self.assertEqual(code, 404)

    def test_server_factory_only_binds_loopback(self):
        class FakeServer:
            def __init__(self, address, handler):
                self.address, self.handler = address, handler
        server = console.make_server(9988, FakeServer)
        self.assertEqual(server.address, ("127.0.0.1", 9988))


if __name__ == "__main__":
    unittest.main()
