"""裂变生成器：一个对标 + 一组方向 → N 条脚本简报（brief）。

不是把对标改写成一条，而是固定对标的核心部分，只在允许的轴上变化。
规则见 ARCHITECTURE.md §15.7。

输入
- benchmark.json：{benchmark_id, tier, fixed:{...}, prompt_fixed, product_anchor, duration, resolution,
                   aspect_ratio, reference_images:[...], constraints:[...], test_only(可选), tier_note}
- directions.json：{n, axes:{轴名: [取值,...]}}，轴名必须在该档允许的变化轴内。

规则
- H：固定 action、scene、camera、rhythm；只允许变化 person、product。
- M：固定 must_keep（3–6 条，写在 benchmark.must_keep）；允许变化 person、product、scene、action、opening、language、camera。
- L：固定 hook、proof、cta 的逻辑（benchmark.logic）；视觉全部重做，允许变化的轴同 M。
- X：不裂变，返回错误（X 档走 own_script，见 §15.6）。
- 档位必须与 replication.classify() 的结果一致；test_only=true 的测试对标除外，并且产出里写明"测试，非真实档位"。

输出（写入 --out 目录）
- F<k>.json：简报，字段与 T01.json 一致（id、title、images、duration、resolution、aspect_ratio、summary、prompt、attempt）
- fission_plan.json：本批的方向、变化轴、费用估算（未调用任何付费接口）
"""
import argparse
import itertools
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replication  # noqa: E402

import settings  # noqa: E402

CNY_PER_SECOND = settings.video_price()      # .env：VIDEO_PRICE_CNY_PER_SECOND
MAX_N = settings.get_int("FISSION_MAX_N")    # 单个对标的单批上限；超过需先确认预算

VARY_ALLOWED = {
    "H": {"person", "product"},
    "M": {"person", "product", "scene", "action", "opening", "language", "camera"},
    "L": {"person", "product", "scene", "action", "opening", "language", "camera"},
}
FIXED_REQUIRED = {
    "H": ["action", "scene", "camera", "rhythm"],
    "M": ["must_keep"],
    "L": ["hook", "proof", "cta"],
}
DEFAULT_CONSTRAINTS = ["No text, no subtitles, no logos"]


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def check_inputs(bench, dirs, tier):
    """检查档位、固定项、变化轴。返回错误列表（空表示通过）。"""
    errs = []
    if tier == "X":
        errs.append("X 档不裂变，应走自建方法库（own_script）")
        return errs
    if tier not in VARY_ALLOWED:
        errs.append(f"未知档位 {tier}")
        return errs
    for k in FIXED_REQUIRED[tier]:
        if not bench.get("fixed", {}).get(k) and not bench.get(k):
            errs.append(f"{tier} 档缺少固定项：{k}")
    bad = [a for a in dirs.get("axes", {}) if a not in VARY_ALLOWED[tier]]
    if bad:
        errs.append(f"{tier} 档不允许变化的轴：{', '.join(bad)}")
    if not dirs.get("axes"):
        errs.append("方向文件没有变化轴")
    if not bench.get("product_anchor"):
        errs.append("缺少产品锚点 product_anchor，无法注入每个镜头")
    return errs


# 按置信度分配一批的总条数：高档多、低档少。默认 H:M:L:X = 4:3:2:1。
# X 不裂变，走自写脚本（own_script.py），不依赖对标，容量不设上限。
TIER_WEIGHTS = settings.tier_weights()  # .env：TIER_WEIGHTS，默认 H:4,M:3,L:2,X:1


def allocate(n, available, weights=None):
    """把 n 条按权重分到 H/M/L。available[档] = 该档可用的对标数；每个对标最多 MAX_N 条。
    返回 (分配 dict, 缺口 int)：缺口为无法分配的条数（所有档都已满）。
    规则：先按权重取整（最大余数法），再把超出容量的部分依次分给仍有空位的档（按权重高低）。"""
    weights = weights or TIER_WEIGHTS
    cap = {t: (n if t == "X" else max(0, int(available.get(t, 0))) * MAX_N) for t in weights}
    total_w = sum(weights.values())
    raw = {t: n * weights[t] / total_w for t in weights}
    alloc = {t: min(int(raw[t]), cap[t]) for t in weights}
    left = n - sum(alloc.values())
    order = sorted(weights, key=lambda t: (-(raw[t] - int(raw[t])), -weights[t]))
    while left > 0:
        progressed = False
        for t in order:
            if left <= 0:
                break
            if alloc[t] < cap[t]:
                alloc[t] += 1
                left -= 1
                progressed = True
        if not progressed:
            break
    return alloc, max(0, left)


def pick_variants(axes, n):
    """按轴取值轮换选出 n 个组合；尽量让相邻组合在每个轴上都不同。"""
    names = list(axes)
    if not names:
        return []
    pools = [axes[a] for a in names]
    combos = list(itertools.product(*[range(len(p)) for p in pools]))
    # 按各轴取值轮换排序：先变最慢的轴，保证前 n 个组合在多个轴上分散
    combos.sort(key=lambda c: tuple(c[i] for i in range(len(c))[::-1]))
    chosen, seen = [], set()
    for c in combos:
        if len(chosen) >= n:
            break
        if c in seen:
            continue
        seen.add(c)
        chosen.append({names[i]: pools[i][c[i]] for i in range(len(names))})
    return chosen


