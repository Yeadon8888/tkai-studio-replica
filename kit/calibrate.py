"""专家判定与人工判定的一致率；只读，不改任何规则。

优先读取每个运行目录的 human_decisions.jsonl，同时兼容旧的
calibration/human_decisions.jsonl（可用 --decisions 指定）。同一成片只取最新一条。
"""
import argparse
import json
import sys
from pathlib import Path

MAP_HUMAN = {"accept": "pass", "release": "pass", "iterate": "repair", "redo": "repair",
             "hold": "escalate", "abandon": "escalate"}


def load_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def expert_decision(project_dir, take):
    p = Path(project_dir) / "reviews" / f"{take}.decision.json"
    if not p.exists():
        return None, None
    detail = json.loads(p.read_text(encoding="utf-8"))
    return detail.get("decision"), detail


def collect_decisions(projects, legacy_path=None):
    """旧集中账本先读，运行目录内的新记录后读并覆盖。"""
    latest = {}
    if legacy_path:
        for row in load_jsonl(legacy_path):
            latest[(row["project"], row["take"])] = row
    for project in projects:
        for row in load_jsonl(Path(project) / "human_decisions.jsonl"):
            latest[(row.get("project") or Path(project).name, row["take"])] = row
    return latest


def analyze(projects, legacy_path=None):
    by_take = collect_decisions(projects, legacy_path)
    result = {"total": 0, "agree": 0, "rate": None, "false_release": [], "false_block": [], "diffs": [],
              "essence_total": 0, "essence_agree": 0, "essence_rate": None, "essence_diffs": []}
    for project in projects:
        name = Path(project).name
        for (project_name, take), human in by_take.items():
            if project_name != name:
                continue
            expert, detail = expert_decision(project, take)
            if expert is None:
                result["diffs"].append({"project": name, "take": take, "reason": "未找到专家判定"})
                continue
            mapped = MAP_HUMAN.get(human.get("decision"))
            result["total"] += 1
            if mapped == expert:
                result["agree"] += 1
            else:
                sample = {"project": name, "take": take, "expert": expert,
                          "human": human.get("decision"), "expert_reason": (detail or {}).get("reason", "")}
                result["diffs"].append(sample)
                if expert == "pass" and mapped != "pass":
                    result["false_release"].append(sample)
                if expert == "repair" and mapped == "pass":
                    result["false_block"].append(sample)
    if result["total"]:
        result["rate"] = result["agree"] / result["total"]
    for project in projects:
        for human in load_jsonl(Path(project) / "essence_human.jsonl"):
            take = human.get("take")
            review_path = Path(project) / "reviews" / f"{human.get('review_id') or take}.json"
            review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
            decision = review.get("decision") or {}
            score = (review.get("essence") or {}).get("essence_score")
            threshold = decision.get("essence_min")
            if score is None or threshold is None:
                result["essence_diffs"].append({"project": Path(project).name, "take": take,
                                                "reason": "缺少 essence_score 或档位阈值"})
                continue
            ai = float(score) >= float(threshold)
            human_pass = human.get("reached") is True
            result["essence_total"] += 1
            if ai == human_pass:
                result["essence_agree"] += 1
            else:
                result["essence_diffs"].append({"project": Path(project).name, "take": take,
                                                "ai": ai, "human": human_pass, "score": score,
                                                "threshold": threshold})
    if result["essence_total"]:
        result["essence_rate"] = result["essence_agree"] / result["essence_total"]
    return result


def print_result(result, write=print):
    rate = f"{result['agree']}/{result['total']}（{result['rate']:.1%}）" if result["rate"] is not None else "0/0（样本不足）"
    write(f"一致率：{rate}")
    write(f"误放行：{len(result['false_release'])}")
    for row in result["false_release"]:
        write(f"  {row['project']} / {row['take']}：专家 {row['expert']}，人工 {row['human']}")
    write(f"误拦截：{len(result['false_block'])}")
    for row in result["false_block"]:
        write(f"  {row['project']} / {row['take']}：专家 {row['expert']}，人工 {row['human']}")
    for row in result["diffs"]:
        if "expert" not in row:
            write(f"缺失：{row['project']} / {row['take']}：{row['reason']}")
    essence_rate = (f"{result['essence_agree']}/{result['essence_total']}（{result['essence_rate']:.1%}）"
                    if result["essence_rate"] is not None else "0/0（样本不足）")
    write(f"神韵一致率：{essence_rate}")
    for row in result["essence_diffs"]:
        detail = row.get("reason") or f"机器 {row['ai']}，人工 {row['human']}"
        write(f"  {row['project']} / {row['take']}：{detail}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="专家与人工判定一致率")
    ap.add_argument("--decisions", help="旧集中人工判定 JSONL；不传则只读各运行目录")
    ap.add_argument("--projects", nargs="+", required=True, help="项目目录（含 reviews/）")
    args = ap.parse_args(argv)
    print_result(analyze(args.projects, args.decisions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
