"""记录和查看视频人工判定。"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import schema

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "video_hitl_decision.schema.json"
VERDICTS = ("accept", "iterate", "redo", "hold", "abandon", "release")


def load(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_by_take(path):
    latest = {}
    for row in load(path):
        latest[row["take"]] = row
    return latest


def expert_decision(project, take):
    path = Path(project) / "reviews" / f"{take}.decision.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("decision")


def record(project, take, verdict, note, reviewer="未署名", now=None):
    if len(note.strip()) < 8:
        raise ValueError("理由至少 8 个字符，以满足 schema")
    project_dir = Path(project)
    if not project_dir.name:
        raise ValueError("运行目录必须有名称")
    if not take.strip():
        raise ValueError("成片 id 不能为空")
    if not reviewer.strip():
        raise ValueError("判定人不能为空")
    decided = now or dt.datetime.now(dt.timezone.utc)
    safe_take = re.sub(r"[^A-Za-z0-9_-]", "_", take)
    row = {
        "schema_version": "VIDEO-HITL-decision-v1",
        "decision_id": f"VHITL-{decided:%Y%m%d}-{safe_take}-{decided:%H%M%S%f}",
        "gate": "H1",
        "decision": verdict,
        "project": project_dir.name,
        "take": take,
        "expert_decision": expert_decision(project_dir, take),
        "reason": note.strip(),
        "reviewer": reviewer.strip(),
        "decided_at": decided.isoformat(timespec="seconds"),
    }
    definition = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = schema.check(row, definition)
    if errors:
        raise ValueError("；".join(errors))
    project_dir.mkdir(parents=True, exist_ok=True)
    with (project_dir / "human_decisions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description="视频人工判定")
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="追加一条人工判定")
    rec.add_argument("--project", required=True)
    rec.add_argument("--take", required=True)
    rec.add_argument("--verdict", required=True, choices=VERDICTS)
    rec.add_argument("--note", required=True)
    rec.add_argument("--reviewer", default="未署名")
    ls = sub.add_parser("list", help="列出人工判定")
    ls.add_argument("--project", required=True)
    args = ap.parse_args(argv)
    if args.command == "record":
        row = record(args.project, args.take, args.verdict, args.note, args.reviewer)
        print(json.dumps(row, ensure_ascii=False))
    else:
        rows = load(Path(args.project) / "human_decisions.jsonl")
        if not rows:
            print("暂无人工判定")
        for row in rows:
            print(f"{row['decided_at']}  {row['take']}  {row['decision']}  {row['reason']}  ({row['reviewer']})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"人工判定失败：{exc}")
