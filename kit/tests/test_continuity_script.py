"""R9 连续性脚本、分组、预算与接力的离线测试。"""
import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import keyframe  # noqa: E402
import loop  # noqa: E402
import script_writer  # noqa: E402
import shot_list  # noqa: E402
import shot_plan  # noqa: E402


def shot(sid, start, end, join="cut", voice="source words"):
    return {"id": sid, "start": start, "end": end, "scale": "close", "camera": "fixed",
            "action": "move product", "product_role": "subject", "hand_action": True,
            "onscreen_text": "", "voiceover": voice, "music_sfx": "", "rhythm": "steady",
            "hook_reason": "proof", "join": join, "keep": "keep", "redo_reason": "",
            "end_state": "the product is centered"}


def shots(rows):
    duration = rows[-1]["end"]
    return {"duration": duration, "summary": "proof", "candidate_id": "C1",
            "structure": [{"beat": "证明", "start": 0, "end": duration}], "shots": rows}


def script_for(rows):
    return {"scene": "A bright kitchen table", "shots": [
        {"id": row["id"], "visual_prompt": "A hand moves the product naturally",
         "voiceover": "Test 16 parameters" if row["voiceover"] else "",
         "caption": "Test 16 parameters" if row["voiceover"] else "",
         "onscreen_copy": "16 parameters" if row["voiceover"] else "",
         "end_state": "The product is centered"} for row in rows]}


class ShotFields(unittest.TestCase):
    def test_new_fields_and_values_are_blocking(self):
        data = shots([shot("S01", 0, 4)])
        self.assertEqual(shot_list.validate(data)["shots"][0]["join"], "cut")
        for field, value, message in (("join", "fade", "join 非法"), ("keep", "maybe", "keep 非法")):
            bad = shots([shot("S01", 0, 4)])
            bad["shots"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                shot_list.validate(bad)
        bad = shots([shot("S01", 0, 4, join="continuous")])
        with self.assertRaisesRegex(ValueError, "第一个镜头 join 必须是 cut"):
            shot_list.validate(bad)


class ScriptBlockingValidation(unittest.TestCase):
    def setUp(self):
        self.source = shots([shot("S01", 0, 4)])
        self.facts = {"product_anchor": "TESPERT 16-in-1 test strips",
                      "confirmed_selling_points": ["16 parameters"]}
        self.valid = script_for(self.source["shots"])

    def test_four_blocking_rules(self):
        self.assertEqual(script_writer.validate(self.valid, self.source, self.facts, ["RivalCo"]), [])
        cases = []
        value = json.loads(json.dumps(self.valid)); value["shots"][0]["voiceover"] = "Test 17 parameters"
        cases.append((value, "没有产品事实依据"))
        value = json.loads(json.dumps(self.valid)); value["shots"][0]["caption"] = "RivalCo is better"
        cases.append((value, "竞品品牌词"))
        value = json.loads(json.dumps(self.valid)); value["shots"][0]["voiceover"] = "one two three four five six seven eight nine ten eleven"
        value["shots"][0]["caption"] = value["shots"][0]["voiceover"]
        value["shots"][0]["onscreen_copy"] = ""
        cases.append((value, "超过上限"))
        value = json.loads(json.dumps(self.valid)); value["shots"][0]["visual_prompt"] = "Render a logo on screen"
        cases.append((value, "要求渲染文字"))
        value = json.loads(json.dumps(self.valid)); value["shots"][0]["caption"] = "Use Acme Bottle"
        cases.append((value, "产品称呼不在产品锚点"))
        for value, message in cases:
            with self.subTest(message=message):
                self.assertTrue(any(message in error for error in script_writer.validate(
                    value, self.source, self.facts, ["RivalCo"])))

    def test_voiceover_presence_count_is_blocking(self):
        self.valid["shots"][0]["voiceover"] = ""
        self.assertTrue(any("非空口播段数" in x for x in script_writer.validate(
            self.valid, self.source, self.facts)))

    def test_rewrites_once_then_writes_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "shots.json").write_text(json.dumps(self.source), encoding="utf-8")
            invalid = json.loads(json.dumps(self.valid))
            invalid["shots"][0]["voiceover"] = "Test 99 parameters"
            outputs = iter((json.dumps(invalid), json.dumps(self.valid)))
            calls = []

            def fake(parts, **kwargs):
                calls.append((parts, kwargs))
                return next(outputs)

            result = script_writer.build(project, ["16 parameters"], model_call=fake, terms=[])
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["generation"]["rewrite_count"], 1)
            self.assertTrue(result["generation"]["validation"][-1]["ok"])
            self.assertTrue((project / "script.json").is_file())


