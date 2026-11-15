# 任务书 R15-studio：执行机按节点约定 v1 收发

分支：`feat/checkpoint-contract` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R14.2。约定见 `docs/tasks/checkpoint-contract.md`（必须先读）。不发起付费调用，不改中台仓库。中台的作废接口由另一个 Codex 同时实现；你用假服务器测试。

## 要做
1. `platform_runner._script_payload`：按约定 v1 输出（`contractVersion`、`audioMode`、`scene`、`music`、`segments[].index/durationSeconds/subtitle/onscreenCopy`）。
2. `_checkpoint_payload` 的预算部分：从引擎的预算明细生成约定 v1 的 `units`（视频按单元、定场图、TTS、配乐各一行）与 `totalCny`、`requestBudgetCny`；有未知价格时 `totalCny: null`。先读 `engine.py` 里预算 subject 的真实结构，用真实请求运行的副本（`products/platform-bf34c96b-e6eb-48bf-8916-ab2d2c51e5c8`，复制到临时目录）生成一次并在 PR 贴出 payload。
3. 中台 `edits`（对象 `{"segments":[...]}`）→ 本地 `checkpoints.resolve` 需要的编辑列表：按约定映射；批准时带编辑、要求修改时作为意见交给重写（R14.1 已有）。现在的代码把对象当列表传，必须修正并测试。
4. 脚本新版本上报后，对同一请求之前所有待处理的脚本节点调用作废接口（原因"已按意见重写为第 N 版"或"系统补写配乐描述"）；作废接口返回 404（旧中台没有该接口）时只记日志不失败。预算节点内容变化同理。
5. `client.create_checkpoint` 发送 payload 前做一次约定校验（缺字段直接报错，不发出）。

## 允许 / 禁止
允许：`kit/platform_runner.py`、`kit/platform_client.py`、`kit/engine.py`（只读预算明细，必要时补字段）、`kit/tests/**`、`references/platform.md`。
禁止：其它、`VERSION`、`CHANGELOG.md`、`.env`、付费调用、中台仓库。

## 验收
- [ ] 单元测试覆盖 1–5；`python3 -m unittest discover -s kit/tests` 全部通过；PR 贴真实副本生成的脚本与预算 payload。
