# 任务书 P1-执行机：studio 从中台领任务并跑完整流水线

分支：`feat/platform-runner` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R13（神韵、配音路线、hydrogen）。中台一侧的接口见 `docs/tasks/platform-production-mvp.md`（中台在本地 `http://127.0.0.1:8886` 运行，接口由另一个 Codex 实现；你用假服务器做单元测试，不依赖它）。本任务不发起付费调用；真实联调由 Claude 做。

## 目标
用户要在中台上直接发起一条复刻、在中台里确认脚本和预算、在中台审核成片。studio 是执行机：领任务 → 用引擎把流水线跑完 → 人工节点上报中台并等待决定 → 成片上传为中台成片版本 → 把中台审核结果回传 studio 做校准。Agent 不参与判断，执行机按引擎推导的下一步走。

## 要做
1. **`kit/platform_client.py`**：中台客户端（标准库 HTTP）。配置 `.env`：`PLATFORM_BASE_URL`（默认 `http://127.0.0.1:8886`）、`PLATFORM_TOKEN_FILE`（存 `ffa_` 令牌的文件，要求权限 600，否则拒绝运行）、`RUNNER_ID`（默认主机名）。封装上述中台接口与 `POST /api/v3/uploads`（先读中台 `cloudflare/api` 里 uploads 的字段要求；不确定的字段停下说明）。超时、5xx 重试一次；令牌永不打印。
2. **`kit/platform_runner.py`**：`--once` 处理一轮；`--loop`（默认 20 秒间隔，Ctrl-C 安全退出）。
   - 领取请求 → 建品（`products.create`，品 id 由请求 id 派生，预算 = 请求预算上限，且不得超过 `.env` 的单品上限，超了就上报失败并说明）→ 下载产品图并登记为产品参考图 → 产品锚点由视觉模型根据产品图和产品名写一句英文外观描述（配置模型、代码校验非空且含产品名）→ 手动对标登记（`intake.py`，对标链接）→ 档位按请求（人已在中台选定，记为 `confirm-tier`，`decided_by: platform-request`）→ `benchmark` 节点本地直接批准并注明依据。
   - 循环 `engine.advance`：
     - 零成本步骤与已批准的付费步骤照常执行，每一步后 `progress` 上报阶段；
     - 遇到 `script` / `budget` 节点 → 上报中台（`external_id` = 本地节点 id，`payload` 带脚本逐段或预算明细），状态 `waiting_human`；之后轮询决定，映射到 `checkpoints.resolve`（含 `edits` → 走 `edit-script` 的校验；校验失败就把错误回写中台并保持待处理）；
     - `verdict` 节点不在中台单独建：成片完成后上传为成片版本 → `complete`（附神韵逐项、专家问题、花费、模型），随后轮询 `verdict` 接口，审核通过/驳回（含原因）写回本地 `verdict` 节点与 `human_decisions.jsonl`。
   - 任何异常：上报 `failed` 与可读原因，本地不自动重提付费任务；在途的视频任务按现有规则先查原任务。
3. **引擎补齐**：确认 `engine.advance` 覆盖完整高质量流水线：镜头表 → 脚本 → 计划 → 定场图（`keyframe.py scene`）→ 生成（含接力）→ 合成（`compose.py`，按 R13 的声音路线）→ 评审（含神韵）。缺的步骤补上（每次只执行一个付费步骤）；预算节点的金额要包含定场图与 TTS。
4. **文档**：`references/platform.md`（怎么在中台建令牌、写 `.env`、启动执行机、常见故障）；`SKILL.md` 增加一节。

## 允许 / 禁止
允许：`kit/platform_client.py`、`kit/platform_runner.py`（新建）、`kit/engine.py`、`kit/checkpoints.py`（不改 resolve 语义）、`kit/products.py`、`kit/intake.py`、`kit/settings.py`、`.env.example`、`kit/tests/**`、`references/platform.md`、`references/workflow.md`、`SKILL.md`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、付费调用、修改中台仓库。

## 验收
- [ ] 单元测试（假中台服务器 + 假步骤）：领取冲突 409、令牌文件权限不对拒绝运行、节点上报幂等、批准/要求修改/驳回的映射、脚本修改校验失败回写、预算超单品上限上报失败、上传后读取审核结果并写回、异常上报 failed。
- [ ] `python3 kit/platform_runner.py --once --dry-run` 对假服务器打印完整的步骤序列。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
