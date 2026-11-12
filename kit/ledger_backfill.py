"""补全历史视频提交行的估算金额；默认只预览，--apply 才写回。"""
import argparse
import datetime as dt
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import ledger


def plan(path):
    """返回原始行、待回填项和回填后各项目视频金额。"""
    p = Path(path)
    raw_lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    changes = []
    totals = defaultdict(float)
    rows = []
    for index, raw in enumerate(raw_lines):
        if not raw.strip():
            rows.append(None)
            continue
        row = json.loads(raw)
        rows.append(row)
        if ledger.is_video_row(row) and row.get("status") == "submitted":
            amount = row.get("estimate_cny")
            if amount is None:
                amount = ledger.estimate_cny(row.get("endpoint"), row.get("units"), row.get("status"))
                if amount is not None:
                    changes.append({"index": index, "project": row.get("project") or "", "ref": row.get("ref") or "",
                                    "units": row.get("units"), "amount": amount})
            if isinstance(amount, (int, float)):
                totals[row.get("project") or ""] += float(amount)
    return raw_lines, rows, changes, {k: round(v, 4) for k, v in sorted(totals.items())}


def run(path, apply=False, out=None):
    p = Path(path)
    raw_lines, rows, changes, totals = plan(p)
    write = out or print
    write(f"待回填视频提交：{len(changes)} 行")
    for item in changes:
        write(f"  {item['project']} / {item['ref']}：{item['units']} 秒 → {item['amount']:.4f} 元")
    write("回填后各项目视频金额：")
    for project, amount in totals.items():
        write(f"  {project or '（未标项目）'}：{amount:.4f} 元")
    write(f"回填后视频金额合计：{sum(totals.values()):.4f} 元")
    if not apply:
        write("dry-run：未写入账本；使用 --apply 才会备份并写回")
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = p.with_name(f"{p.name}.bak-{stamp}")
    shutil.copy2(p, backup)
    change_by_index = {x["index"]: x for x in changes}
    rendered = []
    for index, raw in enumerate(raw_lines):
        if index not in change_by_index:
            rendered.append(raw)
            continue
        row = rows[index]
        row["estimate_cny"] = change_by_index[index]["amount"]
        ending = "\n" if raw.endswith("\n") else ""
        rendered.append(json.dumps(row, ensure_ascii=False) + ending)
    p.write_text("".join(rendered), encoding="utf-8")
    write(f"已备份：{backup}")
    write(f"已回填：{p}")
    return backup


def main(argv=None):
    ap = argparse.ArgumentParser(description="补全历史视频提交的 estimate_cny（默认 dry-run）")
    ap.add_argument("--ledger", default=str(ledger.LEDGER_FILE), help="账本路径")
    ap.add_argument("--apply", action="store_true", help="备份后写回")
    args = ap.parse_args(argv)
    run(args.ledger, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
