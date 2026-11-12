"""统一账本：所有付费调用（TikHub、TokensFactory）都写入 studio/ledger/costs.jsonl。

只记录元数据：时间、供应方、端点或模型、状态、用量、关联项目与对象。
不记录任何密钥、完整请求体或视频内容。
"""
import datetime as dt
import json
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
LEDGER_FILE = LEDGER_DIR / "costs.jsonl"


# 单价（人民币）。只有确认过的价格才写进来；未知的端点估算为 None，不编造。
# minimax-H3：0.13 元 / 请求秒（CLAUDE.md 中的模型表）。
PRICES_CNY = {"minimax-H3": 0.13}


def estimate_cny(endpoint, units, status):
    """提交行按单位数估算金额；终态行（saved/failed）金额为 0，避免重复计费。"""
    if str(status) in ("saved", "failed"):
        return 0.0
    price = PRICES_CNY.get(endpoint)
    if price is None or not units:
        return None
    return round(price * float(units), 4)


def append(provider, endpoint, status, project="", ref="", units=None, usage=None):
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
        "estimate_cny": estimate_cny(endpoint, units, status),  # 估算金额，未知为 None
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
