"""R14 配乐路线的纯离线验收。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import compose  # noqa: E402
import engine  # noqa: E402
import music  # noqa: E402
import review_panel  # noqa: E402
import script_writer  # noqa: E402
import settings  # noqa: E402
import shot_plan  # noqa: E402


STYLE = {"mood": "warm nostalgic", "tempo_bpm": 82, "era_style": "mid-century lounge",
         "instruments": ["muted piano", "soft strings"], "energy_curve": "gentle rise then settle"}


def shots(mode="music_only"):
    return {"candidate_id": "C023", "tier": "H", "duration": 5, "audio_mode": mode,
            "structure": [{"beat": "show", "start": 0, "end": 5}],
            "shots": [{"id": "S01", "start": 0, "end": 5, "scale": "close", "camera": "fixed",
                       "action": "show product", "product_role": "subject", "hand_action": False,
                       "speaker_on_screen": False, "onscreen_text": "", "voiceover": "",
                       "music_sfx": "I Don't Want to Set the World on Fire by The Ink Spots",
                       "rhythm": "slow", "hook_reason": "", "join": "cut", "keep": "keep",
                       "redo_reason": "", "end_state": "product centered"}]}


def script(style=None):
    return {"scene": "A quiet tabletop", "music": style or STYLE,
            "shots": [{"id": "S01", "visual_prompt": "A hand presents the product", "voiceover": "",
                       "caption": "A calm result", "onscreen_copy": "", "end_state": "Product centered"}]}


class PromptAndStyleTest(unittest.TestCase):
    def test_music_and_ambient_append_no_voice_and_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); scene = root / "scene.png"; ref = root / "ref.jpg"
            scene.write_bytes(b"scene"); ref.write_bytes(b"ref")
            for mode, route in (("music_only", "music"), ("ambient", "ambient")):
                value = script_writer._append_no_voice(script(), mode)
                self.assertTrue(value["shots"][0]["visual_prompt"].endswith(script_writer.NO_VOICE))
                plan = shot_plan.make_plan(shots(mode), value, [ref], scene_path=scene, seed=1)
                self.assertEqual(plan["segments"][0]["audio_route"], route)
                self.assertIn(shot_plan.NO_VOICE, plan["segments"][0]["prompt"])

    def test_music_description_rejects_song_artist_and_imitation(self):
        bad_values = [
            {**STYLE, "mood": "I Don't Want to Set the World on Fire"},
            {**STYLE, "mood": "The Ink Spots"},
            {**STYLE, "era_style": "in the style of Famous Artist"},
            {**STYLE, "energy_curve": "like Famous Artist"},
        ]
        for value in bad_values:
            with self.subTest(value=value):
                errors = script_writer.validate(script(value), shots(), {}, [])
                self.assertTrue(any("music" in error for error in errors), errors)


class FakeClient:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.submits = 0
        self.fetches = 0

    def submit(self, _style):
        self.submits += 1
        return {"code": "success", "data": "task_123"}

    def fetch(self, task_id):
        self.fetches += 1
        status = self.statuses.pop(0)
        row = {"id": task_id, "status": status}
        if status == "SUCCESS":
            row["data"] = [{"audio_url": "https://example.test/music.mp3"}]
        if status == "FAILURE":
            row["fail_reason"] = "upstream failed"
        return {"data": [row]}

    def download(self, _url, output):
        Path(output).write_bytes(b"music")


class MusicLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"STUDIO_HOME": str(self.home), "MUSIC_PRICE_CNY": "2.5"})
        self.env.start(); settings.load(force=True)

    def tearDown(self):
        self.env.stop(); settings.load(force=True); self.tmp.cleanup()

    def test_pick_reuses_matching_long_enough_track(self):
        library = self.home / "library.jsonl"
        track = self.home / "track.mp3"; track.write_bytes(b"music")
        library.write_text(json.dumps({"id": "m1", "file": "track.mp3", "style": STYLE,
                                       "duration": 30, "source": "suno", "task_id": "t",
                                       "created": "now", "license_note": "original"}) + "\n", encoding="utf-8")
        self.assertEqual(music.pick(STYLE, 20, library)["id"], "m1")
        self.assertIsNone(music.pick(STYLE, 31, library))

    def test_submit_queue_success_downloads_and_records(self):
        project = self.home / "run"; project.mkdir()
        client = FakeClient(["PENDING", "SUCCESS"]); sleeps = []
        with mock.patch.object(music, "library_path", return_value=self.home / "library.jsonl"), \
             mock.patch.object(music, "music_root", return_value=self.home), \
             mock.patch.object(music.ledger, "append"):
            result = music.generate(STYLE, project, "brief", client, sleeps.append, interval=0,
                                    probe=lambda _cmd: "45.0")
        self.assertEqual((client.submits, client.fetches), (1, 2))
        self.assertTrue(all(value >= 20 for value in sleeps))
        self.assertEqual(result["duration"], 45.0)
        self.assertEqual(json.loads((project / "jobs/music-brief.json").read_text())["status"], "saved")

    def test_failure_stops_and_unknown_resumes_without_resubmit(self):
        failure_project = self.home / "failure"; failure_project.mkdir()
        with mock.patch.object(music.ledger, "append"):
            with self.assertRaisesRegex(RuntimeError, "upstream failed"):
                music.generate(STYLE, failure_project, "x", FakeClient(["FAILURE"]), lambda _x: None,
                               max_polls=0)

        project = self.home / "unknown"; project.mkdir(); client = FakeClient(["MYSTERY", "SUCCESS"])
        with mock.patch.object(music, "library_path", return_value=self.home / "unknown-library.jsonl"), \
             mock.patch.object(music, "music_root", return_value=self.home), \
             mock.patch.object(music.ledger, "append"):
            with self.assertRaises(TimeoutError):
                music.generate(STYLE, project, "x", client, lambda _x: None, max_polls=0)
            music.generate(STYLE, project, "x", client, lambda _x: None, max_polls=0,
                           probe=lambda _cmd: "40")
        self.assertEqual(client.submits, 1)


class BudgetComposeReviewTest(unittest.TestCase):
    def test_budget_has_new_or_reused_music_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.json"
            brief.write_text(json.dumps({"id": "T", "duration": 5, "music": STYLE, "segments": [{
                "duration": 5, "audio_route": "music", "video_route": "default",
                "resolution": "768p", "estimated_cny": 0.65}]}), encoding="utf-8")
            with mock.patch.object(settings, "video_unit_price", return_value=.13), \
                 mock.patch.object(settings, "image_price", return_value=0), \
                 mock.patch.object(music, "pick", return_value=None):
                new = engine._budget_details([brief])
            self.assertIn("配乐：新生成 2.5 元", [x.get("reason") for x in new["units"]])
            with mock.patch.object(settings, "video_unit_price", return_value=.13), \
                 mock.patch.object(settings, "image_price", return_value=0), \
                 mock.patch.object(music, "pick", return_value={"id": "m1"}):
                reused = engine._budget_details([brief])
            self.assertIn("配乐：复用曲库 0 元", [x.get("reason") for x in reused["units"]])

    def test_music_compose_discards_native_audio_and_missing_music_stops(self):
        rows = [{"start": 0, "end": 5, "caption": "calm", "onscreen_copy": ""}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); track = root / "music.wav"; track.write_bytes(b"music")
            with mock.patch.object(compose.subprocess, "run") as run:
                result = compose.compose_music("source.mp4", track, rows, root / "out.mp4", root)
            command = run.call_args.args[0]
            self.assertNotIn("[0:a]", command[command.index("-filter_complex") + 1])
            self.assertEqual(result["source_audio"], "discarded")
            with self.assertRaisesRegex(FileNotFoundError, "没有可用配乐"):
                compose.compose_music("source.mp4", None, rows, root / "out.mp4", root)

    def test_v_expert_and_machine_check_use_unwanted_voice_cause(self):
        brief = {"duration": 5, "segments": [{"duration": 5, "audio_route": "music"}],
                 "hydrogen_transcript": "a faint sung phrase"}
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"; video.write_bytes(b"x" * 60_000)
            probe = {"streams": [{"codec_type": "video", "width": 768, "height": 1365,
                                   "codec_name": "h264"}], "format": {"duration": "5"}}
            with mock.patch.object(review_panel, "_probe", return_value=probe):
                result = review_panel.machine_checks(video, brief)
        self.assertTrue(any("audio_unwanted_voice" in x for x in result["blocking"]))
        self.assertIn("cause=audio_unwanted_voice", review_panel.audio_rule(brief, True))
        with mock.patch.object(review_panel.loop, "gemini", return_value=json.dumps({
                "voice_detected": True, "transcript": "faint singing"})):
            heard = review_panel.unwanted_voice_check("https://example.test/video.mp4", "run", "take")
        self.assertTrue(heard["voice_detected"])
        self.assertEqual(heard["transcript"], "faint singing")


if __name__ == "__main__":
    unittest.main()
