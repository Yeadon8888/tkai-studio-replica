# 任务书 R10：无证据低分不得判通过；专家先补证据再交人

分支：`feat/gate-evidence` · 负责人：Codex · 审核：Claude
**前置**：`main` 已含 R8。动手前 `git pull`。本任务不发起付费生成；真实文本调用 ≤ 30 次（只用于对已有成片复评验证）。

## 背景（证据）
TESPERT 试样 `products/tespert-16in1/runs/20261113-hq-c023` 的最终判定是 `pass 8.51`，但产品专家给了 product_consistency 5、soul_fidelity 5，人物专家给了 person_consistency 5、visual_quality 5，都没写任何问题。`gate.normalize_review` 把这些无证据低分改成中性 8 分，于是加权被抬到 8.51 判"通过"，只附了 `needs_human_look: True`。专家给低分不写证据，说明模型不确定；把不确定当成 8 分是错的。用户已批准修改此规则（2026-11-13）。

## 要做
1. **先让专家补证据**（`kit/review_panel.py`）：某位专家的结果里有低于 `dimension_floor`（6.5）且无证据的分数时，对这位专家追问一次（同一视频输入），提示词明确列出"你给了 X 维度 N 分但没写问题：请写出带时间码的具体问题，或改分"。追问结果替换原结果；记录 `evidence_retry`（原分、新分、是否补上证据）。每位专家每次评审最多追问一次。
2. **闸门**（`kit/gate.py`）：补问之后仍有 `uncited_low` → 判定 `escalate`（交给人），`reason` 写明哪些维度、哪位专家无证据低分；**不得**判 `pass`。去掉"pass + needs_human_look"的组合。加权计算仍可用中性分，仅用于展示，判定以上述规则为准。
3. **专家提示词**（`kit/config/experts.yaml`）：明确"低于 9 分必须写至少一条带时间码的问题；看不清写'看不清'及时间段"。与 `references/experts.md` 的打分说明保持一致。
4. **看板**（`kit/dashboard.py` 或 `kit/production.py` 中显示判定的地方）：`escalate` 且原因是无证据低分时，显示"需人工看：专家给了低分但没写原因"，并列出维度与专家。
5. **逐段评审汇总**（`aggregate_segment_reviews`）同样适用：任一段落 escalate 时父判定不得为 pass。
6. 用新规则对试样复评一次（`review_panel.py reevaluate` 或等价命令，只读已有成片，写新评审文件，不覆盖旧文件），PR 里贴出新旧判定对比。

## 允许 / 禁止
允许：`kit/gate.py`、`kit/review_panel.py`、`kit/config/experts.yaml`、`kit/config/rubric.yaml`（如需）、`kit/dashboard.py`、`kit/production.py`、`kit/tests/**`、`references/experts.md`、`references/reading-and-loop.md`。
禁止：`kit/shot_list.py`、`kit/shot_plan.py`、`kit/loop.py`、`kit/keyframe.py`、`kit/settings.py`（R9 在改）、`VERSION`、`CHANGELOG.md`、`.env`、付费生成。

## 验收
- [ ] 单元测试：无证据低分触发一次追问；追问补上证据 → 正常判定；追问后仍无证据 → `escalate`；没有低分时不追问；逐段汇总传递 escalate。
- [ ] 试样复评对比贴在 PR。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
