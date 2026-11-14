# 任务书 R12.1：确认台验收问题修复

分支：`feat/console-fixes` · 负责人：Codex · 审核：Claude
**前置**：`main` 已含 R12（`kit/console.py`、`kit/engine.py` 的 `adopt`）。动手前 `git pull`。不发起付费调用，不写主仓库数据目录。

## 背景
Claude 用 TESPERT 真实数据副本（`studio adopt` 后）在浏览器验收确认台，发现以下问题。

## 要做
1. **评审重做的文件被忽略**：试样运行 `reviews/` 里有 R10 复评产生的 `hq-c023-r10-evidence-20261113.decision.json`（判定 escalate），但引擎与确认台仍显示旧的 `hq-c023.decision.json`（pass）。
   - 定一个统一约定：同一条成片的多次评审，按评审记录里的 `take` 字段（没有就按文件名前缀）归到同一条成片，**以记录时间最新的为准**，旧的作为历史显示。写进 `references/workflow.md`。
   - `review_panel.reevaluate` 等产生复评的地方，确保写入 `take` 与时间字段（只改写入格式需要的最小部分；如必须改 `review_panel.py`，限于写入字段）。
   - 判定变化（pass → escalate）时，已有的 `verdict` 节点主题随之更新（同一节点更新 subject 并记日志，或旧节点标作废、建新节点，二选一并测试）。
2. **待办不自动出现**：引擎只在 `studio next` 时建节点，确认台首页因此显示"待确认 0"，但阶段已是"人工判定"。确认台加载 `/api/status` 时，对每个品当前阻塞的**确认类**节点做幂等创建（不执行任何模型或付费步骤）。
3. **无证据低分显示成原始 JSON 且只有专家序号**：改成人能读的列表，例如"产品专家：产品一致性 5 分、灵魂还原 5 分——未写原因"。评审记录里存专家 id/名称，展示用名称；旧记录只有序号时按 `experts.yaml` 顺序映射。
4. **对标视频播放器为空**（对标的 TikTok 播放地址已过期）：按顺序尝试——运行目录或调研批次里的本地对标副本 → 可访问的远程地址 → 封面图（`covers/<aweme_id>.jpg`）+ "原链接"按钮 + 说明"播放地址已过期"。不要在页面里请求 TikHub 刷新（付费）；只给一个按钮文案提示用 `covers.py --refresh` 或对应命令。
5. 判定按钮区：显示"闸门判定"与"最新评审时间"，并把成片总时长、费用放在标题旁。

## 允许 / 禁止
允许：`kit/engine.py`、`kit/console.py`、`kit/console/*`、`kit/review_panel.py`（只限第 1 点写入字段）、`kit/checkpoints.py`（只限第 1 点节点主题更新，不改 resolve 语义）、`kit/tests/**`、`references/workflow.md`、`references/console.md`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、付费调用、主仓库数据写入。

## 验收
- [ ] 单元测试：多次评审取最新；判定变化后节点更新；确认台加载即出现确认类待办且不执行付费步骤；无证据低分的可读文案与旧记录映射；对标播放回退顺序。
- [ ] TESPERT 副本：`adopt` → 打开确认台首页即出现 `hq-c023` 判定待办，详情显示 escalate（R10 复评）和可读的无证据说明，对标处显示封面与原链接。PR 贴截图。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
