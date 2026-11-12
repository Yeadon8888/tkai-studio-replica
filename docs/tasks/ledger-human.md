# 任务书 R2：账本金额补全 + 人工判定 + 看板显示花费与人工结论

分支：`feat/ledger-human` · 负责人：Codex · 审核：Claude
**前置**：`kit/ledger.py`、`kit/production.py`、`kit/dashboard.py`、`kit/calibrate.py`、`schemas/video_hitl_decision.schema.json` 存在；测试在动手前全部通过。不满足就停下说明。

## 背景（运行数据）
- 视频共提交 24 次、240 秒，按 0.13 元/秒应为约 31.2 元，但账本 `estimate_cny` 合计只有 24.7 元：早期的 `submitted` 行没有金额。
- 账本 221 行里 177 行价格未知（文本模型、TikHub）；看板的"成品单价"只算视频费用。
- 有 1 次重复付费提交（`20261112-tespert-16in1` 的 `TSP-S2`）。
- 人工判定 0 条，`calibrate.py` 从未运行，通过线 8.0 / 6.5 没有任何校准依据；28 条成片 0 条通过。

## 要做
1. **账本回填**：新建 `kit/ledger_backfill.py`。默认只打印（dry-run）：哪些 `submitted` 行缺金额、按 `units × 单价` 回填后各项目的金额。`--apply` 时先备份原文件为 `costs.jsonl.bak-<时间>`，再写回；只补 `estimate_cny` 为空的视频提交行，其它行原样保留。单价用 `ledger.estimate_cny` 的现有逻辑。
2. **账本汇总**：`kit/ledger.py` 加 `usage_by_project(project)`：视频金额、视频秒数、重复提交（同一 ref 提交多于一次）、文本模型调用次数 / 失败次数 / prompt 与 completion token、TikHub 调用次数。
3. **人工判定**：新建 `kit/human.py`：
   - `record --project <运行目录> --take <id> --verdict accept|iterate|redo|hold|abandon|release --note "<一句理由>" [--reviewer <名字>]`：写一行到 `<运行目录>/human_decisions.jsonl`，字段按 `schemas/video_hitl_decision.schema.json` 的必填项（`gate` 默认 `H1`，`expert_decision` 取当时的专家判定）。
   - `list --project <运行目录>`：列出人工判定。
   - 同一条成片可以多次判定，最新一条为准。
4. **校准**：`kit/calibrate.py` 同时读取各运行目录的 `human_decisions.jsonl`（保留原来的 `calibration/human_decisions.jsonl` 兼容），输出一致率、误放行（专家 pass、人工否决）、误拦截（专家 repair、人工 accept）的数量与样本列表。
5. **看板**：
   - `production.build` 读取 `<takes.json 同目录>/human_decisions.jsonl`，给每条成片加 `human`（最新判定、理由、时间）。
   - `production.build` 读取账本（通过 `ledger.usage_by_project`），在统计里加：文本模型调用次数与 token、TikHub 次数、重复提交次数。
   - `dashboard.py` 的生成页：每条成片显示人工结论（没有就显示"未人工判定"）；顶部统计加"人工判定数""专家与人工一致率"（数据不足显示"样本不足"）；花费区分"视频（元）"和"文本模型（次数 / token，价格未配置）"。
6. **文档**：`references/commands.md` 加 `human.py`、`ledger_backfill.py` 的用法；`references/budget-and-queue.md` 说明账本回填与费用口径。

## 允许 / 禁止
允许：`kit/ledger.py`、`kit/ledger_backfill.py`（新建）、`kit/human.py`（新建）、`kit/calibrate.py`、`kit/production.py`、`kit/dashboard.py`、`kit/tests/test_ledger_human.py`（新建）、`kit/tests/test_core.py`（只改因本任务失效的断言）、`references/commands.md`、`references/budget-and-queue.md`。
禁止：`kit/loop.py`、`kit/review_panel.py`、`kit/gate.py`、`kit/refine_loop.py`、`kit/common.py`、`kit/settings.py`、`kit/config/**`、`SKILL.md`、`VERSION`、`CHANGELOG.md`、`.env`、所有数据目录（另一个任务在改这些文件）。

## 验收
- [ ] 对复制进来的账本做 dry-run：回填后视频金额合计等于 240 × 0.13 = 31.2 元（允许 0.01 误差）；PR 里贴 dry-run 输出摘要。**不要对主仓库账本 `--apply`**。
- [ ] `human.py record/list` 可用，写入的行通过 schema 校验（用 `kit/schema.py`）。
- [ ] `calibrate.py` 在有 / 无人工判定时都能运行。
- [ ] 看板生成页显示人工结论、一致率（或"样本不足"）、文本模型次数与 token、重复提交。
- [ ] 全部测试通过，新测试覆盖回填、汇总、人工判定、校准、看板字段。

## 自测
- `python3 -m unittest discover -s kit/tests`
- 用复制进来的 `projects/20261112-fission-H-test` 生成看板：`python3 kit/loop.py assemble --project projects/20261112-fission-H-test && python3 kit/loop.py board --project projects/20261112-fission-H-test`，在 PR 里贴关键统计数字。
- 不需要任何付费调用；不要调用模型或 TikHub。

## 交付
- 自己 `git add -A && git commit`，`git push -u origin feat/ledger-human`，`gh pr create --base main`
- PR 正文：改了什么、自测结果、dry-run 摘要、已知问题
- **不要合并，不要 push main**；不要改 `VERSION`、`CHANGELOG.md`
