# 任务书 P1-中台：制作台 MVP（中台一侧）

仓库：`content-dashboard-next`（本地 clone，分支 `feat/production-mvp`）· 负责人：Codex · 审核：Claude
**授权边界**：只在本地开发和测试。**不 push、不开 PR、不部署、不远程迁移**（中台远端禁止推送）。在 clone 里本地 `git commit` 即可，Claude 审核后并回主工作区。不调用付费模型。先读仓库 `AGENTS.md`、`harness/policies/current.md`、`harness/guides/workflow.md`，按其要求建 run、留证据。

## 背景
用户（2026-11-14）："把整个逻辑做成最小 MVP 跑起来，直接在中台上面能够去使用，我想试一试效果。"
studio（Python 复刻流水线，仓库 `Yeadon8888/tkai-studio-replica`）作为中台的"制作执行机"运行在用户 Mac 上：从中台领取制作请求，在中台上报需要人确认的节点，生成成片后作为成片版本上传进中台走现有审核。设计见 `/Users/yeadon_1/Desktop/MyProject/tkAI视频增长中台/studio/docs/platform/integration-design.md`。本任务只做中台一侧；执行机另有任务。

## 要做（最小闭环）
### 1. 数据（新迁移，编号接在现有最后一个之后）
- `production_requests`：`id`、`company_id`、`project_id`、`created_by`、`benchmark_url`、`product_name`、`product_facts`（每行一条）、`product_ref_keys`（R2 私有对象 key 的 JSON，2–3 张产品图）、`tier`（H/M/L）、`budget_cny`、`status`（`queued | claimed | running | waiting_human | uploaded | done | failed | cancelled`）、`stage`、`message`、`runner_id`、`lease_until`、`result_version_id`、`internal_result_json`（神韵分、专家问题、花费、模型；仅内部）、`created_at`、`updated_at`。
- `production_checkpoints`：`id`、`request_id`、`company_id`、`external_id`（执行机给的节点 id，与 request 组成唯一键，保证幂等）、`kind`（`script | budget | other`）、`status`（`pending | approved | rejected | changes_requested`）、`summary`、`payload_json`（脚本逐段内容 / 预算明细，仅内部）、`decision_note`、`edits_json`、`decided_by`、`decided_at`、`created_at`。
### 2. 执行机接口（扩展现有 `ffa_` agent 令牌）
- 新权限 `production:run`；创建令牌时可勾选。服务端按令牌所属用户的组织范围过滤（与现有 `companyScope` 同规则）。
- `GET /api/agent/production-requests?status=queued`
- `POST /api/agent/production-requests/:id/claim`：原子领取，租约 30 分钟；已被别的执行机领取且未过期 → 409；同一执行机重复领取幂等。
- `POST /api/agent/production-requests/:id/progress`：`stage`、`message`、可选 `status`（`running | waiting_human | failed`）；续租。
- `POST /api/agent/production-requests/:id/checkpoints`：按 `external_id` 幂等创建。
- `GET /api/agent/production-requests/:id/checkpoints`：返回各节点状态、原因与修改内容。
- `GET /api/agent/production-requests/:id/refs/:n`：下载产品参考图（私有 R2，按组织鉴权）。
- `POST /api/agent/production-requests/:id/complete`：`version_id`（执行机先用现有 `POST /api/v3/uploads` 上传成片得到）+ `internal_result`；状态 → `uploaded`。
- `GET /api/agent/production-requests/:id/verdict`：该成片版本在现有审核中的状态与驳回原因（让执行机把人工判定回传 studio 做校准）。
### 3. 页面"制作台"（仅 `platform_admin`、`content_operator` 可见；客户角色服务端 403、导航不显示）
- 新建请求：选项目、对标 TikTok 链接、产品名、产品事实（每行一条，提示"只写产品图或说明书上能证实的"）、上传 2–3 张产品图、档位 H/M/L、预算上限（元）。
- 列表：状态、当前阶段、最近消息、执行机、更新时间；空状态说明"需要在 Mac 上运行执行机"并给出命令占位。
- 详情：
  - 脚本节点：逐段表格（时长、画面提示词、口播、字幕、声音路线），口播/字幕可就地修改；"批准 / 要求修改 / 驳回"，后两者必须写原因。
  - 预算节点：每个生成单元的模型、时长、单价、合计、请求预算上限；超出请求预算时禁止批准。
  - 完成后：链接到对应成片版本（现有审核页）；显示内部结果（神韵分逐项、专家问题、花费），标注"仅内部可见"。
- 流域绿、左导航、9:16 视频按现有组件；说明文字不小于 13px。
### 4. 不做
自动发布、客户可见的制作信息、在 Worker 里调用任何模型或 TikHub、新的外部服务。

## 验收
- [ ] 测试脚本（`scripts/test-production-mvp.mjs`，加入 `test:production` 之外的独立命令即可）：正常流程（建请求 → 领取 → 上报节点 → 人工批准 → 完成 → 读取审核结果）、失败（缺字段、预算超限批准被拒、驳回无原因被拒）、重复提交（重复领取、重复建节点幂等）、越权（客户角色、他组织令牌、无 `production:run` 的令牌均被拒）。
- [ ] 本地库迁移通过；`npm run build`、`check:bundle`、现有 `test:ui` 不回归。
- [ ] 浏览器实操截图（本地，用独立端口如 8896，不占用 8886）：管理员新建请求、看到节点、批准脚本与预算；客户账号看不到制作台。截图放 harness run。
- [ ] 在 clone 内本地 commit；最后用中文汇报：commit、改动文件、自测结果、未完成项与风险。
