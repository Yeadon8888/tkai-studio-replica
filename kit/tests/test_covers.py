"""封面本地化：过期判断、本地视频截帧、看板内嵌、刷新只在允许时调用。"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import covers  # noqa: E402


class Covers(unittest.TestCase):
    def test_expired_reads_x_expires(self):
        past, future = int(time.time()) - 10, int(time.time()) + 3600
        self.assertTrue(covers.expired(f"https://p16.tiktokcdn.com/a.jpg?x-expires={past}&x=1"))
        self.assertFalse(covers.expired(f"https://p16.tiktokcdn.com/a.jpg?x-expires={future}"))
        self.assertFalse(covers.expired("https://example.com/a.jpg"))

    def test_local_video_gets_frame_cover_and_board_embeds_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            video = tmp / "v.mp4"
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x568:d=2",
                                "-pix_fmt", "yuv420p", str(video)], capture_output=True)
            if r.returncode != 0:
                self.skipTest("没有 ffmpeg")
            cand = {"aweme_id": "file-abc", "local_file": str(video)}
            self.assertEqual(covers.cache(tmp, cand), "frame")
            self.assertEqual(cand["cover_file"], "covers/file-abc.jpg")
            self.assertTrue(covers.data_uri(tmp, cand).startswith("data:image/jpeg;base64,"))
            self.assertEqual(covers.cache(tmp, cand), "exists")

    def test_expired_link_is_not_refreshed_unless_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = {"aweme_id": "1", "url": "https://www.tiktok.com/@a/video/1",
                    "cover_url": f"https://p16.tiktokcdn.com/a.jpg?x-expires={int(time.time()) - 10}"}
            with mock.patch.object(covers, "refresh_cover_url") as refresh:
                self.assertEqual(covers.cache(tmp, dict(cand), refresh=False), "expired")
                refresh.assert_not_called()
            with mock.patch.object(covers, "refresh_cover_url", return_value="https://new/cover.jpg") as refresh, \
                 mock.patch.object(covers, "download", return_value=False):
                self.assertEqual(covers.cache(tmp, dict(cand), refresh=True), "failed")
                refresh.assert_called_once()

    def test_research_build_prefers_local_cover(self):
        import research
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "covers").mkdir()
            (tmp / "covers" / "9.jpg").write_bytes(b"\xff\xd8\xff\xd9")
            cands = [{"aweme_id": "9", "candidate_id": "C1", "hard_pass": True, "topic_level": "strong",
                      "final_pass": True, "cover_url": "https://expired", "cover_file": "covers/9.jpg"}]
            (tmp / "candidates.json").write_text(json.dumps(cands), encoding="utf-8")
            case = research.build(tmp)["cases"][0]
            self.assertTrue(case["cover"].startswith("data:image/jpeg;base64,"))
            self.assertTrue(case["cover_local"])


if __name__ == "__main__":
    unittest.main()
