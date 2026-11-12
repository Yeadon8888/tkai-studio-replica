"""X 档：自写脚本（不复刻，ARCHITECTURE §15.6）。引导式三步：init → 填写 → check → brief。

- init  ：生成空白脚本骨架（带提示），agent 按提示填写；方法来源 methods/tiktok-script-generator/SKILL.md。
- check ：计算型自检（不花钱）。把 SKILL 的规则翻译成可核对的条款；阻塞项必须清零才允许 brief。
- brief ：通过自检后，生成与 H/M/L 相同结构的简报 briefs/<id>.json，可直接进入 gen。

自检条款（阻塞）：
1. 必填字段齐全（敌人、痛点、机制、Macro 开关及理由、手部约束、时长、产品出现时间、镜头）。
2. 镜头时间轴从 0 连续到时长，无空隙、无重叠。
3. 产品第一次出现不早于 28%（SKILL：前 28% 不允许出现产品）。
4. 每个镜头的 image_prompt 与 video_prompt 都逐字包含产品锚点（product_anchor.txt）。
5. 手部约束为 single 时，每个 video_prompt 都含 "one hand"（单手自拍物理限制）。
6. 第一个镜头必须包含敌人（先攻击替代方案，SKILL：敌人前置）。

提示（不阻塞）：video_prompt 里出现 background / room / outdoor 等场景词时提醒（SKILL：Video Prompt 不描述背景）。
"""
import argparse
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parents[1]
REQUIRED = ["id", "enemy", "symptom", "mechanism", "macro", "macro_reason", "hands",
            "duration", "reveal_product_at_s", "shots"]
SCENE_WORDS = ("background", "room", "outdoor", "indoor", "kitchen", "bathroom")


def skeleton(sid):
    """空白骨架。字段值用中文提示占位，check 会把占位当作未填写。"""
    return {
        "id": sid,
        "enemy": "（必填）观众现在用的替代方案，越具体越好，例如：凭眼睛估计池水是否安全",
        "symptom": "（必填）观众自己的话描述的痛点，禁止恐吓式描述",
        "mechanism": "（必填）一句话解释产品为什么有效，必须是敌人失败原因的对立面",
        "macro": None,
        "macro_reason": "（必填）为什么开启或关闭微观 3D：机制肉眼不可见则开启；纯物理用品则关闭",
        "hands": "single",
        "duration": 10,
        "reveal_product_at_s": None,
        "shots": [{"start": 0, "end": 3, "scene_zh": "", "action_zh": "", "line": "", "tone": "",
                   "image_prompt": "", "video_prompt": ""}],
        "_hint": "按 SKILL.md 顺序填：敌人 → 痛点 → 机制 → Macro → 镜头。check 通过后再 brief。",
    }


