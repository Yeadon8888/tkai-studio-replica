"""提示词评审循环：生成 → 计算型自检 → AI 按标准挑毛病 → 修改 → 再检查，最多 N 轮。

为什么要这样做：只靠代码拼接或一次性生成，提示词没有被"读一遍"。这里让模型按 config/prompt_critic.yaml 的条款
逐条挑毛病（每条标 block 或 warn），再按意见修改。计算型检查（own_script.check）每轮都跑，不靠模型。
每轮的挑错与修改都写进 studio/methods/review_log/<id>.jsonl，方便之后回看和校准。

用法：
  python3 prompt_review.py --script <脚本.json> --project <项目目录> [--rounds 3]
不会自动覆盖原脚本：结果写到 <脚本>.reviewed.json，并附上每轮记录。
"""
import argparse
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
STUDIO = KIT.parent
sys.path.insert(0, str(KIT))

import own_script  # noqa: E402

CRITERIA_PATH = KIT / "config" / "prompt_critic.yaml"
LOG_DIR = STUDIO / "methods" / "review_log"


def load_criteria(path=CRITERIA_PATH):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["criteria"]


CRITIQUE_TEMPLATE = """你是提示词质检员。下面是一条视频脚本（JSON），以及必须遵守的标准。
产品锚点（逐字必须一致）：{anchor}

标准（每条都要检查，block 必须修，warn 建议修）：
{criteria}

只依据脚本本身判断，不要补充脚本里没有的东西。输出严格 JSON，不要其他文字：
{{"issues": [{{"id": "标准编号", "severity": "block|warn", "shot": 镜头序号或null, "problem": "哪里不行，具体到字句", "fix": "怎么改"}}],
  "verdict": "pass|revise"}}
没有问题时 issues 为空，verdict 为 pass。

脚本：
{script}
"""

REVISE_TEMPLATE = """下面是一条视频脚本（JSON）和质检员的意见。请只修改意见指出的部分，其他保持不变。
保留 id、duration、hands、reveal_product_at_s 的含义；产品锚点必须逐字保留在每个镜头的 image_prompt 与 video_prompt 中。
输出修改后的完整脚本 JSON，不要其他文字。

意见：
{issues}

脚本：
{script}
"""


def _parse(text):
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    return json.loads(s[start:end + 1])


def review(script, anchor, llm, criteria=None):
    crit = criteria or load_criteria()
    text = "\n".join(f"- [{c['severity']}] {c['id']}：{c['rule']}" for c in crit)
    prompt = CRITIQUE_TEMPLATE.format(anchor=anchor, criteria=text,
                                      script=json.dumps(script, ensure_ascii=False, indent=2))
    return _parse(llm(prompt))


def revise(script, issues, llm):
    prompt = REVISE_TEMPLATE.format(issues=json.dumps(issues, ensure_ascii=False, indent=2),
                                    script=json.dumps(script, ensure_ascii=False, indent=2))
    return _parse(llm(prompt))


def run(script, anchor, llm, rounds=3, criteria=None, log_path=None):
    """循环直到：计算型自检无阻塞项，且模型评审没有 block 级意见；或达到轮数上限。返回 (脚本, 记录)。"""
    history = []
    cur = script
    for r in range(1, rounds + 1):
        det = own_script.check(cur, anchor)
        crit = review(cur, anchor, llm, criteria)
        blocking_ai = [i for i in crit.get("issues", []) if i.get("severity") == "block"]
        rec = {"round": r, "deterministic_blocking": det["blocking"], "deterministic_warnings": det["warnings"],
               "ai_issues": crit.get("issues", []), "verdict": crit.get("verdict")}
        history.append(rec)
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if not det["blocking"] and not blocking_ai:
            return cur, history
        if r == rounds:
            break
        cur = revise(cur, crit.get("issues", []) + [{"id": "deterministic", "severity": "block", "problem": p,
                                                       "fix": "按计算型自检的说明修改"} for p in det["blocking"]], llm)
    return cur, history


def main(argv=None):
    ap = argparse.ArgumentParser(description="提示词评审循环（AI 挑错 + 修改 + 计算型自检）")
    ap.add_argument("--script", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args(argv)
    import loop
    script = json.loads(Path(a.script).read_text(encoding="utf-8"))
    anchor = own_script._anchor(a.project)
    project_name = "prompt-review-" + script.get("id", "script")

    def llm(prompt):
        return loop.gemini([{"type": "text", "text": prompt}], project=project_name, ref="critic", max_tokens=2000)

    out, hist = run(script, anchor, llm, a.rounds, log_path=LOG_DIR / f"{script.get('id', 'script')}.jsonl")
    dst = Path(a.script).with_suffix(".reviewed.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"评审 {len(hist)} 轮 → {dst}")
    print("最终计算型阻塞：", own_script.check(out, anchor)["blocking"] or "无")
    # 注意：最后一轮的模型评审是对"修改前"的脚本做的，所以这里报告的是最后一轮留下的 block 意见，需要人看
    left = [i for i in hist[-1]["ai_issues"] if i.get("severity") == "block"]
    print("最后一轮模型 block 意见（未经再次核对）：", len(left))
    for i in left:
        print("  -", i.get("id"), "|", str(i.get("problem", ""))[:100])
    return 0


if __name__ == "__main__":
    sys.exit(main())
