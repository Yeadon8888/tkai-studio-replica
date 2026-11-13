"""H/M/L 初稿：代码拼出的骨架 → Gemini 起草（产品框定 + 适配改写）。

为什么：对标视频的产品不一定就是我们的产品。代码只能拼固定项，无法判断"这个对标的产品能不能换成我们的"，
也无法把对标里的动作改写成我们产品能做到的样子。所以初稿由 Gemini 起草，代码保留骨架并做硬约束检查。

流程：
  1. 代码骨架：fission 给出的 baseline 提示词（固定项原样、变化项已替换）。
  2. Gemini 做两件事：
     a. 适配判断：对标产品与我方产品是否同类、形态是否接近。输出 fit = fit | adapt | unfit。
     b. 起草：只改产品相关的描述（标签、颜色、形态、试纸），让动作与固定项保持不变。
  3. 硬约束检查（代码）：锚点逐字、固定项关键词仍在、长度、非空。不过则重试一次。
  4. fit = unfit 时不生成，标记给人。

输出写回简报：prompt（草稿）、prompt_baseline（代码骨架，留作对照）、draft_fit、draft_notes。
"""
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from refine_loop import check_revision, MAX_PROMPT_CHARS  # noqa: E402
from common import parse_json, read_anchor  # noqa: E402

DRAFT_TEMPLATE = """你是视频提示词起草员。目标：为我方产品写一条 AI 视频生成的提示词（英文，给 MiniMax H3）。

我方产品锚点（必须逐字保留）：{anchor}
对标视频的拆解（只作参考，产品可能不同）：
{benchmark}
固定项（动作、场景、机位、节奏）：{fixed}
本条的变化项（人物等）：{variant}
代码骨架（作为起点，结构保留）：
{baseline}

请完成两件事，输出严格 JSON：
{{"fit": "fit|adapt|unfit",
  "fit_notes": "对标产品与我方产品的差异，以及是否可以用对标的动作完成我方产品的展示",
  "prompt": "改写后的完整提示词"}}

规则：
1. 产品锚点逐字保留；标签不写文字（除非锚点说有）。
2. 固定项的动作顺序不变；如果对标动作在我方产品上做不到（例如形态不允许蘸水），在 fit_notes 里说明，并在 prompt 里写出最接近的可执行动作，fit 标为 adapt。
3. 只改产品相关的描述，不改人物与场景的已有设定。
4. 不描述背景之外的新场景；不加字幕、logo。
5. 不超过 {max_chars} 字符。
"""



def build(brief, anchor, benchmark_text, fixed_text, variant_text):
    return DRAFT_TEMPLATE.format(anchor=anchor, benchmark=benchmark_text or "（无）", fixed=fixed_text or "（无）",
                                 variant=variant_text or "（无）", baseline=brief.get("prompt_baseline") or brief.get("prompt", ""),
                                 max_chars=MAX_PROMPT_CHARS)


def check_draft(prompt, anchor, baseline_prompt, keywords=()):
    """硬约束：基础检查 + 简报给出的固定项关键词（fixed_keywords）在骨架里出现过的，草稿里也要保留。
    关键词来自对标/简报，不写死在代码里；没有给关键词时只做基础检查。"""
    errs = check_revision(prompt, anchor)
    base_hits = {k.lower() for k in keywords if k.lower() in (baseline_prompt or "").lower()}
    lost = [k for k in base_hits if k not in (prompt or "").lower()]
    if lost:
        errs.append("固定项关键词丢失：" + ", ".join(sorted(lost)))
    return errs


def draft(brief, anchor, benchmark_text, fixed_text, variant_text, llm):
    baseline = brief.get("prompt_baseline") or brief.get("prompt", "")
    prompt = build(dict(brief, prompt_baseline=baseline), anchor, benchmark_text, fixed_text, variant_text)
    last = []
    for _ in range(2):
        try:
            js = parse_json(llm(prompt))
            if not isinstance(js, dict):
                raise ValueError
        except (ValueError, KeyError):
            last = ["模型输出不是有效 JSON"]
            continue
        last = check_draft(js.get("prompt", ""), anchor, baseline, brief.get("fixed_keywords") or ())
        if not last:
            out = dict(brief, prompt=js["prompt"], prompt_baseline=baseline,
                       draft_fit=js.get("fit", "adapt"), draft_notes=js.get("fit_notes", ""))
            if out["draft_fit"] not in ("fit", "adapt", "unfit"):
                out["draft_fit"] = "adapt"
            return out, []
    return None, last


def main(argv=None):
    import argparse
    import loop
    ap = argparse.ArgumentParser(description="H/M/L 初稿：Gemini 起草提示词（含产品适配判断）")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--benchmark", help="benchmark.json，用于提供对标的固定项与拆解")
    a = ap.parse_args(argv)
    brief = json.loads(Path(a.brief).read_text(encoding="utf-8"))
    project = Path(a.project)
    anchor = read_anchor(project)
    bench = json.loads(Path(a.benchmark).read_text(encoding="utf-8")) if a.benchmark else {}
    bench_text = json.dumps({k: bench.get(k) for k in ("source", "tier", "fixed") if k in bench}, ensure_ascii=False)
    fixed_text = json.dumps(bench.get("fixed", {}), ensure_ascii=False)
    variant_text = json.dumps(brief.get("variant", {}), ensure_ascii=False)

    def llm(prompt):
        return loop.gemini([{"type": "text", "text": prompt}], project=f"draft-{brief['id']}", ref="draft",
                           max_tokens=1600, temperature=0.3, role="draft")

    out, errs = draft(brief, anchor, bench_text, fixed_text, variant_text, llm)
    if out is None:
        print("草稿未通过硬约束：", errs)
        return 1
    if out["draft_fit"] == "unfit":
        print("适配判断为 unfit：不生成，交给人。说明：", out["draft_notes"])
        return 2
    dst = Path(a.brief)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"草稿已写入 {dst}（fit={out['draft_fit']}）")
    print("说明：", out["draft_notes"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
