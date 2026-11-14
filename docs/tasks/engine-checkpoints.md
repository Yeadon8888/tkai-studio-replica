# 任务书 R11a：流程引擎 + 人工确认节点（`studio next`）

分支：`feat/engine-checkpoints` · 负责人：Codex · 审核：Claude
**前置**：`main` 为 0.8.0（含 R9 `script_writer.py`、R10 闸门）。动手前 `git pull`。本任务**不发起任何付费调用**（视频、图片、TTS、文本模型都不调）；全部用假实现测试。另一个 Codex 并行做 R11b（模型目录：`kit/models.py`、`kit/config/models.yaml`、`kit/doctor.py`、`kit/loop.py` 的 `build_video_request`），不要碰这些文件。

## 目标（用户 2026-11-14 批准）
把"下一步做什么"从 Agent 的临场判断，变成代码按数据计算；在要花钱、不可逆、模型没把握的地方停下来等人确认。Agent（Claude 或将来的其它 Agent）只执行 `studio next` 给出的那一步并汇报。人的每次确认都留档，作为后续校准数据。这是将来做平台的核心，保持小：只管状态、确认节点、预算闸，具体步骤仍调用现有模块。

## 要做

### 1. 确认节点 `kit/checkpoints.py`
- 存储：`products/<品>/checkpoints/<id>.json`（当前状态）+ `products/<品>/checkpoints/log.jsonl`（只追加的审计日志）。
- 字段：`id`、`product`、`run`、`kind`、`status`（`pending | approved | rejected | changes_requested`）、`created`、`summary`（给人看的一段话）、`subject`（相关文件路径、金额、视频地址等）、`options`、`decided_by`、`decided_at`、`note`、`edits`。
- `kind` 六种：
  | kind | 何时创建 | 是否阻塞 |
  |---|---|---|
  | `benchmark` | 有调研/手动候选，但未选对标或未确认档位 | 阻塞 |
  | `script` | `script.json`（或 H/M/L 简报）生成且代码校验通过后 | 阻塞，付费生成前最后一关 |
  | `budget` | 生成计划有预计金额后 | 阻塞；若预计金额 ≤ 品剩余预算 且 ≤ `.env` 的 `AUTO_APPROVE_CNY`（默认 0 = 永不自动）则自动批准并在日志写明 |
  | `verdict` | 闸门 `escalate`，或闸门 `pass`（通过只代表可交给人） | 阻塞该条进入"可用" |
  | `publish` | 人判"可用"后 | 阻塞；只记录人是否发布、哪个账号、发布链接（发布本身不自动做） |
  | `review_data` | 预留给 R13 的实发数据回看 | 不阻塞，只通知 |
- API：`create(...)`（同一 product+run+kind+subject 幂等，不重复建）、`pending(product=None)`、`get(id)`、`resolve(id, status, note, by, edits=None)`。驳回与"要求修改"必须写 `note`。
- `script` 节点支持 `edits`：人改了某段的口播/字幕/画面提示词 → 写回 `script.json` 前用 `script_writer.validate` 重新校验，不过就拒绝写入并返回错误；改动前后原文都记进日志（这是改进脚本模型提示词的数据）。
- `verdict` 节点的结果与 `kit/human.py record` 互通：`human.py record` 继续可用，内部改为 resolve 对应节点（没有节点就创建后立即 resolve），保持 `human_decisions.jsonl` 兼容。

### 2. 引擎 `kit/engine.py` + 入口 `kit/studio.py`
- `status(product)`：**由数据推导**阶段，不再依赖手设：产品卡 → 对标与档位 → 计划 → 镜头表 → 脚本 → 预算 → 生成 → 评审 → 人工判定 → 发布。返回当前阶段、下一步动作（具体命令）、阻塞它的节点、品预算余额。`products.py queue --set-stage` 保留为人工备注覆盖，但看板以推导结果为准并显示两者差异。
- `studio next --product <品> [--run <运行>] [--dry-run]`：
  - 下一步需要人确认 → 建节点（幂等），打印"等你确认什么、用什么命令"，退出码 3。
  - 下一步是零成本步骤（拆镜头表除外的本地计算）→ 直接执行。
  - 下一步要调用模型或花钱 → 只有对应节点已批准、且金额不超过批准额度与品剩余预算时才执行；**每次调用最多执行一个付费步骤**；执行后停止并汇报。
  - `--dry-run` 只打印将执行什么、预计金额。
- `studio status [--product]`、`studio pending`、`studio approve <id> [--note]`、`studio reject <id> --note`、`studio changes <id> --note`、`studio edit-script <id> --segment <n> --field voiceover|caption|onscreen_copy|visual_prompt --value ...`。
- 所有输出中文；`--json` 输出机器可读结果（给将来的平台界面和其它 Agent 用）。

### 3. 文档
- 新增 `references/workflow.md`：阶段推导规则、六类节点、自动批准规则、Agent 只能通过 `studio next` 推进。
- `SKILL.md` 流程一节改为："每一步先跑 `python3 kit/studio.py next --product <品>`，按它的输出执行或等人确认；不得跳过节点直接调用付费命令。" `references/commands.md`、`architecture.md` 同步。

## 允许 / 禁止
允许：`kit/checkpoints.py`、`kit/engine.py`、`kit/studio.py`（新建）、`kit/products.py`、`kit/human.py`、`kit/settings.py`（只加 `AUTO_APPROVE_CNY` 一项）、`.env.example`（只加这一项）、`kit/tests/**`、`references/workflow.md`、`references/commands.md`、`references/architecture.md`、`SKILL.md`。
禁止：`kit/models.py`、`kit/config/models.yaml`、`kit/doctor.py`、`kit/loop.py`（R11b 在改）、`VERSION`、`CHANGELOG.md`、`.env`、主仓库数据目录写入、任何付费调用。

## 验收
- [ ] 单元测试：六类节点的创建幂等、resolve 规则（驳回必须写原因）、脚本编辑重新校验失败时拒绝写入、预算自动批准边界（=0 永不、超余额不批）、`next` 在每个阶段的行为（建节点退出码 3 / 执行零成本步骤 / 付费步骤需批准且一次只做一个）、`human.py record` 兼容。
- [ ] 用 `products/tespert-16in1` 的只读副本（复制到临时目录，设 `STUDIO_HOME`）跑 `studio status` 与 `studio next --dry-run`，PR 里贴出推导出的阶段与待确认节点。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
