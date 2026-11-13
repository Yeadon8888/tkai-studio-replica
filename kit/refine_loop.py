"""生成 → 审判 → 修订提示词 的循环（一条简报一个循环）。

流程（每一轮 = 一次 attempt）：
  1. 预算检查：products.can_spend（超出品预算则停止，交给人）。
  2. 生成：loop.cmd_gen 用当前提示词提交视频（MiniMax H3），成片落到 takes/<id>.mp4。
  3. 审判：review_panel.review_take（机器检查 + 三位专家 + 闸门）。
  4. 通过 → 结束。升级（escalate）或达到轮数上限 → 交给人。
  5. 不通过 → 把专家的具体问题、闸门的退回原因（rubric causes 的 fix）交给 Gemini，
     让它只修改提示词里对应的部分；修改结果必须通过硬约束检查（产品锚点逐字、长度、结构），否则重试一次。
  6. 用新提示词写下一轮简报，回到第 1 步。

Gemini 只负责"写提示词文字"，不决定通过与否，也不改产品锚点。
每一轮的提示词、判定、问题都写入运行目录的 refine/<基础简报>.jsonl，便于回看。
"""
import hashlib
import json
import re
import sys
from argparse import Namespace
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import products  # noqa: E402

import settings  # noqa: E402
from common import parse_json, read_anchor, read_json, write_json  # noqa: E402

COST_PER_SECOND = settings.video_price()  # 旧测试兼容；真实预算按路线与分辨率计算
MAX_PROMPT_CHARS = 1500


def _fix_hints():
    import yaml
    with open(KIT / "config" / "rubric.yaml", encoding="utf-8") as f:
        causes = yaml.safe_load(f)["causes"]
    return {k: v.get("fix", "") for k, v in causes.items()}


def collect_issues(project, take_id, decision):
    """把专家的具体问题与闸门的退回原因合成一份修改清单。"""
    rec_path = Path(project) / "reviews" / f"{take_id}.json"
    rec = read_json(rec_path, {})
    hints = _fix_hints()
    out = []
    for e in rec.get("experts", []):
        for i in e.get("issues", []) or []:
            out.append({"source": e.get("judge", {}).get("id", "?"), "cause": i.get("cause"),
                        "at": i.get("at"), "problem": i.get("detail"), "fix": hints.get(i.get("cause"), "")})
    for r in (decision or {}).get("repair", []) or []:
        out.append({"source": "gate", "cause": r.get("cause"), "problem": r.get("fix", ""),
                    "fix": hints.get(r.get("cause"), "")})
    return out


def failure_causes(project, take_id, decision):
    """相邻轮比较用：否决项 + 专家 issues 的 cause（无评审文件时兼容 gate repair）。"""
    causes = set((decision or {}).get("vetoes") or [])
    rec_path = Path(project) / "reviews" / f"{take_id}.json"
    if rec_path.exists():
        rec = read_json(rec_path, {})
        for expert in rec.get("experts", []) or []:
            causes.update(i.get("cause") for i in expert.get("issues", []) or [] if i.get("cause"))
    else:
        causes.update(r.get("cause") for r in (decision or {}).get("repair", []) or [] if r.get("cause"))
    return causes


REVISE_TEMPLATE = """你是视频提示词修改员。下面是一条视频生成提示词，以及审判发现的具体问题。
只修改问题涉及的部分，其他保持原样。输出严格 JSON：{{"prompt": "修改后的完整提示词"}}，不要其他文字。

硬约束（违反即作废）：
1. 产品锚点必须逐字保留：{anchor}
2. 固定项（动作、场景、机位、节奏，或简报规定的动作顺序）不得改变：{fixed}
3. 只描述人物与产品的动作与神态，不描述背景；标签上不写文字，除非参考图里有。
4. 保留参考图编号写法（例如 "Use image 1 as the exact product reference"）。
5. 不超过 {max_chars} 个字符。

审判发现的问题（按严重程度排列）：
{issues}

当前提示词：
{prompt}
"""


def build_revise_prompt(prompt, anchor, fixed, issues):
    lines = []
    for i in issues:
        at = f"（时间 {i['at']}）" if i.get("at") else ""
        lines.append(f"- [{i.get('source')}/{i.get('cause')}]{at} {i.get('problem', '')} 修改方向：{i.get('fix', '')}")
    return REVISE_TEMPLATE.format(anchor=anchor, fixed=fixed or "（无）", max_chars=MAX_PROMPT_CHARS,
                                  issues="\n".join(lines) or "（无具体问题，只做整体修改）", prompt=prompt)