def build_prompt(bench, variant):
    parts = []
    if bench.get("product_anchor"):
        parts.append(f"Use image 1 as the exact product reference: {bench['product_anchor']}.")
    person = variant.get("person")
    if person:
        parts.append(f"The character: {person}.")
    parts.append(bench.get("prompt_fixed", ""))
    if variant.get("scene"):
        parts.append(f"Setting: {variant['scene']}.")
    if variant.get("opening"):
        parts.append(f"Opening line direction: {variant['opening']}.")
    if variant.get("language"):
        parts.append(f"Spoken language: {variant['language']}.")
    cons = bench.get("constraints") or DEFAULT_CONSTRAINTS
    parts.append(", ".join(cons) + ".")
    return " ".join(p.strip() for p in parts if p and p.strip())


def self_check(bench, variants, briefs, tier):
    """文本自检：固定项在每条中保留；产品锚点存在；两两至少一个轴不同；无重复 prompt。"""
    problems = []
    fixed_text = bench.get("prompt_fixed", "")
    if fixed_text and any(fixed_text not in b["prompt"] for b in briefs):
        problems.append("有简报丢失了 prompt_fixed")
    anchor = bench.get("product_anchor", "")
    if anchor and any(anchor not in b["prompt"] for b in briefs):
        problems.append("有简报丢失了产品锚点")
    for i, j in itertools.combinations(range(len(variants)), 2):
        if variants[i] == variants[j]:
            problems.append(f"第 {i + 1} 与第 {j + 1} 条变化轴完全相同")
    if len({b["prompt"] for b in briefs}) != len(briefs):
        problems.append("存在重复 prompt")
    return problems


def fission(bench, dirs, out_dir, n=None):
    tier_now, reasons = (bench.get("tier"), [])
    if not bench.get("test_only"):
        # 真实对标：档位必须由 classify 判定，不能手填
        if bench.get("classified_tier") and bench["classified_tier"] != tier_now:
            return {"ok": False, "errors": [f"档位 {tier_now} 与判定结果 {bench['classified_tier']} 不一致"]}
    errs = check_inputs(bench, dirs, tier_now)
    if errs:
        return {"ok": False, "errors": errs}
    n = n or dirs.get("n") or 3
    if n > MAX_N:
        return {"ok": False, "errors": [f"N={n} 超过单批上限 {MAX_N}；需先确认预算"]}
    variants = pick_variants(dirs["axes"], n)
    if len(variants) < n:
        return {"ok": False, "errors": [f"变化轴的组合只有 {len(variants)} 个，少于 N={n}"]}
    briefs = []
    dur = bench.get("duration", 10)
    for k, v in enumerate(variants, start=1):
        bid = f"{bench['benchmark_id']}-F{k:02d}"
        brief = {
            "id": bid,
            "title": f"{bench['benchmark_id']} 裂变 {k}：" + "，".join(f"{a}={val}" for a, val in v.items()),
            "images": bench.get("reference_images", []),
            "duration": dur,
            "resolution": bench.get("resolution", "768p"),
            "aspect_ratio": bench.get("aspect_ratio", "9:16"),
            "summary": f"档位 {tier_now}；固定：{', '.join(FIXED_REQUIRED[tier_now])}；变化：{', '.join(v)}",
            "prompt": build_prompt(bench, v),
            "attempt": 1,
            "variant": v,
            # 审判与起草都读这两项，不再在专家提示词里写死动作
            "must_keep": bench.get("must_keep") or [x for x in [(bench.get("fixed") or {}).get("action"), (bench.get("fixed") or {}).get("rhythm")] if x],
            "fixed_keywords": bench.get("fixed_keywords", []),
            "voiceover": bool(bench.get("voiceover")),
        }
        if bench.get("test_only"):
            brief["summary"] = "【测试，非真实档位】" + brief["summary"]
        briefs.append(brief)
    problems = self_check(bench, variants, briefs, tier_now)
    cost = round(len(briefs) * dur * CNY_PER_SECOND, 2)
    plan = {
        "benchmark_id": bench["benchmark_id"], "tier": tier_now, "test_only": bool(bench.get("test_only")),
        "n": len(briefs), "varied_axes": sorted(dirs["axes"]), "fixed": FIXED_REQUIRED[tier_now],
        "estimate_cny": cost, "paid_calls_made": 0, "self_check": problems or "通过",
    }
    if problems:
        return {"ok": False, "errors": problems, "plan": plan}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for b in briefs:
        (out / f"{b['id']}.json").write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "fission_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "plan": plan, "briefs": [b["id"] for b in briefs]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="一个对标 + 方向 → N 条脚本简报（不调用付费接口）")
    ap.add_argument("--bench", required=True, help="benchmark.json 路径")
    ap.add_argument("--dirs", required=True, help="directions.json 路径")
    ap.add_argument("--out", required=True, help="简报输出目录（如 项目/briefs）")
    ap.add_argument("--n", type=int, help="覆盖 directions 中的 n")
    a = ap.parse_args(argv)
    res = fission(_load(a.bench), _load(a.dirs), a.out, a.n)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
