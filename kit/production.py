"""生成数据 → 看板数据。输入宽容：takes.json 只要有 scores（或 reviews）和 vetoes 即可。

takes.json 结构（宽松）：
{"briefs": [{"id": "...", "title": "...", "takes": [
   {"id": "...", "attempt": 1, "video": "...", "note": "...", "series": "...", "cost_cny": 1.3,
    "reviews": [{"scores": {...}, "vetoes": [...], "issues": [...]}, ...]}  // 多个评委
  ]}]}
判定由 kit/gate.py 完成（先合并评委，再套通过条件）。

看板用到的三样东西：
- grade（每条成片的分级）：good 通过；minor 没有大问题（无否决、无低于下限的维度，只是总分不到线）；
  low 打分低（有维度低于下限）；severe 严重瑕疵（有否决项或机器检查不过）；unreviewed 还没评审。
- series（提示词系列）：同一条简报及其返修、修订轮次归为一组。组内选一条"当前成片"，其余是"过往废片"。
- stats：总条数、通过数、废片率、总花费、每条成品的单价。
"""
import json
from pathlib import Path

import gate
import ledger

HUMAN_TO_EXPERT = {"accept": "pass", "release": "pass", "iterate": "repair", "redo": "repair",
                   "hold": "escalate", "abandon": "escalate"}

GRADE_TEXT = {"good": "通过", "minor": "没有大问题", "low": "打分低", "severe": "严重瑕疵", "unreviewed": "未评审"}
GRADE_RANK = {"good": 0, "minor": 1, "low": 2, "severe": 3, "unreviewed": 4}


def grade(row, floor):
    if row.get("decision") is None:
        return "unreviewed"
    if row.get("decision") == "pass":
        return "good"
    if row.get("vetoes") or row.get("machine_blocking") or row.get("decision") == "escalate":
        return "severe"
    if any(v is not None and v < floor for v in (row.get("scores") or {}).values()):
        return "low"
    return "minor"


def _stats(takes):
    reviewed = [t for t in takes if t["grade"] != "unreviewed"]
    good = [t for t in takes if t["grade"] == "good"]
    cost = round(sum(t.get("cost_cny") or 0 for t in takes), 2)
    return {"takes": len(takes), "reviewed": len(reviewed), "good": len(good),
            "scrap": len(reviewed) - len(good),
            "scrap_rate": round((len(reviewed) - len(good)) / len(reviewed), 3) if reviewed else None,
            "cost_cny": cost, "cost_per_good": round(cost / len(good), 2) if good else None,
            "cost_estimated": any(t.get("cost_estimated") for t in takes),
            "by_grade": {g: sum(1 for t in takes if t["grade"] == g) for g in GRADE_TEXT}}


def _human_latest(path):
    latest = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row["take"]] = row
    return latest


def _legacy_fidelity(reviews):
    """R8 前的历史评审没有还原度；离线展示时用中性分兼容，不伪造 R 专家结论。"""
    if reviews and not any("replication_fidelity" in (row.get("scores") or {}) for row in reviews):
        return list(reviews) + [{"scores": {"replication_fidelity": gate.NEUTRAL}, "vetoes": [], "issues": []}]
    return reviews


def build_series(briefs):
    """把所有成片按提示词系列归组。当前成片 = 通过的那条；没有通过的，取分级最好、轮次最新的一条。"""
    groups, order = {}, []
    for b in briefs:
        for t in b["takes"]:
            key = t.get("series") or b["id"]
            if key not in groups:
                groups[key] = {"id": key, "title": b.get("title") or key, "takes": []}
                order.append(key)
            groups[key]["takes"].append(t)
    out = []
    for key in order:
        g = groups[key]
        takes = sorted(g["takes"], key=lambda t: (t.get("attempt") or 0, t.get("id") or ""))
        final = sorted(takes, key=lambda t: (GRADE_RANK[t["grade"]], -(t.get("attempt") or 0)))[0]
        out.append({"id": key, "title": g["title"], "final": final["id"], "grade": final["grade"],
                    "takes": takes, "past": [t for t in takes if t["id"] != final["id"]], "stats": _stats(takes)})
    return out


