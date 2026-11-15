# 任务书 R14.2：执行机租约过期后自动续领，不判失败

分支：`feat/lease-recover` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R14.1（PR #19）。不发起付费调用，不改中台仓库。

## 问题（证据）
真实请求 `bf34c96b-e6eb-48bf-8916-ab2d2c51e5c8`：执行机在等人确认脚本时停机一夜，30 分钟租约过期；重启后第一次 `progress` 得到 `409 {"error":"LEASE_EXPIRED"}`，`run_once` 把它记为 `phase: failed`（本地状态文件，见 `platform_runner._state_root()`），之后再也不处理。中台的 `claim` 已允许同一或任意执行机重新领取状态为 `claimed | running | waiting_human` 且租约过期的请求（`cloudflare/api/production-request-services.mjs` 第 137–143 行）。等人确认本来就可能很久，这不应算失败。

## 要做
1. `platform_client`：任何接口返回 `409 LEASE_EXPIRED` 时，自动 `claim` 一次（同一 `RUNNER_ID`），成功后重试原请求一次；`claim` 返回 `REQUEST_ALREADY_CLAIMED` 则把本地状态标为 `lost`（被别的执行机接走），不再处理，打印原因。
2. `run_once` 启动时：对本地 `phase` 为 `failed` 且 `error` 含 `LEASE_EXPIRED` 的状态，视为可恢复，改回 `running` 并按 1 续领后继续（只处理这一种失败；其它失败保持）。
3. 等人确认期间（`waiting_human`、`waiting_verdict`）每轮循环都调用一次 `progress`（不改阶段文案）以续租，避免正常运行时过期。
4. 测试：假中台返回 LEASE_EXPIRED → 续领 → 重试成功；续领被他人占用 → `lost`；旧的 LEASE_EXPIRED 失败状态被恢复；等待期间续租。

## 允许 / 禁止
允许：`kit/platform_client.py`、`kit/platform_runner.py`、`kit/tests/**`、`references/platform.md`。
禁止：其它文件、`VERSION`、`CHANGELOG.md`、`.env`、付费调用、中台仓库。

## 验收
- [ ] 上述单元测试；`python3 -m unittest discover -s kit/tests` 全部通过。
