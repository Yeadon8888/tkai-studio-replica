"""专家判定 与 人工判定 的一致率。只读，不改任何规则。

人工判定：calibration/human_decisions.jsonl，每行 {"take": "...", "project": "...", "decision": "accept|iterate|redo|...", ...}
专家判定：<project>/reviews/<take>.decision.json 中的 decision（pass / repair / escalate）
映射：人工 accept ↔ 专家 pass；iterate / redo ↔ repair；hold / abandon ↔ escalate。
"""
import argparse
import json
import sys
from pathlib import Path

MAP_HUMAN = {"accept": "pass", "release": "pass", "iterate": "repair", "redo": "repair",
             "hold": "escalate", "abandon": "escalate"}


def load_jsonl(p):
    rows = []
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def expert_decision(project_dir, take):
    p = Path(project_dir) / "reviews" / f"{take}.decision.json"
    if not p.exists():
        return None, None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("decision"), d


def main(argv=None):
    ap = argparse.ArgumentParser(description="专家与人工判定一致率")
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--projects", nargs="+", required=True, help="项目目录（含 reviews/）")
    a = ap.parse_args(argv)
    rows = load_jsonl(a.decisions)
    by_take = {}
    for r in rows:
        by_take[(r["project"], r["take"])] = r
    total = agree = 0
    diffs = []
    for proj in a.projects:
        name = Path(proj).name
        for (pn, take), h in by_take.items():
            if pn != name:
                continue
            exp, det = expert_decision(proj, take)
            if exp is None:
                diffs.append((pn, take, "未找到专家判定"))
                continue
            hm = MAP_HUMAN.get(h["decision"])
            total += 1
            if hm == exp:
                agree += 1
            else:
                diffs.append((pn, take, f"人工 {h['decision']}（→{hm}） 专家 {exp}：{(det or {}).get('reason', '')}"))
    rate = f"{agree}/{total}" if total else "0/0"
    print(f"一致 {rate}")
    for d in diffs:
        print("不一致/缺失：", d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
