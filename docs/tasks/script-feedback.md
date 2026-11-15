# 任务书 R14.1：脚本"要求修改"按人的意见重写（人工反馈循环）

分支：`feat/script-feedback` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R14（PR #18）。不发起付费调用；文本模型 ≤ 6 次（只为验证重写）。不改中台仓库。

## 问题（证据）
- `kit/platform_runner.py` 第 ~372 行：中台节点状态不是 `approved` 就 `raise RuntimeError`——人在中台点"要求修改"会让整条请求失败，没有按意见重写的路径。用户要的是"在平台上制作、反馈，进入 human in the loop 循环"。
- `engine._checkpoint` 的 `script` 节点 subject 只有文件路径，脚本内容变了节点 id 不变，人看到的是旧脚本。
- 中台第一条真实请求（品 `platform-bf34c96b-e6eb-48bf-8916-ab2d2c51e5c8`）的脚本是 R14 之前写的，`audio_mode: music_only` 却没有 `music` 风格描述，后续配乐步骤会缺输入。

## 要做
1. **节点随内容变化**：`script` 节点 subject 加 `sha256(script.json)` 前 16 位；`budget` 节点 subject 加预算明细的哈希。内容变 → 新节点；旧的待处理节点在本地标为 `superseded`（`checkpoints` 新增该状态，只能由系统设置，不能由人设置；审计日志写原因）。
2. **要求修改 → 重写**：`script` 节点 `changes_requested` 时：
   - 把人的原因（`note`）和逐段修改（`edits`）作为"人的修改意见"传给 `script_writer.build(..., feedback=...)`，提示词里明确"优先满足这些意见，其他要求不变"；代码校验照旧；
   - 每个运行最多自动重写 3 次（`.env` `SCRIPT_FEEDBACK_MAX=3`），超出则请求失败并说明；
   - 重写出新脚本 → 新节点上报中台（新 `external_id`），`progress` 消息写"已按意见重写第 N 版，旧版作废"；上报的 payload 带 `previousExternalId` 与人的意见原文，便于中台显示对比。
   - `rejected` → 请求 `failed`，原因写人的说明（保持现有行为）。
   - `budget` 节点 `changes_requested` → 请求 `waiting_human` 并提示"请调整请求预算或缩减计划"，不失败；`rejected` → 失败。
3. **缺配乐描述自动补写**：`audio_mode` 为 `music_only` 且 `script.json` 没有合法 `music` 字段时，引擎把阶段判为"脚本待重写"（付费文本步骤，节点为 benchmark 已批准即可执行），重写后走第 1 点的新节点。
4. 每一版脚本保存为 `script.v<N>.json`（`script.json` 指向最新），版本、意见、模型、校验结果写进 `script_history.jsonl`——这是改进脚本模型提示词的数据。
5. 文档：`references/platform.md`、`references/workflow.md` 写清"要求修改"的循环与上限。

## 允许 / 禁止
允许：`kit/platform_runner.py`、`kit/engine.py`、`kit/checkpoints.py`（新增 `superseded`，不改其它 resolve 语义）、`kit/script_writer.py`、`kit/settings.py`、`.env.example`、`kit/tests/**`、`references/platform.md`、`references/workflow.md`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、修改中台仓库、视频/图片/音乐/TTS 付费调用、改写真实运行目录（测试用副本）。

## 验收
- [ ] 单元测试：脚本内容变化产生新节点、旧节点 superseded；changes_requested 带意见重写并上报新节点；重写上限；budget changes_requested 不失败；music_only 缺 music 字段触发重写；版本文件与历史记录。
- [ ] 用真实请求运行的副本（`STUDIO_HOME` 指临时目录）实际调用一次脚本模型，带一条示例意见（"字幕再短一点，每段不超过 8 个词"），PR 贴出新旧脚本对比与校验结果。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
