#!/usr/bin/env python3
"""流程引擎命令行入口。"""
import argparse
import json
import sys

import checkpoints
import engine


def _print(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return
    if isinstance(value, list):
        if not value:
            print("暂无待确认节点")
        for row in value:
            print(f"{row['id']}  {row['product']}  {row['kind']}  {row['summary']}")
        return
    print(value.get("message") or f"{value.get('product')}：{value.get('stage_label')}")
    if value.get("action"):
        command = value["action"].get("command")
        if isinstance(command, list):
            command = " ".join(str(x) for x in command)
        if command:
            print("下一步：", command)
    if value.get("blocking_checkpoint"):
        print("阻塞节点：", value["blocking_checkpoint"]["id"])
    if value.get("budget"):
        print("品预算余额：", value["budget"]["remaining_cny"], "元")
    if value.get("stage_differs"):
        print(f"备注阶段 {value['manual_stage']} 与数据推导阶段 {value['stage']} 不同")


def parser():
    ap = argparse.ArgumentParser(description="按数据推进 studio 工作流")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    sub = ap.add_subparsers(dest="command", required=True)
    def command(name):
        value = sub.add_parser(name)
        value.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                           help="输出机器可读 JSON")
        return value

    nxt = command("next")
    nxt.add_argument("--product", required=True); nxt.add_argument("--run"); nxt.add_argument("--dry-run", action="store_true")
    status = command("status")
    status.add_argument("--product"); status.add_argument("--run")
    command("pending")
    for name in ("approve", "reject", "changes"):
        p = command(name); p.add_argument("id"); p.add_argument("--note", default=""); p.add_argument("--by", default="未署名")
    edit = command("edit-script")
    edit.add_argument("id"); edit.add_argument("--segment", type=int, required=True)
    edit.add_argument("--field", required=True, choices=("voiceover", "caption", "onscreen_copy", "visual_prompt"))
    edit.add_argument("--value", required=True); edit.add_argument("--note", default="人工编辑脚本"); edit.add_argument("--by", default="未署名")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "next":
        code, result = engine.advance(args.product, args.run, args.dry_run)
    elif args.command == "status":
        if args.product:
            result = engine.status(args.product, args.run)
        else:
            import products
            result = [engine.status(row["product"]) for row in products.queue_list()]
        code = 0
    elif args.command == "pending":
        result, code = checkpoints.pending(), 0
    elif args.command in ("approve", "reject", "changes"):
        status = {"approve": "approved", "reject": "rejected", "changes": "changes_requested"}[args.command]
        result, code = checkpoints.resolve(args.id, status, args.note, args.by), 0
        result = {**result, "message": f"节点已{ {'approved':'批准','rejected':'驳回','changes_requested':'要求修改'}[status] }"}
    else:
        result = checkpoints.resolve(args.id, "changes_requested", args.note, args.by,
                                     [{"segment": args.segment, "field": args.field, "value": args.value}])
        result = {**result, "message": "脚本编辑已校验并写入，节点状态为要求修改"}
        code = 0
    _print(result, args.json)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"studio 失败：{exc}")
