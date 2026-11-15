import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import calibrate  # noqa: E402
import compose  # noqa: E402
import doctor  # noqa: E402
import essence  # noqa: E402
import gate  # noqa: E402
import loop  # noqa: E402
import audio_transcript  # noqa: E402
import review_panel  # noqa: E402
import shot_list  # noqa: E402
import shot_plan  # noqa: E402


def shots(audio_mode="on_camera_speech"):
    return {"candidate_id": "C1", "tier": "H", "duration": 10, "audio_mode": audio_mode,
            "structure": [{"beat": "钩子", "start": 0, "end": 4},
                          {"beat": "证明", "start": 4, "end": 10}],
            "shots": [
                {"id": "S01", "start": 0, "end": 4, "scale": "近景", "camera": "正面",
                 "action": "举起产品", "product_role": "subject", "hand_action": True,
                 "speaker_on_screen": True, "onscreen_text": "", "voiceover": "Try it now",
                 "music_sfx": "", "rhythm": "excited", "hook_reason": "动作",
                 "join": "cut", "keep": "keep", "redo_reason": "", "end_state": "举起"},
                {"id": "S02", "start": 4, "end": 10, "scale": "俯拍", "camera": "固定",
                 "action": "竞品动作", "product_role": "subject", "hand_action": True,
                 "speaker_on_screen": False, "onscreen_text": "", "voiceover": "",
                 "music_sfx": "beat", "rhythm": "fast", "hook_reason": "",
                 "join": "cut", "keep": "redo", "redo_reason": "不适配", "end_state": "放下"}]}


class EssenceTest(unittest.TestCase):
    def test_list_excludes_redo_action_and_requires_confirmed_tier(self):
        data = essence.build_items(shots())
        ids = {x["id"] for x in data["items"]}
        self.assertIn("expert-action-S01", ids)
        self.assertNotIn("expert-action-S02", ids)
        bad = shots(); bad.pop("tier")
        with self.assertRaisesRegex(ValueError, "不猜档位"):
            essence.build_items(bad)

    def test_code_items_score_and_retry_classification(self):
        data = essence.build_items(shots())
        rows = essence.code_results(data, shots(), "unused", {"beats": ["钩子", "证明"]},
                                    cuts=[4], duration=10)
        self.assertTrue(all(x["status"] == "pass" for x in rows))
        expert = [{"id": x["id"], "status": "pass", "at": "00:01", "detail": "可见"}
                  for x in data["items"] if x["judge"] == "expert"]
        expert[0]["status"] = "unclear"
        result = essence.merge_results(data, rows, expert)
        self.assertEqual(result["pass_count"], result["total"] - 1)
        self.assertIn(expert[0]["id"], result["unclear"])
        result["items"][0]["status"] = "fail"
        self.assertEqual(essence.retry_from(result), "script")

    def test_gate_uses_tier_threshold_before_display_score(self):
        rubric = gate.load_rubric()
        review = {"scores": {k: 9 for k in rubric["dimensions"]}, "vetoes": [], "issues": []}
        er = {"tier": "H", "essence_score": .79, "unclear": [],
              "items": [{"category": "camera", "status": "fail"}]}
        decision = gate.evaluate(review, 1, rubric, er)
        self.assertEqual((decision["decision"], decision["retry_from"]), ("repair", "generate"))

    def test_r_expert_keeps_item_results(self):
        exp = {"id": "R", "name": "还原", "focus": "{essence_list} {shot_list}",
               "prompt": "{focus} {brief} {anchor} {frame_times} {timecode_rule} {shared_rules}",
               "shared_rules": "", "temperature": .1}
        payload = {"scores": {"replication_fidelity": 8}, "checks": {}, "issues": [], "vetoes": [],
                   "essence": [{"id": "expert-hook", "status": "pass", "at": "00:01", "detail": "一致"}]}
        brief = {"duration": 10, "essence_items": [{"id": "expert-hook"}]}
        with mock.patch.object(review_panel.loop, "gemini", return_value=json.dumps(payload)):
            result = review_panel.run_expert(exp, "p", "t", brief, "", [], video_url="https://v")
        self.assertEqual(result["essence"][0]["status"], "pass")


