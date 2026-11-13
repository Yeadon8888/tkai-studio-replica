"""产品首帧路线。付费接口全部用假函数，不发起网络请求。"""
import json
import struct
import sys
import tempfile
import unittest
import urllib.error
import zlib
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import keyframe  # noqa: E402
import settings  # noqa: E402


def png(width, height):
    """用标准库生成一张 RGB PNG。"""
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    rows = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class KeyframePromptTest(unittest.TestCase):
    def test_prefers_explicit_keyframe_prompt(self):
        brief = {"keyframe": {"prompt": "人物在泳池边展示试纸瓶"}, "prompt": "0-3 seconds: ignored"}
        self.assertEqual(keyframe.keyframe_prompt(brief), "人物在泳池边展示试纸瓶")

    def test_extracts_first_three_seconds(self):
        brief = {"prompt": "Style setup. 0-3 seconds: hold the exact vial near camera. 3-7 seconds: dip a strip."}
        self.assertEqual(keyframe.keyframe_prompt(brief),
                         "Style setup. 0-3 seconds: hold the exact vial near camera")

    def test_image_settings_support_model_and_optional_price(self):
        values = {"MODEL_IMAGE": "image-test", "IMAGE_PRICE_CNY": ""}
        with mock.patch.object(settings, "get", side_effect=values.get):
            self.assertEqual(settings.model("image"), "image-test")
            self.assertIsNone(settings.image_price())
        values["IMAGE_PRICE_CNY"] = "0.06"
        with mock.patch.object(settings, "get", side_effect=values.get):
            self.assertEqual(settings.image_price(), 0.06)


class KeyframeBuildTest(unittest.TestCase):
    def _brief(self, root):
        brief = {
            "id": "F01-r2", "title": "test", "images": [str(root / "product.png")],
            "duration": 10, "resolution": "768p", "aspect_ratio": "9:16",
            "prompt": "0-3 seconds: hold the vial toward camera. 3-7 seconds: dip one strip.",
        }
        path = root / "source.json"
        path.write_text(json.dumps(brief), encoding="utf-8")
        return path

    def test_build_writes_keyframe_brief_and_one_image_ledger_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "run"
            product = root / "product.png"
            product.write_bytes(png(64, 64))
            brief_path = self._brief(root)
            ledger_file = root / "costs.jsonl"
            calls = []

            def fake_edit(reference, prompt, size):
                calls.append((reference, prompt, size))
                return png(90, 160)

            with mock.patch.object(keyframe.ledger, "LEDGER_DIR", root), \
                 mock.patch.object(keyframe.ledger, "LEDGER_FILE", ledger_file), \
                 mock.patch.object(keyframe.settings, "image_price", return_value=None):
                new_brief_path, image_path = keyframe.build(project, brief_path, product, edit=fake_edit)

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2], "1024x1536")
            self.assertIn("严格保留形状、颜色、材质、比例和标签", calls[0][1])
            self.assertEqual(keyframe._dimensions(image_path), (90, 160))
            new_brief = json.loads(new_brief_path.read_text(encoding="utf-8"))
            self.assertEqual(new_brief["id"], "F01-r2-kf")
            self.assertEqual(new_brief["parent"], "F01-r2")
            self.assertEqual(new_brief["images"], [str(image_path.resolve()), str(product.resolve())])
            self.assertTrue(new_brief["prompt"].startswith("图 1 是首帧，图 2 是产品参考。"))
            rows = [json.loads(line) for line in ledger_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["kind"], rows[0]["status"], rows[0]["estimate_cny"]), ("image", "ok", None))

    def test_failure_is_logged_once_without_output_or_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "run"
            product = root / "product.png"
            product.write_bytes(png(64, 64))
            brief_path = self._brief(root)
            ledger_file = root / "costs.jsonl"
            calls = []

            error = urllib.error.HTTPError("https://example.invalid", 502, "upstream", {}, None)

            def failed(*args):
                calls.append(args)
                raise error

            with mock.patch.object(keyframe.ledger, "LEDGER_DIR", root), \
                 mock.patch.object(keyframe.ledger, "LEDGER_FILE", ledger_file), \
                 mock.patch.object(keyframe.settings, "image_price", return_value=0.25):
                with self.assertRaises(urllib.error.HTTPError):
                    keyframe.build(project, brief_path, product, edit=failed)
            error.close()

            self.assertEqual(len(calls), 1)
            self.assertFalse((project / "briefs/F01-r2-kf.json").exists())
            self.assertFalse((project / "keyframes/F01-r2-kf.png").exists())
            rows = [json.loads(line) for line in ledger_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["status"], rows[0]["estimate_cny"]), ("502", 0.25))


if __name__ == "__main__":
    unittest.main()