def check_revision(new_prompt, anchor):
    """硬约束检查：锚点逐字、长度、非空。返回错误列表。"""
    errs = []
    if not new_prompt or not new_prompt.strip():
        errs.append("提示词为空")
    if anchor and anchor not in (new_prompt or ""):
        errs.append("产品锚点没有逐字保留")
    if len(new_prompt or "") > MAX_PROMPT_CHARS:
        errs.append(f"超过 {MAX_PROMPT_CHARS} 字符")
    return errs


def revise(prompt, anchor, fixed, issues, llm):
    """Gemini 修改提示词；不合格重试一次，仍不合格则返回 (None, 错误)。"""
    last = []
    for _ in range(2):
        text = llm(build_revise_prompt(prompt, anchor, fixed, issues))
        try:
            obj = parse_json(text)
            new = obj["prompt"] if isinstance(obj, dict) else None
            if new is None:
                raise ValueError
        except (ValueError, KeyError):
            last = ["模型输出不是有效 JSON"]
            continue
        last = check_revision(new, anchor)
        if not last:
            return new, []
    return None, last


def brief_duration(brief):
    default = settings.get_float("VIDEO_DURATION")
    segments = brief.get("segments") or []
    if segments:
        return sum(float(s.get("duration", default)) for s in segments)
    return float(brief.get("duration", default))


def generation_duration(brief):
    """复用段不产生新的视频调用，预算只预留本轮实际要生成的秒数。"""
    default = settings.get_float("VIDEO_DURATION")
    segments = brief.get("segments") or []
    if segments:
        return sum(float(s.get("duration", default)) for s in segments if not s.get("reuse_from"))
    return float(brief.get("duration", default))


def generation_cost(brief):
    """本轮实际生成段按各自路线计价；价格未知时停止，不能绕过预算硬检查。"""
    segments = brief.get("segments") or []
    items = [s for s in segments if not s.get("reuse_from")] if segments else [brief]
    total = 0.0
    for item in items:
        route = item.get("video_route", brief.get("video_route", "default"))
        model = settings.video_route_model(route)
        resolution = item.get("resolution", brief.get("resolution", settings.get("VIDEO_RESOLUTION")))
        price = settings.video_unit_price(model, resolution)
        if price is None:
            raise SystemExit(f"{model} / {resolution} 价格未知，无法检查修订预算")
        total += float(item.get("duration", settings.get_float("VIDEO_DURATION"))) * price
    return total


def brief_prompt(brief):
    if brief.get("segments"):
        return "\n".join(s.get("prompt", "") for s in brief["segments"])
    return brief.get("prompt", "")


def _issue_second(at):
    """取时间码的起点秒数；兼容 00:12、00:12–00:14 和 12s。"""
    if not at:
        return None
    match = re.search(r"(?:(\d+):)?(\d+(?:\.\d+)?)", str(at))
    if not match:
        return None
    return int(match.group(1) or 0) * 60 + float(match.group(2))


def segment_for_second(segments, second):
    elapsed = 0.0
    default = settings.get_float("VIDEO_DURATION")
    for index, segment in enumerate(segments):
        elapsed += float(segment.get("duration", default))
        if second < elapsed or index == len(segments) - 1:
            return index
    return None


def affected_segments(segments, issues):
    """有可用时间码时只返回命中的段；没有时间码时保守地修订所有段。"""
    affected = set()
    for issue in issues:
        second = _issue_second(issue.get("at"))
        if second is not None:
            affected.add(segment_for_second(segments, second))
    return affected if affected else set(range(len(segments)))


