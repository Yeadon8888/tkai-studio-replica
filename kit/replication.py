"""复刻置信度分级。与 VideoRea 的换皮档位对应，并增加一个只提炼逻辑的低置信档。

档位（从高到低）：
- H 高：只替换产品或人物，动作与场景保留（对应 product_only / swap_person）。
     条件：验证 A、主题强词命中、七维"可复制" ≥ 2。
- M 中：保留灵魂卡的 must_keep，场景与动作可换（对应 swap_scene_action）。
     条件：验证 A/B、七维"可复制" ≥ 1。
- L 低：只提炼钩子、证明、行动（CTA）这些逻辑，视觉全部重做（对应 swap_all 的"只剩灵魂"）。
     条件：验证 A/B，或类比品类；七维中钩子/证明/行动 至少两项 ≥ 1。
- X 不复刻：只作研究参考。
类比品类（topic_level=analogy）最高只能到 L；验证 Reject 只表示产品形态不同，不影响 L 判定。
"""

TIER_TEXT = {
    "H": "高置信：只替换产品或人物，动作与场景保留",
    "M": "中置信：保留灵魂卡的必须项，场景与动作可换",
    "L": "低置信：只提炼钩子、证明、行动的逻辑，视觉全部重做",
    "X": "不复刻：仅作研究参考，脚本由方法论库自写（methods/tiktok-script-generator）",
}

# 每档的脚本来源。X 档不复刻视频，只用方法论库与 skill 自写脚本。
SCRIPT_ROUTE = {
    "H": "replicate",
    "M": "replicate",
    "L": "replicate",
    "X": "own_script",
}


def script_route(tier):
    """返回脚本来源：replicate（按灵魂卡复刻）或 own_script（用方法论库自写）。"""
    return SCRIPT_ROUTE.get(tier, "own_script")


def classify(case):
    """case: 调研案例 dict（含 dims、verify、topic_level）。返回 (档位, 理由列表)。"""
    dims = case.get("dims") or {}
    verify = case.get("verify") or {}
    verdict = verify.get("verdict")
    reasons = []
    if not dims:
        return "X", ["没有七维拆解，无法判定"]
    analogy = case.get("topic_level") == "analogy"
    # 类比品类的 Reject 通常是"产品形态不同"，不代表展示语法不可迁移；只要求拆解存在
    # 同类不同形态（验证 product_relation=similar）与类比品类一样：只提炼逻辑，最高 L
    similar_form = verdict == "Reject" and verify.get("product_relation") == "similar"
    analogy = analogy or similar_form
    if not analogy and verdict not in ("A", "B"):
        return "X", [f"模型验证为 {verdict or '未验证'}"]
    hook, proof, cta, copy = dims.get("钩子", 0), dims.get("证明", 0), dims.get("行动", 0), dims.get("可复制", 0)
    if not analogy and verdict == "A" and case.get("topic_level") == "strong" and copy >= 2:
        return "H", ["验证 A、主题强词、可复制 ≥ 2"]
    if not analogy and copy >= 1:
        return "M", [f"可复制 = {copy}，灵魂卡必须项可保留"]
    logic = sum(1 for x in (hook, proof, cta) if x >= 1)
    if logic >= 2:
        extra = "类比品类，只能到低置信" if analogy else "视觉难复刻，只提炼逻辑"
        return "L", [f"钩子/证明/行动 中 {logic} 项有效；{extra}"]
    return "X", [f"可复制 {copy}，钩子/证明/行动 只有 {logic} 项有效，不足以提炼逻辑"]