def build(path, rubric=None):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    humans = _human_latest(path.parent / "human_decisions.jsonl")
    R = rubric or gate.load_rubric()
    floor = R["pass"]["dimension_floor"]
    briefs = []
    for b in data.get("briefs", []):
        takes = []
        for t in b.get("takes", []):
            reviews = t.get("reviews") or ([t["review"]] if t.get("review") else [])
            reviews = _legacy_fidelity(reviews)
            row = {"id": t.get("id"), "attempt": t.get("attempt"), "video": t.get("video"), "note": t.get("note"),
                   "series": t.get("series"), "duration": t.get("duration"), "cost_cny": t.get("cost_cny"),
                   "cost_estimated": t.get("cost_estimated"), "submits": t.get("submits"),
                   "machine_blocking": t.get("machine_blocking") or [], "prompt": t.get("prompt"),
                   "model": t.get("model"), "resolution": t.get("resolution"),
                   "video_route": t.get("video_route"), "route_notice": t.get("route_notice"),
                   "segments": t.get("segments") or [], "segment_decision": t.get("segment_decision")}
            if row["segments"] and row["segment_decision"]:
                explicit = row["segment_decision"]
                merged = gate.merge_judges(reviews) if reviews else {"scores": {}, "issues": [], "vetoes": []}
                row.update(scores=merged.get("scores") or {}, vetoes=explicit.get("vetoes") or [],
                           weighted=explicit.get("weighted"), decision=explicit.get("decision"),
                           stage=explicit.get("stage"), retry_from=explicit.get("retry_from"),
                           reason=explicit.get("reason"), low_dimensions=explicit.get("low_dimensions") or [],
                           issues=[{"cause": i.get("cause"), "at": i.get("at"), "detail": i.get("detail")}
                                   for i in merged.get("issues", []) if i.get("detail")])
            elif row["machine_blocking"] and not reviews:
                row.update(decision="repair", reason="机器检查未通过：" + "；".join(row["machine_blocking"]),
                           vetoes=[], scores={}, issues=[])
            elif not reviews:
                row.update(decision=None, reason="还没有评审结果")
            else:
                merged = gate.merge_judges(reviews)  # 单评委也走同一路径，保留 uncited_low
                res = gate.evaluate(merged, t.get("attempt") or 1, R)
                row.update(scores=merged.get("scores"), vetoes=res.get("vetoes", []),
                           weighted=res.get("weighted"), decision=res.get("decision"),
                           stage=res.get("stage"), retry_from=res.get("retry_from"),
                           reason=res.get("reason"), uncited_low=res.get("uncited_low"),
                           needs_human_look=res.get("needs_human_look", False),
                           issues=[{"cause": i.get("cause"), "at": i.get("at"), "detail": i.get("detail")}
                                   for i in merged.get("issues", []) if i.get("detail")])
            row["grade"] = grade(row, floor)
            row["grade_text"] = GRADE_TEXT[row["grade"]]
            human = humans.get(row["id"])
            row["human"] = ({"decision": human.get("decision"), "reason": human.get("reason"),
                             "decided_at": human.get("decided_at"), "reviewer": human.get("reviewer")}
                            if human else None)
            takes.append(row)
        briefs.append({"id": b.get("id"), "title": b.get("title"), "brief_note": b.get("note"), "takes": takes})
    rule = {"weighted_min": R["pass"]["weighted_min"], "dimension_floor": floor,
            "max_attempts": R.get("max_attempts", 5)}
    all_takes = [t for b in briefs for t in b["takes"]]
    stats = _stats(all_takes)
    stats.update(ledger.usage_by_project(path.parent.name))
    judged = [t for t in all_takes if t.get("human")]
    comparable = [t for t in judged if t.get("decision") and HUMAN_TO_EXPERT.get(t["human"]["decision"])]
    agreed = sum(HUMAN_TO_EXPERT[t["human"]["decision"]] == t["decision"] for t in comparable)
    stats.update(human_count=len(judged), human_compared=len(comparable), human_agreed=agreed,
                 human_agreement_rate=(agreed / len(comparable) if comparable else None))
    shots_path = path.parent / "shots.json"
    shot_list = json.loads(shots_path.read_text(encoding="utf-8")) if shots_path.exists() else None
    return {"rule": rule, "briefs": briefs, "series": build_series(briefs), "stats": stats,
            "grade_text": GRADE_TEXT, "shot_list": shot_list}
