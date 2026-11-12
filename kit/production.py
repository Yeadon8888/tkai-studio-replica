"""生成数据 → 看板数据。输入宽容：takes.json 只要有 scores（或 reviews）和 vetoes 即可。

takes.json 结构（宽松）：
{"briefs": [{"id": "...", "title": "...", "takes": [
   {"id": "...", "attempt": 1, "video": "...", "note": "...",
    "reviews": [{"scores": {...}, "vetoes": [...], "issues": [...]}, ...]}  // 多个评委
  ]}]}
判定由 kit/gate.py 完成（先合并评委，再套通过条件）。
"""
import json
from pathlib import Path

import gate


def build(path, rubric=None):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    R = rubric or gate.load_rubric()
    briefs = []
    for b in data.get("briefs", []):
        takes = []
        for t in b.get("takes", []):
            reviews = t.get("reviews") or ([t["review"]] if t.get("review") else [])
            row = {"id": t.get("id"), "attempt": t.get("attempt"), "video": t.get("video"), "note": t.get("note")}
            if not reviews:
                row.update(decision=None, reason="还没有评审结果")
            else:
                merged = gate.merge_judges(reviews)  # 单评委也走同一路径，保留 uncited_low
                res = gate.evaluate(merged, t.get("attempt") or 1, R)
                row.update(scores=merged.get("scores"), vetoes=res.get("vetoes", []),
                           weighted=res.get("weighted"), decision=res.get("decision"),
                           stage=res.get("stage"), retry_from=res.get("retry_from"),
                           reason=res.get("reason"), uncited_low=res.get("uncited_low"),
                           needs_human_look=res.get("needs_human_look", False))
            takes.append(row)
        briefs.append({"id": b.get("id"), "title": b.get("title"), "brief_note": b.get("note"), "takes": takes})
    rule = {"weighted_min": R["pass"]["weighted_min"], "dimension_floor": R["pass"]["dimension_floor"],
            "max_attempts": R.get("max_attempts", 5)}
    return {"rule": rule, "briefs": briefs}