def run(base_brief, project, pid, refs, gen, review, llm, anchor, fixed="", max_attempts=None, log_dir=None):
    """返回 {"status": succeeded|escalated|repeat_defect|max_attempts|budget_stop|gen_failed|revise_failed, "attempts": [...]}。
    gen(project, brief_path) 与 review(project, take_id, refs) 可注入，便于测试；默认在 main() 里接真实函数。"""
    project = Path(project)
    base = base_brief["id"]
    log = (Path(log_dir) if log_dir else project / "refine") / f"{base}.jsonl"
    brief = dict(base_brief)
    attempts = []
    max_attempts = settings.get_int("MAX_ATTEMPTS") if max_attempts is None else max_attempts
    previous_causes = set()
    for n in range(1, max_attempts + 1):
        tid = f"{base}-a{n}"
        brief["id"] = tid
        cost = generation_cost(brief)
        ok, state = products.can_spend(pid, cost)
        if not ok:
            rec = {"attempt": n, "take": tid, "stop": "budget", "spent": state["spent_cny"], "limit": state["limit_cny"]}
            attempts.append(rec)
            _log(log, rec)
            return {"status": "budget_stop", "attempts": attempts}
        brief_path = project / "briefs" / f"{tid}.json"
        write_json(brief_path, brief)
        gen(project, brief_path)
        if not (project / "takes" / f"{tid}.mp4").exists():
            rec = {"attempt": n, "take": tid, "stop": "gen_failed"}
            attempts.append(rec)
            _log(log, rec)
            return {"status": "gen_failed", "attempts": attempts}
        decision = review(project, tid, refs)
        causes = failure_causes(project, tid, decision)
        rec = {"attempt": n, "take": tid, "prompt_sha256": hashlib.sha256(brief_prompt(brief).encode()).hexdigest()[:12],
               "decision": decision.get("decision"), "weighted": decision.get("weighted"),
               "low": decision.get("low_dimensions"), "vetoes": decision.get("vetoes"),
               "causes": sorted(causes)}
        attempts.append(rec)
        _log(log, rec)
        if decision.get("decision") == "pass":
            return {"status": "succeeded", "attempts": attempts}
        if decision.get("decision") is None or decision.get("decision") == "escalate":
            return {"status": "escalated", "attempts": attempts}
        repeated = sorted(previous_causes & causes)
        if repeated:
            rec2 = {"attempt": n, "take": tid, "stop": "repeat_defect", "causes": repeated}
            attempts.append(rec2)
            _log(log, rec2)
            return {"status": "repeat_defect", "repeat_causes": repeated, "attempts": attempts}
        previous_causes = causes
        if n == max_attempts:
            break
        issues = collect_issues(project, tid, decision)
        segments = brief.get("segments") or []
        if segments:
            affected = affected_segments(segments, issues)
            revised = [dict(segment) for segment in segments]
            for index, segment in enumerate(revised):
                if index not in affected:
                    segment["reuse_from"] = f"{tid}-s{index + 1}"
                    continue
                segment.pop("reuse_from", None)
                local_issues = []
                for issue in issues:
                    second = _issue_second(issue.get("at"))
                    if second is None or segment_for_second(segments, second) == index:
                        local_issues.append(issue)
                new_prompt, errs = revise(segment["prompt"], anchor, fixed, local_issues, llm)
                if new_prompt is None:
                    rec2 = {"attempt": n, "segment": index + 1, "stop": "revise_failed", "errors": errs}
                    attempts.append(rec2)
                    _log(log, rec2)
                    return {"status": "revise_failed", "attempts": attempts}
                segment["prompt"] = new_prompt
            brief["segments"] = revised
            brief["revision_segments"] = sorted(index + 1 for index in affected)
        else:
            new_prompt, errs = revise(brief["prompt"], anchor, fixed, issues, llm)
            if new_prompt is None:
                rec2 = {"attempt": n, "stop": "revise_failed", "errors": errs}
                attempts.append(rec2)
                _log(log, rec2)
                return {"status": "revise_failed", "attempts": attempts}
            brief["prompt"] = new_prompt
        brief["parent"] = tid
        brief["revision_issues"] = issues
    return {"status": "max_attempts", "attempts": attempts}


def _log(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="生成→审判→修订提示词循环")
    ap.add_argument("--brief", required=True, help="基础简报 JSON（含 id、prompt、images、duration）")
    ap.add_argument("--project", required=True, help="运行目录")
    ap.add_argument("--product", required=True, help="品 id（用于预算检查）")
    ap.add_argument("--refs", required=True, help="产品参考图，逗号分隔")
    ap.add_argument("--fixed", default="", help="固定项说明（动作、场景、机位、节奏）")
    ap.add_argument("--attempts", type=int, default=settings.get_int("MAX_ATTEMPTS"))
    a = ap.parse_args(argv)
    import loop
    import review_panel
    project = Path(a.project)
    base = read_json(a.brief, {})
    anchor = read_anchor(project)

    def gen(proj, brief_path):
        loop.cmd_gen(Namespace(project=str(proj), brief=str(brief_path), cap=3, interval=20, max_polls=60,
                               no_review=False, review_skip_reason=""))

    def review(proj, tid, refs):
        decision_path = Path(proj) / "reviews" / f"{tid}.decision.json"
        if decision_path.exists():
            return read_json(decision_path, {})
        return review_panel.review_take(str(proj), tid, refs)

    def llm(prompt):
        return loop.gemini([{"type": "text", "text": prompt}], project=f"refine-{base['id']}", ref="revise",
                           max_tokens=1600, temperature=0.3, role="revise")

    refs = [r for r in a.refs.split(",") if r.strip()]
    res = run(base, project, a.product, refs, gen, review, llm, anchor, a.fixed, a.attempts)
    for r in res["attempts"]:
        print(r)
    print("结束状态：", res["status"])
    return 0 if res["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
