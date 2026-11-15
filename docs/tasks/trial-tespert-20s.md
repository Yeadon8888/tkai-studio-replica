# 任务书 T1：用 studio skill 实跑一条 20 秒带货复刻，并记录 skill 的每一处卡点

分支：`trial/tespert-20s` · 负责人：Codex · 审核：Claude
**性质**：这是一次**真实付费试跑**，目的有两个：①按 skill 流程产出一条成片；②把 skill 在真实任务里的问题如实记下来，作为下一轮改进的证据。
**前置**：`main` 最新（含 R16、R16.1）。动手前 `git pull`。先读 `SKILL.md`、`references/workflow.md`、`references/long-take.md`、`references/configuration.md`。

## 输入（只读，不要改）
目录 `/Users/yeadon_1/Desktop/MyProject/tkAI视频增长中台/studio-trials/20261116-tespert-20s/inputs/`：
- `reference.mp4`：对标视频（40 秒竖版，用户判定 H 档）
- `product-images/`：3 张产品图（产品外观的唯一依据）
- `request.md`：用户需求与产品事实（**必须先读，逐条遵守**）

## 运行环境
- `STUDIO_HOME=/Users/yeadon_1/Desktop/MyProject/tkAI视频增长中台/studio-trials/20261116-tespert-20s/home`（所有产品、运行、账本写在这里，**不要写真实 studio 的 products/**）。
- `.env` 已复制到 clone（含密钥，**不得打印、提交或写进日志/PR**）。本次单品预算用环境变量覆盖为 `PRODUCT_BUDGET_CNY=60`（用户已授权 100 美元总额；本试跑上限 60 元）。
- 对标视频要给理解模型读，用 skill 现有的 `media_host`（`MEDIA_UPLOAD`/`MEDIA_HOST_REPO`，临时上传、用完删除并记录）。产品图如需公网地址，同样只能走这个机制并在用完后删除；**不得用其他图床**。

## 流程（按 skill 正常走，Agent 不手写脚本/提示词/口播——这些必须由 skill 配置的模型生成 + 代码校验）
1. 建品（产品名、产品图登记、产品锚点）→ 手动登记对标（本地视频）→ 档位 H（`decided_by: user-2026-11-16`）。
2. 镜头表拆解（`MODEL_TEXT`=gemini-4-hydrogen 读整条视频）。
3. **目标时长 20 秒**（对标 40 秒）：按 skill 现有能力压缩节拍；如果 skill 不支持"目标时长不同于对标"，记为卡点，并用最小改动让它支持（见"允许改"）。
4. 脚本 → 形式不变量与神韵预估 → 计划 → 定场图 → 生成 → 合成 → 评审与神韵评分。
5. **声音**：用户要求出镜英语口播由 MiniMax H3 原生生成，**禁止 TTS**。20 秒必须拆成 ≤15 秒的单元；优先用"硬切"衔接的多个单元各自出原声（不要走 first-last 接力，那条路线会退回 TTS）。如果计划仍给出 tts 路线，停下记为卡点，按上面原则修正计划逻辑后再生成。
6. **人工节点**：脚本与预算节点由你按以下规则代批并注明 `decided_by: codex-trial (user authorized 2026-11-16)`：代码校验通过、神韵预估 ≥ 0.8、预算 ≤ 60 元。不满足就让 skill 自己重写（最多 3 次），仍不满足则停下汇报。**成片 verdict 节点不要代批**，留给人。

## 付费上限（硬性）
- 总花费 ≤ **60 元**（按 skill 账本 `ledger` 记账为准）；视频生成最多 **3 轮**（每轮 = 一整条 20 秒成片所需的全部单元）；文本/视频理解调用 ≤ 40 次。
- 只用 MiniMax H3 系列（`minimax-H3` / `-fast` / `-cinema`），不用其他视频模型；图片用 `gpt-image-2`；不调 Suno（本条是口播，不需要配乐，除非对标拆出确有 BGM 且 skill 判断需要——那也算卡点先记下）。
- 上游报错不要换模型、不要大量重试；异步任务按 skill 规则先查原任务。

## 每轮生成后
跑 skill 的评审与神韵评分。若神韵 < 0.8，按 `essence.retry_from` 指示回到脚本或生成再来一轮（计入 3 轮上限），并在报告里写清每轮改了什么、分数怎么变。

## 记录卡点（本任务最重要的产出之一）
写 `docs/trials/20261116-tespert-20s.md`（提交到分支）：
- 时间线：每一步用的命令、模型、耗时、花费；
- **卡点表**：现象 → 证据（文件/日志行）→ 根因（代码位置）→ 你做的最小修复或建议；
- 每轮的神韵逐项结果（通过/失败/原因）与成片路径；
- 你认为对"还原 80% 神韵"影响最大的 3 个问题。

## 允许 / 禁止
允许：为解除阻塞做**最小**代码修复（`kit/**`、`kit/tests/**`、`references/**`），每个修复要有单元测试；`docs/trials/**`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、真实 studio 的 `products/`、中台仓库、`kit/platform_runner.py`/`platform_client.py`/`checkpoints.py` 的中台相关逻辑（另有任务在改）、绕过代码校验、Agent 手写脚本/口播/提示词。

## 交付
- 成片、各轮素材在 `STUDIO_HOME` 运行目录；报告里写绝对路径。
- `python3 -m unittest discover -s kit/tests` 全部通过。
- commit、push 分支、`gh pr create`（PR 正文：成片路径、神韵分、总花费、卡点摘要、修复列表）；不合并、不 push main；不输出密钥。