class AudioRouteTest(unittest.TestCase):
    def test_audio_mode_validation_and_auto_route(self):
        data = shots("bad")
        with self.assertRaisesRegex(ValueError, "audio_mode"):
            shot_list.validate(data)
        self.assertEqual(shot_plan.audio_route("on_camera_speech", "auto"), "native")
        self.assertEqual(shot_plan.audio_route("voiceover", "auto"), "tts")

    def test_visible_special_voice_uses_native_and_embeds_dialogue(self):
        import format_invariants
        import script_writer
        with tempfile.TemporaryDirectory() as tmp:
            scene = Path(tmp) / "scene.png"; ref = Path(tmp) / "ref.jpg"; ref.write_bytes(b"x")
            data = shots("special_voice")
            script = {"scene": "kitchen", "shots": [
                {"id": "S01", "visual_prompt": "The person says in English, in a playful tone: 'Try it now'",
                 "voiceover": "Try it now", "caption": "Try it now", "onscreen_copy": "", "end_state": "up"},
                {"id": "S02", "visual_prompt": "hand moves", "voiceover": "",
                 "caption": "Result", "onscreen_copy": "", "end_state": "down"}]}
            facts = {"product_anchor": "test strips", "confirmed_selling_points": []}
            self.assertFalse(any("出镜口播" in error
                                 for error in script_writer.validate(script, data, facts)))
            prompt = script_writer._prompt(data, facts, [], format_invariants.derive(data))
            self.assertIn("speaker_on_screen=false 的 voiceover、special_voice", prompt)
            plan = shot_plan.make_plan(data, script, [ref], scene_path=scene, seed=1)
        self.assertTrue(all(row["audio_route"] == "native" for row in plan["segments"]))

    def test_on_camera_match_cut_uses_independent_native_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene = Path(tmp) / "scene.png"; scene.write_bytes(b"x")
            ref = Path(tmp) / "ref.jpg"; ref.write_bytes(b"x")
            data = shots(); data["shots"][1]["join"] = "match_cut"; data["shots"][1]["keep"] = "keep"
            script = {"scene": "kitchen", "shots": [
                {"id": "S01", "visual_prompt": "person talks", "voiceover": "Try it now",
                 "caption": "Try it now", "onscreen_copy": "", "end_state": "up"},
                {"id": "S02", "visual_prompt": "hand moves", "voiceover": "",
                 "caption": "Result", "onscreen_copy": "", "end_state": "down"}]}
            plan = shot_plan.make_plan(data, script, [ref], scene_path=scene, seed=1,
                                       first_last_audio={"usable": False, "reason": "无历史证据"})
        second = plan["segments"][1]
        self.assertEqual((second["video_route"], second["audio_route"]), ("quality", "native"))
        self.assertEqual(second["join_strategy"], "hard_cut_for_native_audio")
        self.assertNotIn("first_frame", second)

    def test_native_compose_keeps_source_audio_and_ducks_music(self):
        rows = [{"start": 0, "end": 10, "caption": "hello", "onscreen_copy": ""}]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(compose, "mean_volume", return_value=-20), \
             mock.patch.object(compose.subprocess, "run") as run:
            info = compose.compose_native("source.mp4", "music.wav", rows, Path(tmp) / "out.mp4", tmp)
        command = run.call_args.args[0]
        self.assertIn("[0:a]loudnorm", command[command.index("-filter_complex") + 1])
        self.assertEqual(info["engine"], "ffmpeg-native")

    def test_mixed_compose_uses_native_and_tts_windows(self):
        rows = [{"start": 0, "end": 4, "caption": "a", "onscreen_copy": ""},
                {"start": 4, "end": 10, "caption": "b", "onscreen_copy": ""}]
        segments = [{"audio_route": "native"}, {"audio_route": "tts"}]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(compose, "mean_volume", return_value=-20), \
             mock.patch.object(compose.subprocess, "run") as run:
            info = compose.compose_mixed("source.mp4", "voice.wav", "music.wav", rows, segments,
                                         Path(tmp) / "out.mp4", tmp)
        graph = run.call_args.args[0][run.call_args.args[0].index("-filter_complex") + 1]
        self.assertIn("[0:a]atrim=start=0.000:end=4.000", graph)
        self.assertIn("[1:a]atrim=start=0.000:end=6.000", graph)
        self.assertEqual(info["engine"], "ffmpeg-mixed")