class ContinuityPlan(unittest.TestCase):
    def _assets(self, root):
        ref, scene = root / "product.png", root / "scene.png"
        ref.write_bytes(b"ref")
        scene.write_bytes(b"scene")
        return ref, scene

    def test_cut_continuous_match_cut_and_price(self):
        rows = [shot("S01", 0, 6), shot("S02", 6, 12, "continuous"),
                shot("S03", 12, 18, "match_cut")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ref, scene = self._assets(root)
            plan = shot_plan.make_plan(shots(rows), script_for(rows), [ref], scene_path=scene, seed=17)
        self.assertEqual([x["shot_ids"] for x in plan["segments"]], [["S01", "S02"], ["S03"]])
        self.assertEqual([x["video_route"] for x in plan["segments"]], ["quality", "keyframe"])
        self.assertEqual(plan["segments"][1]["first_frame"], "chain:prev")
        self.assertTrue(all(x["seed"] == 17 for x in plan["segments"]))
        self.assertAlmostEqual(plan["estimated_cny"], 12 * 0.23 + 6 * 0.13)

    def test_over_fifteen_splits_and_chains(self):
        rows = [shot("S01", 0, 10), shot("S02", 10, 20, "continuous")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ref, scene = self._assets(root)
            plan = shot_plan.make_plan(shots(rows), script_for(rows), [ref], scene_path=scene, seed=1)
        self.assertEqual([x["duration"] for x in plan["segments"]], [10, 10])
        self.assertEqual(plan["segments"][1]["chain_index"], 2)
        self.assertEqual(plan["segments"][1]["last_frame"], "chain:end")

    def test_single_long_shot_splits_voiceover_without_duplication(self):
        rows = [shot("S01", 0, 20)]
        script = script_for(rows)
        script["shots"][0]["voiceover"] = "one two three four five six"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ref, scene = self._assets(root)
            plan = shot_plan.make_plan(shots(rows), script, [ref], scene_path=scene, seed=1)
        words = " ".join(x["voiceover_line"] for x in plan["segments"]).split()
        self.assertEqual(words, ["one", "two", "three", "four", "five", "six"])
        self.assertIn("Natural intermediate state", plan["segments"][0]["end_state"])

    def test_scene_is_first_quality_image_and_missing_product_blocks(self):
        rows = [shot("S01", 0, 5)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ref, scene = self._assets(root)
            plan = shot_plan.make_plan(shots(rows), script_for(rows), [ref], scene_path=scene, seed=1)
            self.assertEqual(plan["segments"][0]["images"][0], str(scene.resolve()))
            with self.assertRaisesRegex(ValueError, "真实产品图"):
                shot_plan.make_plan(shots(rows), script_for(rows), [], scene_path=scene)

    def test_seed_reaches_quality_and_keyframe_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "frame.png"; image.write_bytes(b"frame")
            _, quality = loop.build_video_request({"video_route": "quality", "prompt": "move",
                                                    "images": [str(image)], "duration": 5,
                                                    "resolution": "960p", "aspect_ratio": "9:16", "seed": 7})
            _, chained = loop.build_video_request({"video_route": "keyframe", "prompt": "move",
                                                    "first_frame": str(image), "last_frame": str(image),
                                                    "duration": 5, "resolution": "768p",
                                                    "aspect_ratio": "9:16", "seed": 7})
        self.assertEqual((quality["seed"], chained["seed"]), (7, 7))


class ChainGeneration(unittest.TestCase):
    def _setup(self, root):
        project = Path(root) / "run"
        (project / "briefs").mkdir(parents=True)
        brief = {"id": "T", "segments": [
            {"prompt": "first", "duration": 5, "images": []},
            {"prompt": "second", "duration": 5, "video_route": "keyframe", "resolution": "768p",
             "first_frame": "chain:prev", "last_frame": "chain:end", "end_state": "product centered"}]}
        path = project / "briefs/T.json"
        path.write_text(json.dumps(brief), encoding="utf-8")
        args = argparse.Namespace(project=str(project), brief=str(path), cap=2,
                                  interval=0, max_polls=1, no_review=True)
        return project, args

    def test_chain_waits_for_previous_and_records_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, args = self._setup(tmp)
            generated = []

            def fake_generate(child_args):
                child = json.loads(Path(child_args.brief).read_text(encoding="utf-8"))
                generated.append(child)
                (project / "takes").mkdir(exist_ok=True)
                (project / "takes" / f"{child['id']}.mp4").write_bytes(b"video")

            def fake_extract(_video, output):
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_bytes(b"frame")
                return Path(output)

            def fake_last(_project, sid, _first, _state, _aspect):
                out = project / "chain" / f"{sid}-last.png"
                out.write_bytes(b"last")
                return out

            with mock.patch.object(loop, "_generate_single", side_effect=fake_generate), \
                 mock.patch.object(loop, "extract_last_frame", side_effect=fake_extract), \
                 mock.patch.object(keyframe, "generate_chain_last_frame", side_effect=fake_last), \
                 mock.patch.object(loop, "concat_segments", side_effect=lambda videos, out: Path(out).write_bytes(b"joined")):
                loop.cmd_gen(args)
            self.assertEqual([x["id"] for x in generated], ["T-s1", "T-s2"])
            self.assertIn("chain_source", generated[1])
            self.assertTrue((project / "chain/T-s2-first.png").is_file())

    def test_previous_failure_never_submits_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, args = self._setup(tmp)
            calls = []
            with mock.patch.object(loop, "_generate_single", side_effect=lambda a: calls.append(a.brief)), \
                 mock.patch.object(loop, "concat_segments") as concat:
                loop.cmd_gen(args)
            self.assertEqual(len(calls), 1)
            concat.assert_not_called()
            blocked = json.loads((project / "jobs/T-s2.json").read_text(encoding="utf-8"))
            self.assertEqual(blocked["status"], "blocked")
            self.assertIn("未提交", blocked["reason"])


class SceneKeyframe(unittest.TestCase):
    def test_scene_uses_script_and_product_without_real_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product.png"; product.write_bytes(b"x")
            (root / "script.json").write_text(json.dumps({"scene": "A stable kitchen"}), encoding="utf-8")
            with mock.patch.object(keyframe, "normalize_aspect"), \
                 mock.patch.object(keyframe, "_append_image_ledger"), \
                 mock.patch.object(keyframe.settings, "get", return_value="9:16"):
                path = keyframe.generate_scene(root, product, edit=lambda ref, prompt, size: b"png")
            self.assertEqual(path, (root / "scene.png").resolve())


if __name__ == "__main__":
    unittest.main()
