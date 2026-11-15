# 任务书 R16.1：执行机在中台断线时等待重连，不判失败、不退出

分支：`feat/runner-resilience` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R15（PR #21）。动手前 `git pull`。不发起任何付费调用。不改中台仓库。

## 问题（证据）
2026-11-15 本地中台进程停止后，`ledger/platform-runner.log`：
```
请求 bf34c96b-... 失败：PlatformError: 中台连接失败：URLError
执行机失败：中台连接失败：URLError
```
- `Runner.process` 把所有异常都交给 `_fail`：一次网络断线就把本地状态标为 `failed`，并尝试向中台上报 `failed`（中台恰好恢复时会把一条正在等人确认的请求判死）。
- `main` 的 `--loop` 没有捕获 `run_once` 的异常：`client.queued()` 连不上直接让进程退出，人不知道执行机已经停了。

## 要做
1. `platform_client.py`：`PlatformError` 增加 `transient` 属性——连接失败/超时（无 HTTP 状态）与 502/503/504 为 `True`；其余 `False`。现有重试一次的行为保留。
2. `platform_runner.py`：
   - `process`：`transient` 错误不调用 `_fail`，本地状态保持原阶段，写 `lastError`/`lastErrorAt`，打印"中台暂时连不上，稍后重试"；
   - `--loop`：捕获 `run_once` 的所有异常，不退出；`transient` 时按 20 s → 40 s → 80 s … 最长 300 s 退避，恢复后回到 `--interval`；非 transient 的意外异常打印并按 `--interval` 继续；连续断线超过 30 分钟时每 10 分钟打印一次提醒（只打印，不上报）；
   - 恢复连接后：若中台显示该请求已不是本执行机的（`LeaseLostError` 等）照现有 `_lost` 处理。
3. `references/platform.md` 常见故障一节补"中台断线"。

## 允许 / 禁止
允许：`kit/platform_client.py`、`kit/platform_runner.py`（只改 `process`、`_fail` 调用条件、`main` 的循环与新增退避函数）、`kit/tests/**`、`references/platform.md`。
禁止：`platform_runner.py` 中脚本节点 payload 组装（R16 在改）、`VERSION`、`CHANGELOG.md`、`.env`、中台仓库、付费调用。

## 验收
- [ ] 单元测试（假中台）：连接失败不改本地阶段、不上报 failed；`--loop` 遇异常不退出；退避序列 20/40/80/…/300 并在恢复后复位；502/503/504 为 transient、400/409 不是；LeaseLost 仍走 `_lost`。
- [ ] 实测：起一个假中台 → 跑 `--loop` → 关掉假中台 60 秒 → 再打开，日志显示等待与恢复，本地状态未变 failed。PR 贴日志片段。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
- [ ] 交付：自己 commit、push 分支、`gh pr create`；PR 写自测结果与已知问题；不合并、不 push main；不输出密钥。