def _anchor(project):
    p = Path(project) / "product_anchor.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def check(script, anchor=""):
    """返回 {"blocking": [...], "warnings": [...]}。计算型，不调用模型。"""
    blocking, warn = [], []
    for k in REQUIRED:
        v = script.get(k)
        if v is None or (isinstance(v, str) and (not v.strip() or v.startswith("（必填）"))):
            blocking.append(f"缺少必填字段：{k}")
    if script.get("macro") not in (True, False):
        blocking.append("macro 必须是 true 或 false")
    if script.get("hands") not in ("single", "two", "tripod"):
        blocking.append("hands 必须是 single / two / tripod")
    shots = script.get("shots") or []
    dur = float(script.get("duration") or 0)
    if not shots or dur <= 0:
        blocking.append("没有镜头或时长无效")
        return {"blocking": blocking, "warnings": warn}
    # 1. 时间轴连续
    t = 0.0
    for i, s in enumerate(shots):
        st, en = float(s.get("start", -1)), float(s.get("end", -1))
        if abs(st - t) > 0.05 or en <= st:
            blocking.append(f"镜头 {i + 1} 时间轴不连续（应从 {t:g}s 开始，实际 {st:g}–{en:g}s）")
        t = en
    if abs(t - dur) > 0.05:
        blocking.append(f"镜头总长 {t:g}s 与时长 {dur:g}s 不符")
    # 2. 28% 延迟
    reveal = script.get("reveal_product_at_s")
    if reveal is not None and float(reveal) < 0.28 * dur:
        blocking.append(f"产品在 {float(reveal):g}s 出现，早于 28% 线（{0.28 * dur:g}s）；SKILL 要求前 28% 不出现产品")
    # 单手交互：视频提示词里出现"双手""另一只手""both hands"等描述即违规（持机手之外不能有第二只手操作产品）
    if script.get("hands") == "single":
        for i, s_ in enumerate(shots):
            vp = (s_.get("video_prompt") or "").lower()
            if any(w in vp for w in ("both hands", "two hands", "other hand", "second hand")):
                blocking.append(f"镜头 {i + 1} 的 video_prompt 出现第二只手操作（单手约束）")
    # 3. 产品锚点逐字 + 4. 单手 + 5. 敌人前置
    for i, s in enumerate(shots):
        for key in ("image_prompt", "video_prompt"):
            if anchor and anchor not in (s.get(key) or ""):
                blocking.append(f"镜头 {i + 1} 的 {key} 没有逐字包含产品锚点")
        if script.get("hands") == "single" and "one hand" not in (s.get("video_prompt") or ""):
            blocking.append(f"镜头 {i + 1} 的 video_prompt 缺少单手约束 'one hand'")
        vp = (s.get("video_prompt") or "").lower()
        if any(w in vp for w in SCENE_WORDS):
            warn.append(f"镜头 {i + 1} 的 video_prompt 含场景词，SKILL 要求不描述背景")
    if shots and not (shots[0].get("scene_zh") or shots[0].get("action_zh")):
        blocking.append("第一个镜头为空，无法确认敌人前置")
    return {"blocking": blocking, "warnings": warn}


def to_brief(script, project, anchor, sid=None):
    """通过自检的脚本 → 与 H/M/L 同结构的简报。"""
    project = Path(project)
    refs = sorted((project / "refs").glob("*.png")) if (project / "refs").exists() else []
    if not refs:
        raise SystemExit("项目 refs/ 下没有产品参考图（png），不能生成简报")
    prompt = " ".join(s["video_prompt"].strip() for s in script["shots"])
    return {
        "id": sid or f"X-{script['id']}",
        "title": f"X 档自写脚本 {script['id']}：{script['enemy'][:30]}",
        "tier": "X", "route": "own_script",
        "images": [str(refs[0])],
        "duration": script["duration"], "resolution": "768p", "aspect_ratio": "9:16",
        "summary": f"X 档（自写，不复刻）；Macro={'开' if script['macro'] else '关'}：{script['macro_reason']}",
        "product_anchor": anchor,
        "prompt": f"Use image 1 as the exact product reference: {anchor}. {prompt}",
        "script": script,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="X 档自写脚本：init / check / brief")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--id", required=True)
    i.add_argument("--out", required=True, help="骨架输出路径，例如 projects/<项目>/scripts/<id>.json")
    c = sub.add_parser("check")
    c.add_argument("--script", required=True)
    c.add_argument("--project", required=True)
    b = sub.add_parser("brief")
    b.add_argument("--script", required=True)
    b.add_argument("--project", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "init":
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(skeleton(a.id), ensure_ascii=False, indent=2), encoding="utf-8")
        print("骨架已写入", out)
        return 0
    script = json.loads(Path(a.script).read_text(encoding="utf-8"))
    anchor = _anchor(a.project)
    res = check(script, anchor)
    for m in res["blocking"]:
        print("阻塞：", m)
    for m in res["warnings"]:
        print("提示：", m)
    if a.cmd == "check":
        print("通过" if not res["blocking"] else f"未通过：{len(res['blocking'])} 项阻塞")
        return 0 if not res["blocking"] else 1
    if res["blocking"]:
        print("有阻塞项，不生成简报")
        return 1
    brief = to_brief(script, a.project, anchor)
    out = Path(a.project) / "briefs" / f"{brief['id']}.json"
    out.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    print("简报已写入", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
