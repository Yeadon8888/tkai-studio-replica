"""R8 高质量复刻流程；远程付费调用全部替换为假函数。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import compose  # noqa: E402
import dashboard  # noqa: E402
import gate  # noqa: E402
import intake  # noqa: E402
import review_panel  # noqa: E402
import shot_list  # noqa: E402
import shot_plan  # noqa: E402


def shots():
    return {"duration": 12, "candidate_id": "C023", "source_video_url": "https://example.test/ref.mp4",
            "summary": "结果悬念推动观看", "structure": [{"beat": "证明", "start": 0, "end": 12}], "shots": [
        {"id": "S01", "start": 0, "end": 4, "scale": "近景", "camera": "第一人称俯拍",
         "action": "拿起试纸瓶", "product_role": "subject", "hand_action": False,
         "onscreen_text": "old", "voiceover": "source line", "music_sfx": "beat", "rhythm": "开场",
         "hook_reason": "先给结果", "join": "cut", "keep": "keep", "redo_reason": "", "end_state": "手持瓶"},
        {"id": "S02", "start": 4, "end": 8, "scale": "特写", "camera": "固定俯拍",
         "action": "手把试纸浸入水中", "product_role": "supporting", "hand_action": True,
         "onscreen_text": "", "voiceover": "source line 2", "music_sfx": "dip", "rhythm": "动作",
         "hook_reason": "验证", "join": "continuous", "keep": "keep", "redo_reason": "", "end_state": "试纸在水中"},
        {"id": "S03", "start": 8, "end": 12, "scale": "特写", "camera": "轻微推进",
         "action": "把试纸放到色卡旁比对", "product_role": "subject", "hand_action": True,
         "onscreen_text": "", "voiceover": "source line 3", "music_sfx": "hit", "rhythm": "揭晓",
         "hook_reason": "结果", "join": "cut", "keep": "keep", "redo_reason": "", "end_state": "试纸在色卡旁"},
    ]}


class ShotListValidation(unittest.TestCase):
    def test_accepts_continuous_timeline(self):
        result = shot_list.validate(shots())
        self.assertEqual(result["shots"][-1]["end"], 12)

    def test_rejects_gap_and_overlap(self):
        for value in (4.2, 3.8):
            data = shots()
            data["shots"][1]["start"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "空隙|重叠"):
                shot_list.validate(data)

    def test_build_copies_benchmark_cover_into_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, batch = root / "run", root / "research" / "batch"
            run.mkdir(); (batch / "covers").mkdir(parents=True); (batch / "videos").mkdir()
            (batch / "candidates.json").write_text(json.dumps([{
                "candidate_id": "C023", "aweme_id": "123", "url": "https://example.test/video"
            }]), encoding="utf-8")
            (batch / "covers" / "123.jpg").write_bytes(b"cover")
            (batch / "videos" / "123.mp4").write_bytes(b"video")

            result = shot_list.build(
                run, batch, "C023",
                model_call=lambda *_args, **_kwargs: json.dumps(shots()),
                reachable=lambda _url: True,
            )

            self.assertEqual((run / "benchmark" / "cover.jpg").read_bytes(), b"cover")
            self.assertEqual((run / "benchmark" / "video.mp4").read_bytes(), b"video")
            self.assertEqual(result["benchmark_media"]["cover"]["status"], "copied")


class ShotPlanRoutes(unittest.TestCase):
    def test_groups_continuous_shots_and_uses_scene_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            refs = [root / f"r{i}.jpg" for i in range(2)]
            for path in refs:
                path.write_bytes(b"image")
            scene = root / "scene.png"
            scene.write_bytes(b"scene")
            script = {"scene": "A clean table", "shots": [
                {"id": f"S0{i}", "visual_prompt": "A hand moves the product", "voiceover": "Safe line",
                 "caption": "Safe line", "onscreen_copy": "", "end_state": "product held"}
                for i in range(1, 4)]}
            plan = shot_plan.make_plan(shots(), script, refs, scene_path=scene, seed=9)
        self.assertEqual([s["shot_ids"] for s in plan["segments"]], [["S01", "S02"], ["S03"]])
        self.assertEqual(plan["segments"][0]["images"][0], str(scene.resolve()))
        for segment in plan["segments"]:
            self.assertIn("do not render captions", segment["prompt"].lower())
            self.assertEqual(segment["seed"], 9)

    def test_single_long_shot_is_evenly_split_under_fifteen(self):
        data = shots()
        long_shot = dict(data["shots"][0], start=0, end=31)
        chunks = shot_plan.split_group([long_shot])
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(sum(x["duration"] for x in chunk) <= 15 for chunk in chunks))


class ComposeTimeline(unittest.TestCase):
    def test_subtitles_follow_segment_boundaries(self):
        brief = {"duration": 8, "segments": [
            {"duration": 3, "caption": "First line"}, {"duration": 5, "caption": "Second line"}]}
        rows = compose.timeline(brief)
        self.assertEqual([(r["start"], r["end"]) for r in rows], [(0.0, 3.0), (3.0, 8.0)])
        srt = compose.srt_text(rows)
        self.assertIn("00:00:03,000 --> 00:00:08,000", srt)

    def test_mismatched_parent_duration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不一致"):
            compose.timeline({"duration": 9, "segments": [{"duration": 4, "caption": "x"}]})

    def test_caption_overlay_wraps_long_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "caption.png"
            with mock.patch.object(compose.subprocess, "run"):
                compose.make_overlay(output, "Compare the colors on the bottle chart. This pack includes 125 strips.")
            rendered = output.with_suffix(".txt").read_text(encoding="utf-8")
        self.assertIn("\n", rendered)
        self.assertTrue(all(len(line) <= 34 for line in rendered.splitlines()))

    def test_composed_job_records_export_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for name in ("briefs", "takes", "jobs"):
                (project / name).mkdir()
            brief = project / "briefs" / "take.json"
            brief.write_text(json.dumps({"id": "take", "segments": [
                {"duration": 3, "shot_id": "S01", "caption": "line", "onscreen_copy": "copy"}
            ]}), encoding="utf-8")
            (project / "script.json").write_text(json.dumps({"scene": "table", "shots": [
                {"id": "S01", "voiceover": "line", "caption": "line", "onscreen_copy": "copy"}
            ]}), encoding="utf-8")
            (project / "takes" / "take.mp4").write_bytes(b"raw")
            (project / "jobs" / "take.json").write_text("{}", encoding="utf-8")

            def fake_compose(_project, _raw, _speech, _music, _rows, output):
                output.write_bytes(b"composed")
                return {"engine": "hypit"}

            with mock.patch.object(compose, "synthesize_speech", return_value=project / "speech.raw.wav"), \
                    mock.patch.object(compose, "fit_audio", return_value=project / "speech.wav"), \
                    mock.patch.object(compose, "make_music", return_value=project / "music.wav"), \
                    mock.patch.object(compose, "compose_hypit", side_effect=fake_compose):
                compose.build(project, brief)
            job = json.loads((project / "jobs" / "take.json").read_text(encoding="utf-8"))
        self.assertTrue(job["composed"])
        self.assertEqual(job["resolution"], "1080p")


class ReplicationExpert(unittest.TestCase):
    def test_prompt_and_request_contain_shot_plan_reference_then_final_video(self):
        expert = next(e for e in review_panel.load_experts()["experts"] if e["id"] == "R")
        brief = {"duration": 12, "segments": [{"shot_id": "S01", "duration": 4, "prompt": "overhead"}]}
        prompt = review_panel._expert_prompt(expert, brief, "anchor", "00:00")
        self.assertIn("structure_order", prompt)
        self.assertIn("S01", prompt)
        captured = {}

        def fake(parts, **kwargs):
            captured["parts"] = parts
            return json.dumps({"scores": {"replication_fidelity": 9},
                               "checks": {"replication_fidelity": {"structure_order": "通过"}},
                               "vetoes": [], "issues": []})

        with mock.patch.object(review_panel.loop, "gemini", side_effect=fake):
            review_panel.run_expert(expert, "run", "take", brief, "anchor", [],
                                    video_url="https://example.test/final.mp4", frames=[],
                                    reference_url="https://example.test/reference.mp4")
        videos = [part["video_url"] for part in captured["parts"] if part["type"] == "video_url"]
        self.assertEqual(videos, ["https://example.test/reference.mp4", "https://example.test/final.mp4"])

    def test_weights_sum_to_one_and_replication_has_fifteen_percent(self):
        dimensions = gate.load_rubric()["dimensions"]
        self.assertAlmostEqual(sum(row["weight"] for row in dimensions.values()), 1.0)
        self.assertEqual(dimensions["replication_fidelity"]["weight"], 0.15)

    def test_composed_take_uses_export_resolution_for_machine_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for name in ("briefs", "takes", "jobs", "reviews"):
                (project / name).mkdir()
            (project / "briefs" / "take.json").write_text(json.dumps({
                "id": "take", "duration": 3, "resolution": "768p", "product_anchor": "anchor"
            }), encoding="utf-8")
            (project / "takes" / "take.mp4").write_bytes(b"video")
            (project / "jobs" / "take.json").write_text(json.dumps({
                "composed": True, "resolution": "1080p"
            }), encoding="utf-8")
            seen = {}

            def fake_machine(_video, brief):
                seen.update(brief)
                return {"blocking": ["stop"], "info": [], "facts": {}}

            with mock.patch.object(review_panel, "machine_checks", side_effect=fake_machine):
                review_panel.review_take(project, "take", [])
        self.assertEqual(seen["resolution"], "1080p")


class BatchTierConfirmation(unittest.TestCase):
    def test_explicit_research_batch_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "candidates.json").write_text(json.dumps([{"candidate_id": "C023"}]), encoding="utf-8")
            row = intake.confirm_tier(None, "C023", "H", "用户确认 C023 为 TESPERT 试样 H 档对标", batch=batch)
            saved = json.loads((batch / "tier_overrides.json").read_text(encoding="utf-8"))
        self.assertEqual(row["tier"], "H")
        self.assertEqual(saved["C023"]["note"], "用户确认 C023 为 TESPERT 试样 H 档对标")


class ShotBoard(unittest.TestCase):
    def test_board_renders_shot_table_and_human_command(self):
        html = dashboard.render({"production": {"shot_list": shots(), "briefs": [], "series": [],
                                                 "stats": {}, "rule": {}, "grade_text": {}}}, "测试")
        self.assertIn("镜头表", html)
        self.assertIn("kit/human.py record", html)


if __name__ == "__main__":
    unittest.main()
