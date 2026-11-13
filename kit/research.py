"""调研数据 → 看板数据。输入宽容：candidates.json 必需，verify.json、breakdown.json 可选。

分组规则（先用这一版，后面可改）：
- 验证结论 A → "A 模板"
- 验证为 Reject，或产品关系为 different → "D 边界"
- 其余入选或待复核 → "C 待验证"
- "B 点击强转化弱" 需要广告转化数据，目前没有，不分配。
转化、CVR、ROI 没有数据时显示"未提供"，不估算。
"""
import json
from pathlib import Path

import covers
import replication


def _load(path):
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _group(c, v):
    if c.get("topic_level") == "analogy" or (v and v.get("verdict") == "Reject" and v.get("product_relation") == "similar"):
        return "E 类比参考"
    if v and v.get("verdict") == "A":
        return "A 模板"
    if (v and (v.get("verdict") == "Reject" or v.get("product_relation") == "different")):
        return "D 边界"
    return "C 待验证"


USE_TEXT = {
    "H": "可直接替换产品或人物，动作、场景、机位、节奏保留",
    "M": "保留灵魂卡的必选项，场景和动作可换",
    "L": "只参考钩子、证明、行动的逻辑，画面全部重做",
    "X": "只作参考，不复刻，脚本由方法论自写",
}


def use_text(tier, relation):
    """适用方式。同款（same）才可能替换产品；同类不同形态或不同品类只借逻辑。"""
    base = USE_TEXT.get(tier, USE_TEXT["X"])
    if tier in ("H", "M") and relation not in (None, "same"):
        return "同类不同形态，只借逻辑：" + base
    return base


def build(out_dir):
    out = Path(out_dir)
    cands = _load(out / "candidates.json") or []
    verify = {r["aweme_id"]: r for r in (_load(out / "verify.json") or [])}
    breakdown = {r["aweme_id"]: r for r in (_load(out / "breakdown.json") or [])}
    tier_overrides = _load(out / "tier_overrides.json") or {}
    cases = []
    for c in cands:
        # 只展示与产品有关的案例（主题强词或弱词）；与产品无关的硬筛通过条目不进看板
        if not c.get("hard_pass") or c.get("topic_level") not in ("strong", "analogy"):
            continue
        v = verify.get(c["aweme_id"])
        b = breakdown.get(c["aweme_id"], {})
        case = {
            "id": c.get("candidate_id") or c["aweme_id"],
            "aweme_id": c["aweme_id"],
            "title": (c.get("desc") or "")[:80],
            "author": c.get("author"),
            "url": c.get("url"),
            # 优先内嵌本地封面（不会过期）；没有本地封面才用原始地址（可能已过期）
            "cover": covers.data_uri(out, c) or c.get("cover_url"),
            "cover_local": bool(covers.data_uri(out, c)),
            "group": _group(c, v),
            "metrics": {
                "plays": c.get("plays"), "likes": c.get("likes"), "comments": c.get("comments"),
                "saves": c.get("saves"), "shares": c.get("shares"),
                "followers": c.get("author_followers"), "duration_s": c.get("duration_s"),
                "published": c.get("published_at_cst"),
            },
            "viral_type": c.get("viral_type"),
            "auto_score": c.get("auto_score"),
            "dims": b.get("dims"),                 # 七维拆解，缺失则为 None
            "judgement": b.get("judgement"),
            "evidence": b.get("evidence"),
            "verify": ({k: v.get(k) for k in ("product_relation", "product_shown", "hook_first_3s",
                                               "structure", "replication_risk", "verdict", "reason")}
                       if v and not v.get("parse_error") else None),
            "risk": (v or {}).get("risk_reason"),
            "final_pass": bool(c.get("final_pass")),
            "topic_level": c.get("topic_level"),  # 定档依赖它：缺了就无法判成 H
            "source": c.get("source") or "tiktok",
            "local_file": c.get("local_file"),
            "relation": c.get("relation"),
        }
        tier, tier_reasons = replication.classify(case)
        override = tier_overrides.get(case["id"])
        if isinstance(override, dict) and override.get("tier") in ("H", "M", "L", "X"):
            tier = override["tier"]
            note = (override.get("note") or "").strip()
            tier_reasons = [f"人工确认为 {tier} 档" + (f"：{note}" if note else "")]
        case["tier"] = tier
        case["tier_text"] = replication.TIER_TEXT[tier]
        case["tier_reasons"] = tier_reasons
        case["use"] = use_text(tier, c.get("relation") or (v or {}).get("product_relation"))
        reasons = []
        if not case["final_pass"] and case.get("topic_level") != "analogy":
            reasons.append("未过主题门控/硬筛")
        if tier == "X":
            reasons += tier_reasons
        case["eligible"] = not reasons
        case["block_reasons"] = reasons
        cases.append(case)
    groups = ["A 模板", "B 点击强转化弱", "C 待验证", "D 边界", "E 类比参考"]
    summary = ("分组依据 AI 验证结论；B 组需要广告转化数据，当前没有，因此未分配。"
               "可进入制作 = 主题入选 + 验证 A/B + 七维已拆解 + 可复制 ≥ 1（闸门 E1–E2）。")
    return {"groups": groups, "cases": cases, "summary": summary}
