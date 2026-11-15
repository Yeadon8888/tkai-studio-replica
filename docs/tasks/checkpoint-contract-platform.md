# 任务书 R15-中台：制作台按节点约定 v1 显示与作废

仓库：`content-dashboard-next`（本地 clone，分支 `feat/checkpoint-contract`）· 负责人：Codex · 审核：Claude
**授权边界**：只在本地开发和测试；**不 push、不开 PR、不部署、不远程迁移**；在 clone 里本地 commit。不调用付费模型。先读仓库 `AGENTS.md`、`harness/policies/current.md`，按 harness 要求建 run 留证。
**约定**：`/Users/yeadon_1/Desktop/MyProject/tkAI视频增长中台/studio/docs/tasks/checkpoint-contract.md`（必须先读，以它为准）。执行机一侧由另一个 Codex 同时实现。

## 要做
1. **迁移**（接在 0043 之后）：`production_checkpoints` 增加 `superseded_at`、`superseded_reason`（不重建表、不改 CHECK）。
2. **接口**：`POST /api/agent/production-requests/:id/checkpoints/:externalId/supersede`（`production:run` 权限、组织范围校验、幂等）；`decide` 对已作废节点返回 409 `CHECKPOINT_SUPERSEDED`；节点 DTO 增加 `supersededAt`、`supersededReason`。
3. **脚本节点页面**（`src/v1/production-desk.tsx`）：
   - 列：时长（`durationSeconds`，保留 1 位小数）、画面提示词、口播、字幕（`subtitle`）、**画面文字（`onscreenCopy`，新增）**、声音路线；口播/字幕/画面文字可编辑。
   - 表格上方显示"定场描述"（`scene`）与"配乐风格"（`music` 各字段，中文标签：情绪、速度、年代风格、乐器、起伏）与"对标声音类型"（`audioMode` 中文名）。
   - 提交时 `edits` 只含改过的段与字段，格式按约定。
   - 兼容旧 payload（`duration`、`caption`）用于显示，但不再写旧字段。
4. **预算节点页面**：按约定显示每一行（名称、模型、时长、单价、小计）、合计与请求预算；`totalCny` 为空或超出请求预算时禁止批准并说明原因。
5. **作废显示**：作废节点默认折叠为"已作废（原因）"，不显示操作按钮；列表与"等待人工"计数只算未作废的待处理节点。
6. 测试：扩展 `scripts/test-production-mvp.mjs`：作废接口（正常、幂等、越权、他组织）、决定已作废节点 409、新 payload 字段读写、编辑格式。
7. 浏览器实操（独立端口如 8896）：用约定 v1 的假 payload 建两个脚本节点、作废旧的，截图新列、配乐风格与折叠效果；预算节点未知价格禁止批准。

## 验收
- [ ] `test-production-mvp`、`test:production`、`test:ui`、`npm run build`、`check:bundle` 通过；本地迁移通过。
- [ ] 截图与 harness run 证据；本地 commit；中文汇报 commit、改动、自测、截图位置、风险。
