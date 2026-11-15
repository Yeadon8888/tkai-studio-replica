"""R16 形式不变量、付费前预估与合成覆盖测试。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import compose  # noqa: E402
import essence  # noqa: E402
import format_invariants  # noqa: E402
import platform_runner  # noqa: E402
import script_writer  # noqa: E402


def c023():
    return {"candidate_id": "C023", "tier": "H", "duration": 17.5,
            "audio_mode": "music_only",
            "structure": [{"beat": "钩子", "start": 0, "end": 11.5},
                          {"beat": "证明", "start": 11.5, "end": 17.5}],
            "shots": [
                {"id": "S01", "start": 0, "end": 11.5, "keep": "keep", "join": "cut",
                 "scale": "特写", "camera": "俯拍", "action": "浸入试纸",
                 "onscreen_text": "Reply to real_user's comment water safer than the ish we drink now",
                 "voiceover": "", "music_sfx": "old song"},
                {"id": "S02", "start": 11.5, "end": 17.5, "keep": "redo", "join": "cut",
                 "scale": "特写", "camera": "平视", "action": "对照色卡",
                 "onscreen_text": "MRSFALLOUT", "voiceover": "", "music_sfx": "old song"},
            ]}


def valid_script():
    music = {"mood": "curious", "tempo_bpm": 72, "era_style": "vintage",
             "instruments": ["piano"], "energy_curve": "steady"}
    return {"scene": "A dim tabletop", "music": music, "shots": [
        {"id": "S01", "visual_prompt": "A hand dips testing material into cloudy water",
         "voiceover": "", "caption": "",
         "onscreen_copy": {"style": "reply_sticker", "handle": "@curious_guest",
                           "comment": "Would this emergency water pass a test?"},
         "end_state": "Testing material stays above the glass"},
        {"id": "S02", "visual_prompt": "A hand compares testing material with a color chart",
         "voiceover": "", "caption": "", "onscreen_copy": "",
         "end_state": "Testing material stays beside the chart"},
    ]}


FACTS = {"product_state": {"name": "Exact Product"},
         "product_anchor": "Exact Product bottle", "confirmed_selling_points": []}


class FormatDerivationTest(unittest.TestCase):
    def test_c023_fixture_has_no_captions_reply_sticker_and_no_cta(self):
        result = format_invariants.derive(c023())
        self.assertFalse(result["captions"])
        self.assertFalse(result["voice"])
        self.assertEqual(result["onscreen_style"], "reply_sticker")
        self.assertEqual(result["reply_sticker_shots"], ["S01"])
        self.assertFalse(result["cta"])
        self.assertEqual(result["product_mentions"], 0)
        self.assertIn("对标无字幕", result["description"])

    def test_other_formats_voice_cta_and_named_product_are_derived(self):
        data = c023()
        data.update(audio_mode="voiceover", reference_product_name="Acme Kit")
        data["shots"][0].update(caption="Spoken caption", onscreen_text="Buy Acme Kit today")
        result = format_invariants.derive(data)
        self.assertTrue(result["captions"])
        self.assertTrue(result["voice"])
        self.assertEqual(result["onscreen_style"], "text_overlay")
        self.assertTrue(result["cta"])
        self.assertEqual(result["product_mentions"], 1)


class ScriptInvariantTest(unittest.TestCase):
    def errors(self, value):
        return script_writer.validate(value, c023(), FACTS, [], format_invariants.derive(c023()))

    def test_valid_reply_sticker_script_passes(self):
        self.assertEqual(self.errors(valid_script()), [])

    def test_each_format_violation_blocks(self):
        cases = []
        value = valid_script(); value["shots"][0]["caption"] = "Always on captions"
        cases.append((value, "字幕有无"))
        value = valid_script(); value["shots"][0]["onscreen_copy"]["comment"] = "Click to order today"
        cases.append((value, "不得新增行动号召"))
        value = valid_script(); value["shots"][0]["onscreen_copy"]["comment"] = "Exact Product beats Exact Product"
        cases.append((value, "品名出现"))
        value = valid_script(); value["shots"][0]["onscreen_copy"] = "ordinary string"
        cases.append((value, "reply_sticker 对象"))
        for value, message in cases:
            with self.subTest(message=message):
                self.assertTrue(any(message in error for error in self.errors(value)), self.errors(value))

    def test_real_handle_and_copied_comment_block(self):
        value = valid_script(); value["shots"][0]["onscreen_copy"]["handle"] = "@real_user"
        self.assertTrue(any("真实用户名" in error for error in self.errors(value)))
        value = valid_script()
        value["shots"][0]["onscreen_copy"]["comment"] = "water safer than the ish we drink now"
        self.assertTrue(any("重合率超过 60%" in error for error in self.errors(value)))


class ComposeInvariantTest(unittest.TestCase):
    def test_no_caption_timeline_does_not_fail_or_emit_srt(self):
        rows = compose.timeline({"duration": 4, "captions": False, "segments": [
            {"duration": 4, "voiceover_line": "Spoken but not captioned"}]})
        self.assertEqual(rows[0]["caption"], "")
        self.assertEqual(compose.srt_text(rows), "")

    def test_reply_sticker_window_is_exact_in_both_routes(self):
        sticker = {"style": "reply_sticker", "handle": "@guest", "comment": "Safe to drink?"}
        rows = [{"start": 1.25, "end": 3.75, "caption": "", "onscreen_copy": sticker}]
        source = compose._hypit_source(rows, 4, [])
        self.assertIn('id="copy-1" from="1.25s" for="2.5s"', source)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(compose, "make_overlay", return_value=Path(tmp) / "sticker.png"):
            _, filters, label = compose._video_layers(rows, tmp, 3)
        self.assertEqual(label, "v1")
        self.assertTrue(any("between(t,1.250,3.750)" in value for value in filters))


class PreviewParityTest(unittest.TestCase):
    def test_preview_and_formal_format_items_share_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "shots.json").write_text(json.dumps(c023()), encoding="utf-8")
            (project / "script.json").write_text(json.dumps(valid_script()), encoding="utf-8")
            (project / "product_state.json").write_text(json.dumps(FACTS["product_state"]), encoding="utf-8")
            plan = {"duration": 17.5, "structure": c023()["structure"]}
            preview = essence.preview(project, plan)
            data = essence.build_items(c023(), plan)
            formal = essence.code_results(data, c023(), "unused", plan, cuts=[11.5], duration=17.5,
                                          script=valid_script(), format_data=format_invariants.derive(c023()),
                                          facts=FACTS)
        formal_status = {row["id"]: row["status"] for row in formal if row["id"].startswith("code-format-")}
        self.assertEqual({row["id"]: row["status"] for row in preview["items"]}, formal_status)
        self.assertEqual(preview["score"], 1.0)

    def test_empty_structure_has_explicit_explanation(self):
        data = c023(); data["structure"] = []
        essence_data = essence.build_items(data)
        item = next(row for row in essence_data["items"] if row["id"] == "code-narrative-beats")
        self.assertEqual(item["what"], "对标镜头表缺少节拍结构")
        result = essence.code_results(essence_data, data, "unused", cuts=[11.5], duration=17.5)
        scored = next(row for row in result if row["id"] == "code-narrative-beats")
        self.assertEqual(scored["detail"], "对标镜头表缺少节拍结构")


class PlatformPayloadTest(unittest.TestCase):
    def test_optional_format_and_preview_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "shots.json").write_text(json.dumps(c023()), encoding="utf-8")
            (run / "script.json").write_text(json.dumps(valid_script()), encoding="utf-8")
            (run / "format.json").write_text(json.dumps({"description": "对标无字幕；无 CTA"}), encoding="utf-8")
            (run / "essence_preview.json").write_text(json.dumps({"score": 1.0, "items": [
                {"label": "字幕有无", "status": "pass", "detail": "一致"}]}), encoding="utf-8")
            payload = platform_runner._script_payload(run)
        self.assertEqual(payload["format"], "对标无字幕；无 CTA")
        self.assertEqual(payload["essencePreview"]["items"][0]["label"], "字幕有无")


if __name__ == "__main__":
    unittest.main()


class EngineFormatRewriteTest(unittest.TestCase):
    def test_format_errors_flag_legacy_ad_style_script(self):
        import script_writer
        shots = {"audio_mode": "music_only", "shots": [
            {"id": "S01", "start": 0, "end": 5, "keep": "keep", "caption": None, "voiceover": "",
             "onscreen_text": "Reply to user1's comment is this safe"}]}
        legacy = {"scene": "x", "shots": [{"id": "S01", "voiceover": "", "caption": "Testing now",
                  "onscreen_copy": "Check your water today"}]}
        errors = script_writer.format_errors(legacy, shots)
        self.assertIn("字幕有无必须与对标一致", errors)
        self.assertTrue(any("CTA" in e for e in errors))
        self.assertTrue(any("屏幕文字形式" in e for e in errors))
        good = {"scene": "x", "shots": [{"id": "S01", "voiceover": "", "caption": "",
                "onscreen_copy": {"style": "reply_sticker", "handle": "@sipper_01", "comment": "really?"}}]}
        self.assertEqual(script_writer.format_errors(good, shots), [])
