"""P1 中台执行机：全部使用假中台与假步骤，不发起付费调用。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import checkpoints  # noqa: E402
import engine  # noqa: E402
import platform_client  # noqa: E402
import platform_runner  # noqa: E402
import products  # noqa: E402
import settings  # noqa: E402


class ApiHandler(BaseHTTPRequestHandler):
    calls = []
    fail_once = False

    def log_message(self, *_):
        pass

    def _json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        self.__class__.calls.append(("GET", self.path, None, dict(self.headers)))
        if self.path.endswith("/refs/1"):
            body = b"fake-image"
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        self._json(200, {"requests": []})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.__class__.calls.append(("POST", self.path, body, dict(self.headers)))
        if "claim-conflict" in self.path and self.path.endswith("/claim"):
            self._json(409, {"error": "REQUEST_ALREADY_CLAIMED"}); return
        if self.path.endswith("/retry") and self.__class__.fail_once:
            self.__class__.fail_once = False; self._json(503, {"error": "busy"}); return
        if self.path == "/api/v3/uploads":
            self._json(201, {"id": "video-1"}); return
        self._json(200, {"ok": True})


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.token = self.root / "token"; self.token.write_text("ffa_test-token", encoding="utf-8")
        self.token.chmod(0o600)
        ApiHandler.calls = []; ApiHandler.fail_once = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.client = platform_client.PlatformClient(
            f"http://127.0.0.1:{self.server.server_port}", self.token, "runner-test", timeout=2)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def test_token_permissions_must_be_600(self):
        self.token.chmod(0o644)
        with self.assertRaisesRegex(platform_client.PlatformError, "权限必须为 600"):
            platform_client.PlatformClient("http://127.0.0.1", self.token)

    def test_claim_conflict_is_exposed_as_409_without_token(self):
        with self.assertRaises(platform_client.PlatformError) as raised:
            self.client.claim("claim-conflict")
        self.assertEqual(raised.exception.status, 409)
        self.assertNotIn("ffa_test", str(raised.exception))

    def test_5xx_retries_once_and_checkpoint_posts_are_identical(self):
        ApiHandler.fail_once = True
        self.client._request("POST", "/retry", {})
        self.assertEqual(sum(path == "/retry" for _, path, _, _ in ApiHandler.calls), 2)
        for _ in range(2):
            self.client.create_checkpoint("r1", "cp1", "script", "脚本", {"segments": []})
        bodies = [body for method, path, body, _ in ApiHandler.calls if path.endswith("/checkpoints")]
        self.assertEqual(len(bodies), 2)
        self.assertEqual(json.loads(bodies[0]), json.loads(bodies[1]))

    def test_upload_uses_required_raw_mp4_headers(self):
        video = self.root / "result.mp4"; video.write_bytes(b"mp4-body")
        result = self.client.upload_video(video, company_id="c1", project_id="p1", title="成片",
                                          duration=3.2, upload_id="00000000-0000-0000-0000-000000000001")
        self.assertEqual(result, "video-1")
        _, _, body, headers = next(row for row in ApiHandler.calls if row[1] == "/api/v3/uploads")
        self.assertEqual(body, b"mp4-body")
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lowered["x-company-id"], "c1")
        self.assertEqual(lowered["x-project-id"], "p1")
        self.assertEqual(lowered["content-type"], "video/mp4")


class FakeClient:
    def __init__(self):
        self.progress_rows = []; self.created = []; self.remote = []; self.verdict_data = {}
        self.uploads = []; self.completions = []

    def progress(self, request_id, stage, message, status="running"):
        self.progress_rows.append((request_id, stage, message, status)); return {}

    def create_checkpoint(self, request_id, external_id, kind, summary, payload):
        row = {"externalId": external_id, "kind": kind, "status": "pending", "payload": payload}
        self.created.append(row)
        if not any(item["externalId"] == external_id for item in self.remote): self.remote.append(row)
        return row

    def checkpoints(self, _request_id):
        return self.remote

    def verdict(self, _request_id):
        return self.verdict_data

    def upload_video(self, path, **kwargs):
        self.uploads.append((str(path), kwargs)); return "video-1"

    def complete(self, request_id, version_id, internal_result):
        self.completions.append((request_id, version_id, internal_result)); return {"status": "uploaded"}


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"STUDIO_HOME": str(self.home), "PRODUCT_BUDGET_CNY": "5",
                                                "AUTO_APPROVE_CNY": "0", "IMAGE_PRICE_CNY": "0.2",
                                                "TTS_PRICE_CNY": "0.1"})
        self.env.start(); settings.load(force=True)
        self.old = products.STUDIO, products.PRODUCTS, products.QUEUE
        products.STUDIO = self.home; products.PRODUCTS = self.home / "products"
        products.QUEUE = products.PRODUCTS / "_queue.json"
        self.client = FakeClient(); self.runner = platform_runner.Runner(self.client)

    def tearDown(self):
        products.STUDIO, products.PRODUCTS, products.QUEUE = self.old
        self.env.stop(); settings.load(force=True); self.tmp.cleanup()

    def _request(self, budget=5):
        return {"id": "request-1", "companyId": "company-1", "projectId": "project-1",
                "productName": "Exact Product", "productFacts": ["Blue bottle"],
                "productRefCount": 1, "benchmarkUrl": "https://www.tiktok.com/test",
                "tier": "H", "budgetCny": budget}

    def _script_checkpoint(self):
        products.create("p1", "Exact Product", 5, "Exact Product blue bottle")
        run = products.new_run("p1", "test", date="20261114")
        shots = {"audio_mode": "voiceover", "shots": [{"id": "S01", "start": 0, "end": 4,
                  "voiceover": "source"}]}
        script = {"scene": "A bright kitchen", "shots": [{"id": "S01",
                  "visual_prompt": "A hand holds Exact Product blue bottle", "voiceover": "Blue bottle",
                  "caption": "Blue bottle", "onscreen_copy": "", "end_state": "Bottle centered"}]}
        (run / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
        (run / "script.json").write_text(json.dumps(script), encoding="utf-8")
        subject = {"script": str((run / "script.json").relative_to(self.home)), "sha256": "old"}
        cp = checkpoints.create("p1", run.name, "script", "确认脚本", subject)
        return run, cp

    def test_budget_over_product_limit_reports_failed(self):
        state = {"request": self._request(5.01), "phase": "claimed", "checkpointMap": {}}
        result = self.runner.process(state)
        self.assertEqual(result["phase"], "failed")
        self.assertIn("超过单品上限", result["error"])
        self.assertEqual(self.client.progress_rows[-1][3], "failed")

    def test_approved_script_maps_to_local_checkpoint(self):
        run, cp = self._script_checkpoint()
        self.client.remote = [{"externalId": cp["id"], "status": "approved",
                               "decisionNote": "可以执行", "decidedBy": "user-1", "edits": None}]
        state = {"request": self._request(), "checkpointMap": {}}
        self.assertTrue(self.runner._resolve_platform_checkpoint(state, cp, run))
        self.assertEqual(checkpoints.get(cp["id"])["status"], "approved")
        self.assertEqual(len(self.client.created), 1)

    def test_invalid_script_edit_writes_error_to_new_pending_node(self):
        run, cp = self._script_checkpoint()
        self.client.remote = [{"externalId": cp["id"], "status": "changes_requested",
            "decisionNote": "修改参数", "decidedBy": "user-1",
            "edits": [{"segment": 1, "field": "voiceover", "value": "Claim 999 benefits"}]}]
        state = {"request": self._request(), "checkpointMap": {}}
        self.assertFalse(self.runner._resolve_platform_checkpoint(state, cp, run))
        self.assertEqual(checkpoints.get(cp["id"])["status"], "pending")
        self.assertIn(":validation:", state["checkpointMap"][cp["id"]])
        self.assertIn("validationError", self.client.created[-1]["payload"])
        self.assertEqual(state["phase"], "waiting_human")

    def test_budget_decision_maps_and_rejection_reports_failure(self):
        products.create("p1", "Exact Product", 5, "Exact Product")
        run = products.new_run("p1", "test", date="20261114")
        cp = checkpoints.create("p1", run.name, "budget", "确认预算",
                                {"estimated_cny": 1, "totalCny": 1, "units": []})
        self.client.remote = [{"externalId": cp["id"], "status": "approved",
                               "decisionNote": "", "decidedBy": "user-1", "edits": None}]
        state = {"request": self._request(), "checkpointMap": {}}
        self.assertTrue(self.runner._resolve_platform_checkpoint(state, cp, run))
        self.assertEqual(checkpoints.get(cp["id"])["status"], "approved")

    def test_rejected_budget_maps_before_stopping(self):
        products.create("p1", "Exact Product", 5, "Exact Product")
        run = products.new_run("p1", "test", date="20261114")
        cp = checkpoints.create("p1", run.name, "budget", "确认预算",
                                {"estimated_cny": 1, "totalCny": 1, "units": []})
        self.client.remote = [{"externalId": cp["id"], "status": "rejected",
                               "decisionNote": "本轮不制作", "decidedBy": "user-1", "edits": None}]
        state = {"request": self._request(), "checkpointMap": {}}
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            self.runner._resolve_platform_checkpoint(state, cp, run)
        self.assertEqual(checkpoints.get(cp["id"])["status"], "rejected")

    def test_verdict_is_written_to_checkpoint_and_human_log(self):
        products.create("p1", "Exact Product", 5, "Exact Product")
        run = products.new_run("p1", "test", date="20261114")
        (run / "reviews").mkdir()
        (run / "reviews" / "T1.decision.json").write_text(json.dumps({"decision": "pass"}), encoding="utf-8")
        cp = checkpoints.create("p1", run.name, "verdict", "人工判定",
                                {"take": "T1", "decision": "pass", "review": "x"})
        self.client.verdict_data = {"reviewStatus": "approved", "cases": [
            {"decision": "approved", "reason": None}]}
        state = {"request": self._request(), "productId": "p1", "run": run.name,
                 "phase": "waiting_verdict", "take": "T1", "verdictCheckpoint": cp["id"]}
        result = self.runner._poll_verdict(state)
        self.assertEqual(result["phase"], "done")
        self.assertEqual(checkpoints.get(cp["id"])["status"], "approved")
        row = json.loads((run / "human_decisions.jsonl").read_text().strip())
        self.assertEqual(row["decision"], "accept")

    def test_upload_then_reads_rejected_verdict_and_writes_back(self):
        products.create("p1", "Exact Product", 5, "Exact Product")
        run = products.new_run("p1", "test", date="20261114")
        (run / "takes").mkdir(); (run / "takes" / "T1.mp4").write_bytes(b"fake-mp4")
        (run / "reviews").mkdir()
        (run / "reviews" / "T1.json").write_text(json.dumps({"experts": []}), encoding="utf-8")
        (run / "reviews" / "T1.decision.json").write_text(json.dumps({"decision": "pass"}), encoding="utf-8")
        cp = checkpoints.create("p1", run.name, "verdict", "人工判定",
                                {"take": "T1", "decision": "pass", "review": "x"})
        self.client.verdict_data = {"reviewStatus": "rejected", "cases": [
            {"decision": "rejected", "reason": "产品展示不够清楚"}]}
        state = {"request": self._request(), "productId": "p1", "run": run.name,
                 "phase": "running", "checkpointMap": {}}
        current = {"stage": "verdict", "blocking_checkpoint": cp}
        with mock.patch.object(platform_runner, "_duration", return_value=4.0):
            result = self.runner._upload(state, current)
        self.assertEqual(self.client.uploads[0][1]["company_id"], "company-1")
        self.assertEqual(self.client.completions[0][1], "video-1")
        self.assertEqual(result["platformVerdict"], "rejected")
        self.assertEqual(checkpoints.get(cp["id"])["status"], "rejected")


class EnginePipelineBudgetTest(RunnerCase):
    def test_budget_includes_video_scene_and_tts(self):
        brief = self.home / "brief.json"
        brief.write_text(json.dumps({"id": "T1", "segments": [{"duration": 10,
            "request_duration": 10, "video_route": "default", "resolution": "768p",
            "audio_route": "tts", "estimated_cny": 1.3}]}), encoding="utf-8")
        with mock.patch.object(settings, "video_unit_price", return_value=0.13):
            value = engine._budget_details([brief])
        self.assertEqual(value["estimated_cny"], 1.6)
        self.assertEqual([row["kind"] for row in value["units"]], ["video", "keyframe", "tts"])

    def test_engine_exposes_keyframe_generation_compose_review_sequence(self):
        products.create("flow", "Exact Product", 5, "Exact Product")
        ref = self.home / "product.jpg"; ref.write_bytes(b"image")
        products.register_references("flow", [ref])
        products.select_benchmarks("flow", ["M001"])
        run = products.new_run("flow", "test", date="20261114")
        first = engine.status("flow", run.name)
        benchmark = checkpoints.create("flow", run.name, "benchmark", first["action"]["summary"],
                                       first["action"]["subject"])
        checkpoints.resolve(benchmark["id"], "approved", "来自中台请求", "platform-request")
        (products.product_dir("flow") / "plan.json").write_text("{}", encoding="utf-8")
        shots = {"audio_mode": "voiceover", "shots": [{"id": "S01", "start": 0, "end": 10,
                 "voiceover": "say it"}]}
        script = {"scene": "A studio", "shots": [{"id": "S01", "visual_prompt": "Exact Product",
                  "voiceover": "say it", "caption": "say it", "onscreen_copy": "",
                  "end_state": "centered"}]}
        (run / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
        (run / "script.json").write_text(json.dumps(script), encoding="utf-8")
        script_state = engine.status("flow", run.name)
        script_cp = checkpoints.create("flow", run.name, "script", script_state["action"]["summary"],
                                       script_state["action"]["subject"])
        checkpoints.resolve(script_cp["id"], "approved", "脚本已确认", "platform-request")
        (run / "briefs").mkdir()
        brief = {"id": "hq", "segments": [{"prompt": "Exact Product", "duration": 10,
                 "request_duration": 10, "audio_route": "tts", "estimated_cny": 1.3,
                 "video_route": "default", "resolution": "768p"}], "estimated_cny": 1.3}
        (run / "briefs" / "hq.json").write_text(json.dumps(brief), encoding="utf-8")
        budget_state = engine.status("flow", run.name)
        budget_cp = checkpoints.create("flow", run.name, "budget", budget_state["action"]["summary"],
                                       budget_state["action"]["subject"])
        checkpoints.resolve(budget_cp["id"], "approved", "预算已确认", "platform-request")
        self.assertEqual(engine.status("flow", run.name)["stage"], "keyframe")
        (run / "scene.png").write_bytes(b"scene")
        generation = engine.status("flow", run.name)
        self.assertEqual(generation["stage"], "generation")
        self.assertIn("--no-review", generation["action"]["command"])
        (run / "takes").mkdir(); (run / "takes" / "hq.mp4").write_bytes(b"video")
        (run / "jobs").mkdir(); (run / "jobs" / "hq.json").write_text(json.dumps({"status": "saved"}), encoding="utf-8")
        self.assertEqual(engine.status("flow", run.name)["stage"], "compose")
        (run / "jobs" / "hq.json").write_text(json.dumps({"status": "saved", "composed": True}), encoding="utf-8")
        self.assertEqual(engine.status("flow", run.name)["stage"], "review")


if __name__ == "__main__":
    unittest.main()
