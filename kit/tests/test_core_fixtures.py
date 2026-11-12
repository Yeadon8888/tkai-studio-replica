import json
from pathlib import Path
ANCHOR = "a small clear vial of water test strips with a plain white label and NO text"


def good_script():
    return json.loads((Path(__file__).resolve().parents[2] / "methods/tiktok-script-generator/examples/X-sample-pool-strips.json").read_text(encoding="utf-8"))
