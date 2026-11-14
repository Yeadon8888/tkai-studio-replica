"""人工确认节点：当前状态 JSON + 只追加审计日志。"""
import datetime as dt
import hashlib
import json
from pathlib import Path

import products
import script_writer
import settings
from common import read_json, write_json

KINDS = ("benchmark", "script", "budget", "verdict", "publish", "review_data")
STATUSES = ("pending", "approved", "rejected", "changes_requested")
BLOCKING_KINDS = set(KINDS) - {"review_data"}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _root(product):
    return settings.studio_home() / "products" / product / "checkpoints"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _id(product, run, kind, subject):
    raw = "\0".join((product, run or "", kind, _canonical(subject)))
    return f"cp-{kind}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _append(product, event, checkpoint, **extra):
    root = _root(product)
    root.mkdir(parents=True, exist_ok=True)
    row = {"time": _now(), "event": event, "checkpoint": checkpoint, **extra}
    with (root / "log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _amount(subject):
    if isinstance(subject, dict):
        for key in ("estimated_cny", "estimate_cny", "amount_cny"):
            if subject.get(key) is not None:
                return float(subject[key])
    return None


def create(product, run, kind, summary, subject, options=None):
    """幂等创建节点；预算节点在双重边界内可自动批准。"""
    if kind not in KINDS:
        raise ValueError(f"未知确认节点类型：{kind}")
    cid = _id(product, run, kind, subject)
    path = _root(product) / f"{cid}.json"
    if path.exists():
        return read_json(path)
    row = {
        "id": cid, "product": product, "run": run or "", "kind": kind,
        "status": "pending", "created": _now(), "summary": str(summary),
        "subject": subject, "options": list(options or []), "decided_by": "",
        "decided_at": "", "note": "", "edits": [],
    }
    write_json(path, row)
    _append(product, "created", row)
    if kind == "budget":
        amount = _amount(subject)
        auto = settings.get_float("AUTO_APPROVE_CNY")
        if amount is not None and auto > 0:
            can_pay, budget = products.can_spend(product, amount)
            if can_pay and amount <= auto + 1e-9:
                row.update(status="approved", decided_by="system:auto_budget", decided_at=_now(),
                           note=f"预计 {amount:g} 元不超过自动批准上限 {auto:g} 元及品剩余预算")
                write_json(path, row)
                _append(product, "resolved", row, automatic=True, budget=budget)
    return row

def get(checkpoint_id, product=None):
    if product:
        path = _root(product) / f"{checkpoint_id}.json"
        if path.exists():
            return read_json(path)
    base = settings.studio_home() / "products"
    for path in base.glob(f"*/checkpoints/{checkpoint_id}.json") if base.exists() else ():
        return read_json(path)
    raise KeyError(f"没有确认节点：{checkpoint_id}")


def pending(product=None):
    base = settings.studio_home() / "products"
    pattern = f"{product}/checkpoints/*.json" if product else "*/checkpoints/*.json"
    rows = []
    for path in base.glob(pattern) if base.exists() else ():
        if path.name == "log.jsonl":
            continue
        row = read_json(path)
        if row and row.get("status") == "pending":
            rows.append(row)
    return sorted(rows, key=lambda row: (row.get("created", ""), row["id"]))


def _script_path(row):
    subject = row.get("subject")
    value = subject.get("script") if isinstance(subject, dict) else subject
    if value:
        path = Path(value)
        return path if path.is_absolute() else settings.studio_home() / path
    return settings.studio_home() / "products" / row["product"] / "runs" / row["run"] / "script.json"


def _apply_script_edits(row, edits):
    path = _script_path(row)
    script = read_json(path)
    if not script:
        raise ValueError(f"找不到脚本：{path}")
    before_after = []
    allowed = {"voiceover", "caption", "onscreen_copy", "visual_prompt"}
    for edit in edits:
        segment, field = int(edit["segment"]), edit["field"]
        if field not in allowed:
            raise ValueError(f"不可编辑字段：{field}")
        if segment < 1 or segment > len(script.get("shots") or []):
            raise ValueError(f"脚本段号不存在：{segment}")
        target = script["shots"][segment - 1]
        before = target.get(field, "")
        target[field] = edit.get("value", "")
        before_after.append({"segment": segment, "field": field, "before": before,
                             "after": target[field]})
    project = path.parent
    shots = read_json(project / "shots.json")
    facts = script_writer.load_facts(project, [])
    errors = script_writer.validate(script, shots or {}, facts,
                                    script_writer.competitor_terms(project, shots or {}))
    if errors:
        raise ValueError("脚本编辑未通过校验：" + "；".join(errors))
    write_json(path, script)
    return before_after


def resolve(checkpoint_id, status, note, by, edits=None):
    if status not in STATUSES or status == "pending":
        raise ValueError("确认结果必须是 approved、rejected 或 changes_requested")
    if status in ("rejected", "changes_requested") and not str(note or "").strip():
        raise ValueError("驳回或要求修改时必须填写 note")
    if not str(by or "").strip():
        raise ValueError("必须填写决定人")
    row = get(checkpoint_id)
    applied = _apply_script_edits(row, list(edits or [])) if edits else []
    row.update(status=status, decided_by=str(by).strip(), decided_at=_now(),
               note=str(note or "").strip(), edits=list(row.get("edits") or []) + applied)
    write_json(_root(row["product"]) / f"{row['id']}.json", row)
    _append(row["product"], "resolved", row, edits=applied)
    return row
