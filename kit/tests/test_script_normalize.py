import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import script_writer  # noqa: E402

SHOTS = {"shots": [{"id": "S01", "speaker_on_screen": True, "rhythm": "panicked"},
                   {"id": "S02", "speaker_on_screen": True}]}


class NormalizeTest(unittest.TestCase):
    def test_speech_line_is_appended_with_character_as_speaker(self):
        script = {"scene": "x", "characters": [{"name": "Lead", "appearance": "a", "first_shot": "S02"},
                                               {"name": "Chlorine", "appearance": "b", "first_shot": "S01"}],
                  "shots": [{"id": "S01", "characters": ["Lead"], "visual_prompt": "A glass of water.",
                             "voiceover": "Do not test me!"},
                            {"id": "S02", "characters": ["Chlorine"],
                             "visual_prompt": "Chlorine says in English, in a whiny tone: 'Ouch!'",
                             "voiceover": "Ouch!"}]}
        out = script_writer.normalize(script, SHOTS)
        self.assertEqual(out["shots"][0]["visual_prompt"],
                         "A glass of water. Lead says in English, in a panicked tone: 'Do not test me!'")
        self.assertEqual(out["shots"][1]["visual_prompt"], script["shots"][1]["visual_prompt"])
        self.assertEqual({c["name"]: c["first_shot"] for c in out["characters"]},
                         {"Lead": "S01", "Chlorine": "S02"})

    def test_generic_capitalized_words_are_not_product_names(self):
        tokens = lambda text: [m.group(1) for m in script_writer.NAMED_PRODUCT.finditer(text)]
        self.assertTrue(tokens("PLEASE DO NOT BUY The product"))
        name = tokens("PLEASE DO NOT BUY The product")[0].lower().split()
        self.assertFalse([t for t in name if t not in script_writer.GENERIC_NAME_WORDS])


if __name__ == "__main__":
    unittest.main()