class OutputLimitTest(unittest.TestCase):
    def test_audio_requires_tokens_and_sends_user_agent_and_low_reasoning(self):
        parts = [{"type": "input_audio", "input_audio": {"data": "eA==", "format": "mp3"}}]
        bodies, headers = [], []

        def response(_method, _url, request_headers, body, timeout):
            headers.append(request_headers); bodies.append(body)
            return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                    "usage": {"prompt_tokens_details": {"audio_tokens": 481}}}

        with mock.patch.object(loop.settings, "model", return_value="gemini-3.8-flash"), \
             mock.patch.object(loop.models, "get", return_value={}), \
             mock.patch.object(loop.tt, "load_tf_env", return_value=("https://x", "secret")), \
             mock.patch.object(loop.common, "http_json", side_effect=response), \
             mock.patch.object(loop.ledger, "count_text_calls", return_value=0), \
             mock.patch.object(loop.ledger, "append"):
            self.assertEqual(loop.gemini(parts, "p", "r", role="transcribe",
                                         reasoning_effort="low"), "ok")
        self.assertTrue(headers[0]["User-Agent"])
        self.assertEqual(bodies[0]["reasoning_effort"], "low")

        with mock.patch.object(loop.settings, "model", return_value="gemini-3.8-flash"), \
             mock.patch.object(loop.models, "get", return_value={}), \
             mock.patch.object(loop.tt, "load_tf_env", return_value=("https://x", "secret")), \
             mock.patch.object(loop.common, "http_json", return_value={
                 "choices": [{"finish_reason": "stop", "message": {"content": "invented"}}], "usage": {}}), \
             mock.patch.object(loop.ledger, "count_text_calls", return_value=0), \
             mock.patch.object(loop.ledger, "append"):
            with self.assertRaises(loop.AudioNotIngested):
                loop.gemini(parts, "p", "r", role="transcribe")

    def test_transcript_uses_only_mono_mp3_and_keeps_original_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, source = Path(tmp), Path(tmp) / "video.mp4"
            source.write_bytes(b"video")

            def extract(_video, output):
                output = Path(output); output.parent.mkdir(parents=True); output.write_bytes(b"mp3")
                return output

            def model(parts, **kwargs):
                self.assertEqual([part["type"] for part in parts], ["text", "input_audio"])
                self.assertEqual(kwargs["reasoning_effort"], "low")
                return json.dumps({"language": "en", "segments": [
                    {"start": 1.25, "end": 2.75, "speaker": "villain", "text": "Don't test me"}]})

            with mock.patch.object(audio_transcript, "extract_audio", side_effect=extract):
                value = audio_transcript.transcribe(source, project, model_call=model)
        self.assertEqual(value["timebase"], "reference_original_seconds")
        self.assertEqual(value["segments"][0]["start"], 1.25)
    def test_loop_uses_minimum_and_retries_length_once(self):
        replies = [
            {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "思考"}}], "usage": {}},
            {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {}},
        ]
        bodies = []
        def fake(_method, _url, _headers, body, timeout):
            bodies.append(body); return replies.pop(0)
        with mock.patch.object(loop.settings, "model", return_value="gemini-4-hydrogen"), \
             mock.patch.object(loop.models, "get", return_value={"min_output_tokens": 2000}), \
             mock.patch.object(loop.tt, "load_tf_env", return_value=("https://x", "secret")), \
             mock.patch.object(loop.common, "http_json", side_effect=fake), \
             mock.patch.object(loop.ledger, "count_text_calls", return_value=0), \
             mock.patch.object(loop.ledger, "append"):
            self.assertEqual(loop.gemini([{"type": "text", "text": "x"}], "p", "r", max_tokens=120), "ok")
        self.assertEqual([x["max_tokens"] for x in bodies], [2000, 4000])

    def test_doctor_video_read_uses_explicit_probe_model(self):
        with mock.patch.object(doctor, "models") as catalog, \
             mock.patch.object(doctor, "probe_video_read", return_value=[]) as probe:
            catalog.get.return_value = {"kind": "text"}
            doctor.run_probes("video_read,gemini-4-hydrogen", {"gemini-4-hydrogen"})
        probe.assert_called_once_with(["gemini-4-hydrogen"])


class EssenceCalibrationTest(unittest.TestCase):
    def test_reports_human_vs_threshold_agreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"; (run / "reviews").mkdir(parents=True)
            (run / "reviews" / "T1.json").write_text(json.dumps({
                "essence": {"essence_score": .81}, "decision": {"essence_min": .8}}), encoding="utf-8")
            (run / "essence_human.jsonl").write_text(json.dumps({"take": "T1", "reached": True}) + "\n", encoding="utf-8")
            result = calibrate.analyze([run])
        self.assertEqual((result["essence_total"], result["essence_agree"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
