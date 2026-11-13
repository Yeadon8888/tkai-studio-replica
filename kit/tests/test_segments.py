"""多段生成、拼接与定点修订的回归测试。"""
import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import loop  # noqa: E402
import refine_loop  # noqa: E402
import review_panel  # noqa: E402


class SegmentGeneration(unittest.TestCase):
    def _project(self, root):
        project = Path(root) / "run"
        (project / "briefs").mkdir(parents=True)
        brief = {"id": "T1", "segments": [
            {"prompt": "first", "duration": 10, "images": []},
            {"prompt": "second", "duration": 10, "images": []},
        ]}
        brief_path = project / "briefs/T1.json"
        brief_path.write_text(json.dumps(brief), encoding="utf-8")
        args = argparse.Namespace(project=str(project), brief=str(brief_path), cap=2,
                                  interval=0, max_polls=1, no_review=True)
        return project, args

    def test_generates_each_segment_then_concats(self):
        with tempfile.TemporaryDirectory() as root:
            project, args = self._project(root)
            generated = []

            def fake_generate(child_args):
                child = json.loads(Path(child_args.brief).read_text(encoding="utf-8"))
                generated.append(child["id"])
                (project / "takes").mkdir(exist_ok=True)
                (project / "takes" / f"{child['id']}.mp4").write_bytes(b"segment")

            def fake_concat(videos, output):
                self.assertEqual([p.name for p in videos], ["T1-s1.mp4", "T1-s2.mp4"])
                Path(output).write_bytes(b"joined")

            with mock.patch.object(loop, "_generate_single", side_effect=fake_generate), \
                 mock.patch.object(loop, "concat_segments", side_effect=fake_concat) as concat:
                loop.cmd_gen(args)
            self.assertEqual(generated, ["T1-s1", "T1-s2"])
            concat.assert_called_once()
            self.assertTrue((project / "takes/T1.mp4").exists())

    def test_segment_failure_stops_without_concat(self):
        with tempfile.TemporaryDirectory() as root:
            project, args = self._project(root)

            def fake_generate(child_args):
                child = json.loads(Path(child_args.brief).read_text(encoding="utf-8"))
                if child["id"].endswith("s1"):
                    (project / "takes").mkdir(exist_ok=True)
                    (project / "takes" / f"{child['id']}.mp4").write_bytes(b"kept")

            with mock.patch.object(loop, "_generate_single", side_effect=fake_generate), \
                 mock.patch.object(loop, "concat_segments") as concat:
                loop.cmd_gen(args)
            concat.assert_not_called()
            self.assertTrue((project / "takes/T1-s1.mp4").exists())
            self.assertFalse((project / "takes/T1.mp4").exists())

    def test_concat_failure_removes_old_parent(self):
        with tempfile.TemporaryDirectory() as root:
            project, args = self._project(root)
            (project / "takes").mkdir()
            (project / "takes/T1.mp4").write_bytes(b"old")
            (project / "takes/T1-s1.mp4").write_bytes(b"one")
            (project / "takes/T1-s2.mp4").write_bytes(b"two")
            with mock.patch.object(loop, "concat_segments", side_effect=subprocess.CalledProcessError(1, "ffmpeg")), \
                 self.assertRaises(SystemExit):
                loop.cmd_gen(args)
            self.assertFalse((project / "takes/T1.mp4").exists())

    def test_concat_normalizes_video_and_audio(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first, second, output = root / "a.mp4", root / "b.mp4", root / "joined.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=160x284:rate=12", "-t", "0.5", "-pix_fmt", "yuv420p", str(first)],
                           check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=180x320:rate=24", "-f", "lavfi", "-i", "sine=frequency=440",
                            "-t", "0.5", "-pix_fmt", "yuv420p", str(second)], check=True)
            loop.concat_segments([first, second], output)
            probe = json.loads(subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
            self.assertTrue(any(s.get("codec_type") == "audio" for s in probe["streams"]))
            video = next(s for s in probe["streams"] if s.get("codec_type") == "video")
            self.assertEqual((video["width"], video["height"]), (160, 284))
            self.assertAlmostEqual(float(probe["format"]["duration"]), 1.0, delta=0.2)


class SegmentReview(unittest.TestCase):
    def test_machine_duration_is_sum_of_segments(self):
        brief = {"segments": [{"duration": 10}, {"duration": 10}]}
        with mock.patch.object(review_panel, "_probe", return_value={
            "streams": [{"codec_type": "video", "width": 768, "height": 1366, "codec_name": "h264"}],
            "format": {"duration": "20.0"},
        }), tempfile.NamedTemporaryFile() as video:
            Path(video.name).write_bytes(b"x" * 50_001)
            result = review_panel.machine_checks(video.name, brief)
        self.assertFalse(any("时长" in x for x in result["blocking"]))


class SegmentRefinement(unittest.TestCase):
    def test_only_regenerates_segment_hit_by_timecode(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "run"
            (project / "briefs").mkdir(parents=True)
            generated = []

            def gen(proj, brief_path):
                brief = json.loads(Path(brief_path).read_text(encoding="utf-8"))
                generated.append([i + 1 for i, s in enumerate(brief["segments"]) if not s.get("reuse_from")])
                (proj / "takes").mkdir(exist_ok=True)
                (proj / "takes" / f"{brief['id']}.mp4").write_bytes(b"joined")

            calls = 0

            def review(proj, take, refs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    (proj / "reviews").mkdir(exist_ok=True)
                    record = {"experts": [{"judge": {"id": "visual"}, "issues": [
                        {"cause": "motion_artifact", "at": "00:12–00:14", "detail": "第二段动作变形"}
                    ]}]}
                    (proj / "reviews" / f"{take}.json").write_text(json.dumps(record), encoding="utf-8")
                    return {"decision": "repair", "weighted": 6, "repair": []}
                return {"decision": "pass", "weighted": 9}

            base = {"id": "B", "segments": [
                {"prompt": "anchor first", "duration": 10},
                {"prompt": "anchor second", "duration": 10},
            ]}
            llm = mock.Mock(return_value=json.dumps({"prompt": "anchor second fixed"}))
            with mock.patch.object(refine_loop.products, "can_spend", return_value=(True, {})):
                result = refine_loop.run(base, project, "p", [], gen, review, llm, "anchor", max_attempts=2)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(generated, [[1, 2], [2]])
            self.assertEqual(llm.call_count, 1)
            second = json.loads((project / "briefs/B-a2.json").read_text(encoding="utf-8"))
            self.assertEqual(second["segments"][0]["reuse_from"], "B-a1-s1")
            self.assertNotIn("reuse_from", second["segments"][1])


if __name__ == "__main__":
    unittest.main()
