"""统一账本：所有付费调用（TikHub、TokensFactory）都写入 studio/ledger/costs.jsonl。

只记录元数据：时间、供应方、端点或模型、状态、用量、关联项目与对象。
不记录任何密钥、完整请求体或视频内容。
"""
import datetime as dt
import json
from collections import Counter
from pathlib import Path

import settings  # noqa: E402

LEDGER_DIR = settings.studio_home() / "ledger"
LEDGER_FILE = LEDGER_DIR / "costs.jsonl"


# 单价（人民币）。只有确认过的价格才写进来；未知的端点估算为 None，不编造。
# 历史直接调用 estimate_cny 时保留默认 768p 价格；新视频行按 settings.VIDEO_PRICES 与 resolution 计价。
PRICES_CNY = {"minimax-H3": 0.13}


def estimate_cny(endpoint, units, status, resolution=None):
    """提交行按单位数估算金额；终态行（saved/failed）金额为 0，避免重复计费。"""
    if str(status) in ("saved", "failed"):
        return 0.0
    price = settings.video_unit_price(endpoint, resolution)
    if price is None and endpoint == settings.model("video"):
        # 旧 .env 只配置 MODEL_VIDEO + VIDEO_PRICE_CNY_PER_SECOND 时继续可用。
        price = settings.get_float("VIDEO_PRICE_CNY_PER_SECOND")
    if price is None and endpoint in PRICES_CNY and resolution is None:
        price = PRICES_CNY[endpoint]
    if price is None or not units:
        return None
    return round(price * float(units), 4)


def is_video_row(row):
    """兼容新旧账本：新行看 kind，旧行按已知视频模型识别。"""
    kind = row.get("kind")
    if kind is not None:
        return kind == "video"
    return row.get("endpoint") in set(settings.video_prices()) | set(PRICES_CNY)


def is_text_row(row):
    """兼容新旧账本：新行看 kind，旧行按文本模型名识别。"""
    kind = row.get("kind")
    if kind is not None:
        return kind == "text"
    names = settings.text_models() | {"gemini-3.8-flash"}  # 历史模型名只用于识别旧账本，不用于发起调用
    return row.get("endpoint") in names


def _rows():
    if not LEDGER_FILE.exists():
        return []
    return [json.loads(line) for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(provider, endpoint, status, project="", ref="", units=None, usage=None, kind=None, resolution=None):
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "provider": provider,        # tikhub | tokensfactory
        "endpoint": endpoint,        # 端点路径或模型名
        "status": str(status),       # 200 | ok | skip | submitted | saved | failed ...
        "project": project,          # 项目 id，如 202611-water-strips
        "ref": ref,                  # 关联对象，如 candidate_id 或 aweme_id
        "units": units,              # 计费单位数（TikHub 为 1 次请求；视频为秒）
        "usage": usage or {},        # 模型 token 用量等
        "resolution": resolution,      # 视频请求的分辨率；旧行没有此字段
        "estimate_cny": estimate_cny(endpoint, units, status, resolution),  # 估算金额，未知为 None
        "kind": kind,                # text | video | search（旧行没有此字段）
    }
    with LEDGER_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def summary(project=None):
    if not LEDGER_FILE.exists():
        return {}
    out = {}
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if project and r.get("project") != project:
            continue
        key = (r["provider"], r["endpoint"])
        s = out.setdefault(key, {"calls": 0, "ok": 0})
        s["calls"] += 1
        if str(r["status"]) in ("200", "ok"):
            s["ok"] += 1
    return out


def count_calls(project, endpoint):
    """某运行、某端点已记账的请求数（含失败），用于调用上限检查。"""
    if not LEDGER_FILE.exists():
        return 0
    n = 0
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("project") == project and r.get("endpoint") == endpoint and r.get("units"):
            n += 1
    return n


def count_text_calls(project):
    """某运行的文本模型请求数（含失败），用于 LIMIT_TEXT_CALLS。新行看 kind，旧行按模型名识别。"""
    if not LEDGER_FILE.exists():
        return 0
    n = 0
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("project") != project or not r.get("units"):
            continue
        if is_text_row(r):
            n += 1
    return n


def usage_by_project(project):
    """汇总某运行的可确认用量；未知价格不折算为金额。"""
    out = {
        "video_cny": 0.0,
        "video_seconds": 0.0,
        "duplicate_submit_count": 0,
        "duplicate_submissions": [],
        "text_calls": 0,
        "text_failures": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "tikhub_calls": 0,
    }
    submitted_refs = Counter()
    for row in _rows():
        if row.get("project") != project:
            continue
        status = str(row.get("status"))
        units = row.get("units")
        if is_video_row(row) and status == "submitted":
            out["video_seconds"] += float(units or 0)
            if isinstance(row.get("estimate_cny"), (int, float)):
                out["video_cny"] += float(row["estimate_cny"])
            if row.get("ref"):
                submitted_refs[row["ref"]] += 1
        elif is_text_row(row) and units:
            out["text_calls"] += 1
            if status not in ("200", "ok"):
                out["text_failures"] += 1
            usage = row.get("usage") or {}
            out["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            out["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        if row.get("provider") == "tikhub" and units:
            out["tikhub_calls"] += 1
    out["video_cny"] = round(out["video_cny"], 4)
    out["video_seconds"] = round(out["video_seconds"], 4)
    out["duplicate_submissions"] = [
        {"ref": ref, "submits": count} for ref, count in sorted(submitted_refs.items()) if count > 1
    ]
    out["duplicate_submit_count"] = sum(x["submits"] - 1 for x in out["duplicate_submissions"])
    return out
