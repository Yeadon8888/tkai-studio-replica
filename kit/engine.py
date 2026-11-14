"""由文件数据推导工作流阶段，并且一次只推进一步。"""
import json
import subprocess
import sys
from pathlib import Path

import checkpoints
import products
import settings
from common import read_json

KIT = Path(__file__).resolve().parent

STAGE_LABELS = {
    "product": "产品卡", "benchmark": "对标与档位", "plan": "计划", "shots": "镜头表",
    "script": "脚本", "budget": "预算", "generation": "生成", "review": "评审",
    "verdict": "人工判定", "publish": "发布", "done": "完成",
}


def _checkpoint(product, run, kind, subject):
    cid = checkpoints._id(product, run or "", kind, subject)
    try:
        return checkpoints.get(cid, product)
    except KeyError:
        return None


def _latest_run(meta, requested=None):
    run = requested or ((meta.get("runs") or [""])[-1])
    return run, products.product_dir(meta["product_id"]) / "runs" / run if run else None


def _briefs(run_dir):
    return sorted((run_dir / "briefs").glob("*.json")) if run_dir and (run_dir / "briefs").exists() else []


def _estimate(brief_paths):
    values = []
    for path in brief_paths:
        data = read_json(path, {}) or {}
        value = data.get("estimated_cny", data.get("estimate_cny"))
        if value is None:
            return None
        values.append(float(value))
    return round(sum(values), 4)


def _queue_manual(product):
    for row in products.queue_list():
        if row["product"] == product:
            return row.get("stage")
    return None


