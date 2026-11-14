"""生成视频的自检闸门：把逐项打分变成 通过 / 重做（带退回阶段） / 升级给人。

规则来自 电商一致性 项目的 config/rubric.yaml（已复制到 kit/config/rubric.yaml）。
输入宽容：只要能取到分数和否决项就能判定；缺少的可选字段不报错，只记入 notes。
"""
import os

import yaml
import settings

KIT = os.path.dirname(os.path.abspath(__file__))

# 维度 → 能为它作证的失败原因；来自 config/rubric.yaml 的 dimensions[*].evidence（单一来源）。
# 默认归因 = evidence 的第一项。旧版本是两份硬编码字典，2026-11-12 改为从 yaml 派生，见 tests/test_gate_rubric.py。
DIM_CAUSES = dict()
NEUTRAL = 8.0
DEFAULT_CAUSE = dict()  # 下面由 _derive() 填充


def _derive(rubric):
    dim_causes = {k: set(v.get("evidence", [])) for k, v in rubric["dimensions"].items()}
    default = {k: v["evidence"][0] for k, v in rubric["dimensions"].items() if v.get("evidence")}
    return dim_causes, default


def load_rubric(path=None):
    with open(path or os.path.join(KIT, "config", "rubric.yaml"), encoding="utf-8") as f:
        rubric = yaml.safe_load(f)
    # 兼容只读展示方：配置文件不再保存第二套轮数，运行时值仍来自 settings。
    rubric["max_attempts"] = settings.get_int("MAX_ATTEMPTS")
    return rubric


_DC, _DF = _derive(load_rubric())
DIM_CAUSES.update(_DC)
DEFAULT_CAUSE.update(_DF)


def _cited(review, dim):
    causes = DIM_CAUSES.get(dim, set())
    for i in review.get("issues", []) or []:
        if i.get("cause") in causes and str(i.get("detail", "")).strip() and (i.get("at") or i.get("shot")):
            return True
    return any(v in causes for v in review.get("vetoes", []) or [])


def normalize_review(review, threshold=None):
    """低于 threshold（默认 rubric 的 dimension_floor = 6.5）的分数必须有带时间码的具体问题佐证；
    没有就改为中性分 NEUTRAL（8.0）并记录。
    注意：阈值与专家提示词的 6.5 一致。2026-11-12 修复：此前误用 NEUTRAL 8.0 作阈值，
    导致 7 分的无证据维度被抬成 8 分（见 studio-audit 的 EVIDENCE-FLOOR）。"""
    thr = threshold if threshold is not None else load_rubric()["pass"]["dimension_floor"]
    scores, uncited = {}, []
    for k, v in review["scores"].items():
        if v is None:
            continue
        if v < thr and not _cited(review, k):
            uncited.append({"dim": k, "score": v})
            scores[k] = NEUTRAL
        else:
            scores[k] = v
    return dict(review, scores=scores), uncited


def merge_judges(reviews):
    """多评委：先逐个做低分证据归一，再逐项取较低值；否决项与问题取并集。"""
    norm, uncited = [], []
    for idx, r in enumerate(reviews):
        n, u = normalize_review(r)
        norm.append(n)
        judge = r.get("judge") or {}
        judge_id = judge.get("id") if isinstance(judge, dict) else str(judge)
        judge_name = judge.get("name") if isinstance(judge, dict) else None
        uncited += [dict(x, judge=idx + 1, judge_id=judge_id, judge_name=judge_name) for x in u]
    dims = {}
    for r in norm:
        for k, v in r["scores"].items():
            dims[k] = min(dims.get(k, 10), v)
    vetoes = sorted({v for r in norm for v in r.get("vetoes", []) or []})
    issues = [i for r in norm for i in r.get("issues", []) or []]
    return {"scores": dims, "vetoes": vetoes, "issues": issues, "judges": len(reviews), "uncited_low": uncited}


def evaluate(review, attempt, rubric=None):
    """判定一次生成。返回 decision: pass | repair | escalate，repair 时带 stage 与 retry_from。"""
    R = rubric or load_rubric()
    dims = R["dimensions"]
    scores = review["scores"]
    missing = [k for k in dims if k not in scores or scores[k] is None]
    out = {"attempt": attempt, "missing_dimensions": missing}
    if missing:
        out.update(decision="repair", stage="consistency", reason="评分不完整，需要补齐评审后再判定")
        return out
    weighted = sum(scores[k] * dims[k]["weight"] for k in dims) / sum(d["weight"] for d in dims.values())
    floor = R["pass"]["dimension_floor"]
    low = sorted(k for k in dims if scores[k] < floor)
    vetoes = [v for v in review.get("vetoes", []) or [] if v in R["vetoes"]]
    out.update(weighted=round(weighted, 2), low_dimensions=low, vetoes=vetoes)
    if review.get("uncited_low"):
        out["uncited_low"] = review["uncited_low"]
        labels = []
        for item in review["uncited_low"]:
            expert = item.get("judge_name") or item.get("judge_id") or f"专家{item.get('judge', '?')}"
            labels.append(f"{expert}：{item.get('dim')}（{item.get('score')} 分）")
        out.update(decision="escalate", stage="review",
                   reason="需人工看：专家给了低分但没写原因；" + "；".join(labels))
        return out
    ok = weighted >= R["pass"]["weighted_min"] and not low and not vetoes
    if ok:
        out["decision"] = "pass"
        return out
    # 不通过：与源规则一致。失败归因 = 否决项 + 问题 cause + 低分维度的默认归因；
    # 只保留"最靠前的失败阶段"的原因；修复方案按上游顺序排序，取第一条的 retry_from。
    order = ["plan", "script", "keyframes", "generate", "compose"]
    causes = list(vetoes) + [i.get("cause", "other") for i in review.get("issues", []) or []]
    for k in low:
        causes.append(DEFAULT_CAUSE.get(k, "other"))
    stage_of = lambda c: R["causes"].get(c, R["causes"]["other"]).get("stage", "delivery")
    failed_stage = None
    for st in R["stages"]:
        bad = [v for v in vetoes if v in st["vetoes"]] + [k for k in low if k in st["dimensions"]]
        if bad:
            failed_stage = st["id"]
            break
    if failed_stage:
        causes = [c for c in causes if stage_of(c) == failed_stage] or causes
    plan = []
    for c in dict.fromkeys(causes):
        spec = R["causes"].get(c, R["causes"]["other"])
        plan.append({"cause": c, "retry_from": spec["retry_from"], "fix": spec["fix"]})
    plan.sort(key=lambda p: order.index(p["retry_from"]) if p["retry_from"] in order else 99)
    out.update(stage=failed_stage, failed_stage=failed_stage, repair=plan,
               retry_from=plan[0]["retry_from"] if plan else "plan")
    if attempt >= settings.get_int("MAX_ATTEMPTS"):
        out.update(decision="escalate", reason=f"第 {attempt} 次仍不通过，停止自动重做，交给人")
    else:
        out.update(decision="repair", reason="未通过：按 retry_from 重做")
    return out
