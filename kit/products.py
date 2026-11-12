"""品级管理：一个品一个文件夹，预算与任务队列。

目录（studio/products/）：
  <product_id>/product.json        品卡（已有约定，本模块不写）
  <product_id>/product_state.json  预算、已选对标、运行记录（runs）；本模块读写
  <product_id>/product_anchor.txt  产品锚点（逐字注入每个镜头）
  _queue.json                  全部品的任务队列（多品并行时用来看进度与下一步）

预算规则（用户约定）：
- 默认每个品 5 元（budget_cny）。超出需要提前沟通，并在 product.json 写 approved_budget_cny 与理由。
- TikHub、Gemini 不设上限（仅记账）；TokensFactory 视频按账本估算金额计入品预算。
- 账本行的 project 字段 = 运行目录名；只统计 runs 里登记过的运行。
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
STUDIO = KIT.parent
PRODUCTS = STUDIO / "products"
QUEUE = PRODUCTS / "_queue.json"
DEFAULT_BUDGET_CNY = 5.0
STAGES = ["scope", "research", "breakdown", "brief", "generate", "review", "board", "done"]


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _read(p, default):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def product_dir(pid):
    return PRODUCTS / pid


def load(pid):
    p = product_dir(pid) / "product_state.json"
    if not p.exists():
        raise SystemExit(f"没有品 {pid}：先运行 create")
    return _read(p, {})


def create(pid, name, budget_cny=DEFAULT_BUDGET_CNY, anchor=""):
    d = product_dir(pid)
    if (d / "product_state.json").exists():
        raise SystemExit(f"品 {pid} 已存在")
    meta = {"product_id": pid, "name": name, "anchor": anchor, "budget_cny": budget_cny,
            "approved_budget_cny": None, "budget_note": "", "selected_benchmarks": [],
            "runs": [], "created": _now()}
    _write(d / "product_state.json", meta)
    _queue_set(pid, stage="scope", status="active", note="新建")
    return meta


def register_run(pid, run_id):
    meta = load(pid)
    if run_id not in meta["runs"]:
        meta["runs"].append(run_id)
        _write(product_dir(pid) / "product_state.json", meta)
    return meta


def approve_budget(pid, cny, note):
    """超出默认预算时由用户确认后调用。必须写明理由。"""
    if not note.strip():
        raise SystemExit("超出预算必须写明理由（note）")
    meta = load(pid)
    meta["approved_budget_cny"] = float(cny)
    meta["budget_note"] = note
    _write(product_dir(pid) / "product_state.json", meta)
    return meta


def select_benchmarks(pid, ids, batch=None):
    """记录要复刻的对标（可多条；来自调研勾选或用户在 skill 里点名）。同一个品可以有多条复刻视频。"""
    meta = load(pid)
    cur = meta.setdefault("selected_benchmarks", [])
    for cid in ids:
        if cid not in [x["id"] for x in cur]:
            cur.append({"id": cid, "batch": batch, "selected_at": _now()})
    _write(product_dir(pid) / "product_state.json", meta)
    _queue_set(pid, note=f"已选对标 {len(cur)} 条")
    return cur


def limit_cny(meta):
    return float(meta.get("approved_budget_cny") or meta.get("budget_cny") or DEFAULT_BUDGET_CNY)


def spent_cny(pid, ledger_file=None):
    """从账本统计该品已花的金额（只计 estimate_cny 为数字的行；None 视为未知，单独报告）。"""
    meta = load(pid)
    lf = Path(ledger_file) if ledger_file else (STUDIO / "ledger" / "costs.jsonl")
    runs = set(meta.get("runs", []))
    total, unknown = 0.0, 0
    if lf.exists():
        for line in lf.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("project") not in runs:
                continue
            est = r.get("estimate_cny")
            if est is None:
                unknown += 1
            else:
                total += float(est)
    return {"spent_cny": round(total, 4), "unknown_rows": unknown, "limit_cny": limit_cny(meta),
            "remaining_cny": round(limit_cny(meta) - total, 4)}


def can_spend(pid, cny, ledger_file=None):
    s = spent_cny(pid, ledger_file)
    return s["spent_cny"] + float(cny) <= s["limit_cny"] + 1e-9, s


def _queue_set(pid, stage=None, status=None, note=None):
    q = _read(QUEUE, {"tasks": {}})
    t = q["tasks"].setdefault(pid, {"stage": "scope", "status": "active", "note": "", "updated": ""})
    if stage:
        t["stage"] = stage
    if status:
        t["status"] = status
    if note is not None:
        t["note"] = note
    t["updated"] = _now()
    _write(QUEUE, q)
    return t


def queue_list():
    q = _read(QUEUE, {"tasks": {}})
    rows = []
    for pid, t in sorted(q["tasks"].items(), key=lambda kv: kv[1].get("updated", "")):
        idx = STAGES.index(t["stage"]) if t["stage"] in STAGES else 0
        rows.append({"product": pid, "stage": t["stage"], "progress": f"{idx}/{len(STAGES) - 1}",
                     "status": t["status"], "note": t.get("note", ""), "updated": t.get("updated", "")})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="品级管理：文件夹、预算、任务队列")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--product", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--anchor", default="", help="产品锚点；也可放在 product_anchor.txt")
    c.add_argument("--budget", type=float, default=DEFAULT_BUDGET_CNY)
    s = sub.add_parser("status")
    s.add_argument("--product", required=True)
    ap_ = sub.add_parser("approve-budget")
    ap_.add_argument("--product", required=True)
    ap_.add_argument("--cny", type=float, required=True)
    ap_.add_argument("--note", required=True)
    r = sub.add_parser("register-run")
    r.add_argument("--product", required=True)
    r.add_argument("--run", required=True)
    sb = sub.add_parser("select", help="记录要复刻的对标（逗号分隔，可多条）")
    sb.add_argument("--product", required=True)
    sb.add_argument("--ids", required=True)
    sb.add_argument("--batch", help="调研批次目录名，便于追溯")
    q = sub.add_parser("queue")
    q.add_argument("--set-stage", nargs=2, metavar=("PRODUCT", "STAGE"))
    a = ap.parse_args(argv)
    if a.cmd == "create":
        m = create(a.product, a.name, a.budget, a.anchor)
        print("已创建", product_dir(a.product), "预算", m["budget_cny"], "元")
    elif a.cmd == "status":
        print(json.dumps({"product": load(a.product), "budget": spent_cny(a.product)}, ensure_ascii=False, indent=2))
    elif a.cmd == "approve-budget":
        m = approve_budget(a.product, a.cny, a.note)
        print("已确认预算上限", m["approved_budget_cny"], "元；理由：", m["budget_note"])
    elif a.cmd == "select":
        ids = [x.strip() for x in a.ids.split(",") if x.strip()]
        cur = select_benchmarks(a.product, ids, a.batch)
        print("已选对标：", ", ".join(x["id"] for x in cur))
    elif a.cmd == "register-run":
        register_run(a.product, a.run)
        print("已登记运行", a.run)
    elif a.cmd == "queue":
        if a.set_stage:
            pid, st = a.set_stage
            if st not in STAGES:
                raise SystemExit(f"阶段必须是 {STAGES}")
            _queue_set(pid, stage=st, status="done" if st == "done" else "active")
        for row in queue_list():
            print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