def status(product, run=None):
    """返回完全由当前数据推导的阶段与下一动作。"""
    pdir = products.product_dir(product)
    state_path = pdir / "product_state.json"
    budget = products.spent_cny(product) if state_path.exists() else None
    base = {"product": product, "run": run or "", "budget": budget, "blocking_checkpoint": None}
    if not state_path.exists():
        return {**base, "stage": "product", "stage_label": STAGE_LABELS["product"],
                "action": {"type": "human", "command": f"python3 kit/products.py create --product {product} --name '<品名>'",
                           "summary": "先建立产品卡"}, "manual_stage": _queue_manual(product), "stage_differs": False}
    meta = products.load(product)
    run_id, run_dir = _latest_run(meta, run)
    base["run"] = run_id
    plan_path = pdir / "plan.json"
    selected = meta.get("selected_benchmarks") or []
    # subject 只包含选择本身；生成 plan.json 后仍应命中同一个批准节点。
    benchmark_subject = {"selected": selected}
    benchmark_cp = _checkpoint(product, run_id, "benchmark", benchmark_subject)
    if (not selected and not plan_path.exists()) or not benchmark_cp or benchmark_cp["status"] != "approved":
        action = {"type": "checkpoint", "kind": "benchmark", "summary": "确认对标视频、复刻档位和制作范围",
                  "subject": benchmark_subject, "options": ["确认当前选择", "调整对标", "调整档位"]}
        result = {**base, "stage": "benchmark", "stage_label": STAGE_LABELS["benchmark"], "action": action,
                  "blocking_checkpoint": benchmark_cp}
        return _with_manual(result)
    if not plan_path.exists():
        result = {**base, "stage": "plan", "stage_label": STAGE_LABELS["plan"],
                  "action": {"type": "local", "command": [sys.executable, str(KIT / "intake.py"), "plan", "--product", product, "--n", "1", "--all"],
                             "summary": "本地计算一条制作计划", "estimated_cny": 0.0}}
        return _with_manual(result)
    if not run_id or not run_dir or not run_dir.exists():
        result = {**base, "stage": "plan", "stage_label": STAGE_LABELS["plan"],
                  "action": {"type": "local", "command": [sys.executable, str(KIT / "products.py"), "new-run", "--product", product, "--name", "engine"],
                             "summary": "建立本次运行目录", "estimated_cny": 0.0}}
        return _with_manual(result)
    if not (run_dir / "shots.json").exists():
        first = selected[0] if selected else {}
        command = [sys.executable, str(KIT / "shot_list.py"), "--project", str(run_dir),
                   "--batch", str(first.get("batch") or "<调研批次>"), "--id", str(first.get("id") or "<候选id>")]
        result = {**base, "stage": "shots", "stage_label": STAGE_LABELS["shots"],
                  "action": {"type": "paid", "command": command, "summary": "调用模型拆出镜头表", "estimated_cny": None,
                             "approval": benchmark_cp["id"]}}
        return _with_manual(result)
    early_briefs = _briefs(run_dir)
    if not (run_dir / "script.json").exists() and not early_briefs:
        facts = meta.get("anchor") or "<已确认产品事实>"
        result = {**base, "stage": "script", "stage_label": STAGE_LABELS["script"],
                  "action": {"type": "paid", "command": [sys.executable, str(KIT / "script_writer.py"), "--project", str(run_dir), "--facts", facts],
                             "summary": "调用脚本模型并运行代码校验", "estimated_cny": None, "approval": benchmark_cp["id"]}}
        return _with_manual(result)
    if (run_dir / "script.json").exists():
        script_subject = {"script": str((run_dir / "script.json").relative_to(settings.studio_home()))}
    else:
        # H/M/L 路线的简报本身就是交给人确认的脚本与生成输入。
        script_subject = {"briefs": [str(path.relative_to(settings.studio_home())) for path in early_briefs]}
    script_cp = _checkpoint(product, run_id, "script", script_subject)
    if not script_cp or script_cp["status"] != "approved":
        result = {**base, "stage": "script", "stage_label": STAGE_LABELS["script"],
                  "action": {"type": "checkpoint", "kind": "script", "summary": "确认脚本、字幕和画面提示词",
                             "subject": script_subject, "options": ["批准", "要求修改", "驳回"]},
                  "blocking_checkpoint": script_cp}
        return _with_manual(result)
    briefs = early_briefs
    if not briefs:
        refs = sorted(str(p) for p in pdir.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
        result = {**base, "stage": "plan", "stage_label": STAGE_LABELS["plan"],
                  "action": {"type": "local", "command": [sys.executable, str(KIT / "shot_plan.py"), "--project", str(run_dir), "--refs", ",".join(refs) or "<产品参考图>"],
                             "summary": "本地生成连续性计划", "estimated_cny": 0.0}}
        return _with_manual(result)
    amount = _estimate(briefs)
    budget_subject = {"briefs": [str(p.relative_to(settings.studio_home())) for p in briefs], "estimated_cny": amount}
    budget_cp = _checkpoint(product, run_id, "budget", budget_subject)
    if not budget_cp or budget_cp["status"] != "approved":
        result = {**base, "stage": "budget", "stage_label": STAGE_LABELS["budget"],
                  "action": {"type": "checkpoint", "kind": "budget", "summary": f"确认本轮预计费用 {amount if amount is not None else '未知'} 元",
                             "subject": budget_subject, "options": ["批准预算", "缩减计划", "驳回"]},
                  "blocking_checkpoint": budget_cp}
        return _with_manual(result)
    takes = sorted((run_dir / "takes").glob("*.mp4")) if (run_dir / "takes").exists() else []
    if not takes:
        brief = briefs[0]
        result = {**base, "stage": "generation", "stage_label": STAGE_LABELS["generation"],
                  "action": {"type": "paid", "command": [sys.executable, str(KIT / "loop.py"), "gen", "--project", str(run_dir), "--brief", str(brief), "--cap", "1"],
                             "summary": "生成一个视频步骤后停止", "estimated_cny": amount, "approval": budget_cp["id"]}}
        return _with_manual(result)
    decisions = sorted((run_dir / "reviews").glob("*.decision.json")) if (run_dir / "reviews").exists() else []
    if not decisions:
        take = takes[0].stem
        result = {**base, "stage": "review", "stage_label": STAGE_LABELS["review"],
                  "action": {"type": "paid", "command": [sys.executable, str(KIT / "loop.py"), "review", "--project", str(run_dir), "--take", take, "--refs", ""],
                             "summary": "评审一条成片后停止", "estimated_cny": None, "approval": budget_cp["id"]}}
        return _with_manual(result)
    decision = read_json(decisions[-1], {}) or {}
    take = decisions[-1].name.replace(".decision.json", "")
    verdict_subject = {"take": take, "decision": decision.get("decision"), "review": str(decisions[-1].relative_to(settings.studio_home()))}
    verdict_cp = _checkpoint(product, run_id, "verdict", verdict_subject)
    if decision.get("decision") in ("pass", "escalate") and (not verdict_cp or verdict_cp["status"] != "approved"):
        result = {**base, "stage": "verdict", "stage_label": STAGE_LABELS["verdict"],
                  "action": {"type": "checkpoint", "kind": "verdict", "summary": "人工判断这条成片是否可用",
                             "subject": verdict_subject, "options": ["可用", "要求修改", "不用"]},
                  "blocking_checkpoint": verdict_cp}
        return _with_manual(result)
    if decision.get("decision") == "repair":
        result = {**base, "stage": "review", "stage_label": STAGE_LABELS["review"],
                  "action": {"type": "human", "summary": "闸门要求返修，请按 review 中的 retry_from 处理", "command": "查看评审记录"}}
        return _with_manual(result)
    publish_subject = {"take": take, "account": "", "url": ""}
    publish_cp = _checkpoint(product, run_id, "publish", publish_subject)
    if not publish_cp or publish_cp["status"] == "pending":
        result = {**base, "stage": "publish", "stage_label": STAGE_LABELS["publish"],
                  "action": {"type": "checkpoint", "kind": "publish", "summary": "记录是否发布、发布账号和链接",
                             "subject": publish_subject, "options": ["已发布", "暂不发布"]}, "blocking_checkpoint": publish_cp}
        return _with_manual(result)
    return _with_manual({**base, "stage": "done", "stage_label": STAGE_LABELS["done"], "action": None})


def _with_manual(result):
    manual = _queue_manual(result["product"])
    result["manual_stage"] = manual
    result["stage_differs"] = bool(manual and manual != result["stage"])
    return result


def _approved_amount(row):
    return checkpoints._amount(row.get("subject"))


def advance(product, run=None, dry_run=False, runner=None):
    """推进恰好一步。返回 (退出码, 结构化结果)。"""
    current = status(product, run)
    action = current.get("action")
    if not action:
        return 0, {**current, "message": "流程已经完成"}
    if action["type"] == "checkpoint":
        if dry_run:
            return 0, {**current, "message": "将创建确认节点（dry-run 未写入）"}
        cp = checkpoints.create(product, current.get("run"), action["kind"], action["summary"],
                                action["subject"], action.get("options"))
        if cp["status"] == "approved":
            return 0, {**current, "blocking_checkpoint": cp, "message": "确认节点已自动批准；再次运行 next 继续"}
        return 3, {**current, "blocking_checkpoint": cp,
                   "message": f"等待确认：{cp['summary']}；批准命令：python3 kit/studio.py approve {cp['id']}"}
    if dry_run:
        return 0, {**current, "message": "dry-run：未执行", "would_run": action.get("command")}
    if action["type"] == "human":
        return 3, {**current, "message": action["summary"]}
    if action["type"] == "paid":
        approval = checkpoints.get(action["approval"], product)
        if approval["status"] != "approved":
            return 3, {**current, "message": "付费步骤尚未批准"}
        amount = action.get("estimated_cny")
        approved = _approved_amount(approval)
        if amount is not None and approved is not None and amount > approved + 1e-9:
            return 3, {**current, "message": "预计金额超过已批准额度"}
        if amount is not None and not products.can_spend(product, amount)[0]:
            return 3, {**current, "message": "预计金额超过品剩余预算"}
    run_command = runner or (lambda command: subprocess.run(command, check=False).returncode)
    code = run_command(action["command"])
    return code, {**current, "executed": action["command"],
                  "message": "已执行一个步骤并停止" if code == 0 else f"步骤执行失败，退出码 {code}"}
