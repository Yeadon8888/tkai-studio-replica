#!/usr/bin/env python3
"""Studio 统一入口。

用法：
  studio.py validate <json> <product|project|ledger_entry>   校验契约
  studio.py ledger [--project ID]                            汇总账本
  studio.py research <tt_research 子命令与参数...>             调研流程（透传到 tt_research.py）
  studio.py board --title T --research <批次目录> --production <takes.json> --out <html>
                                                             生成看板（调研 / 生成两个标签页，可只给其一）
"""
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
import ledger  # noqa: E402
import schema  # noqa: E402


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd, rest = argv[1], argv[2:]
    if cmd == "validate":
        if len(rest) != 2:
            sys.exit("用法：studio.py validate <json> <schema名>")
        errs = schema.validate_file(rest[0], rest[1])
        if errs:
            print("校验失败：")
            for e in errs:
                print("  -", e)
            sys.exit(1)
        print("校验通过")
    elif cmd == "ledger":
        project = rest[1] if len(rest) == 2 and rest[0] == "--project" else None
        for (provider, endpoint), s in sorted(ledger.summary(project).items()):
            print(f"{provider:14s} {endpoint:50s} 调用 {s['calls']}  成功 {s['ok']}")
        if not ledger.summary(project):
            print("账本为空")
    elif cmd == "board":
        import argparse
        import dashboard
        import production
        import research
        ap = argparse.ArgumentParser(prog="studio.py board")
        ap.add_argument("--title", default="Studio 看板")
        ap.add_argument("--research", help="调研批次目录（含 candidates.json）")
        ap.add_argument("--production", help="生成数据 takes.json")
        ap.add_argument("--out", required=True)
        a = ap.parse_args(rest)
        data = {"subtitle": "由 Studio 生成，数据来源见各分组说明"}
        if a.research:
            data["research"] = research.build(a.research)
        if a.production:
            data["production"] = production.build(a.production)
        if not (a.research or a.production):
            sys.exit("至少提供 --research 或 --production 之一")
        Path(a.out).write_text(dashboard.render(data, a.title), encoding="utf-8")
        print("看板 →", a.out)
    elif cmd == "research":
        sys.exit(subprocess.call([sys.executable, str(KIT / "tt_research.py")] + rest))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
