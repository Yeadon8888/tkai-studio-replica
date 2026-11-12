---
name: studio-replica
description: 按品做 TikTok 调研→复刻/自写→生成→评审→看板的完整流程，覆盖一品一文件夹、每品默认 5 元预算（超出先沟通）、按置信度 H/M/L/X 分配条数、调研勾选或口头点名对标、一品多条复刻、多品任务队列。当用户提到要为某个产品做 TikTok 视频、调研对标、复刻某条视频、按档位分配条数、做一批视频、看某个品的进度或预算、新开一个品、或说"选这条来复刻""做 10 条""这个品花了多少"时使用，即使用户没有说"skill"或"studio"。
compatibility: 需要 python3、ffprobe（ffmpeg）、studio/kit 仓库代码；TikHub、TokensFactory 密钥从 ~/.config/tokensfactory/credentials.env 读取（不得打印）。
---

# studio-replica：调研 → 复刻/自写 → 生成 → 评审 → 看板

这个 skill 把一个品从"调研"推进到"成片看板"。代码在 `studio/kit/`（仓库根的 `studio/` 目录），本 skill 只负责告诉你**什么时候用哪条命令、哪些事要先问用户**。

## 先读这些（按需）

- `references/budget-and-queue.md`：预算规则、品目录结构、多品任务队列。
- `references/tiers.md`：H/M/L/X 的定义、条数分配、各档怎么出简报。
- `references/commands.md`：每个子命令的参数与示例。

## 核心原则

1. **一个品一个文件夹。** 所有状态写在 `studio/products/<品id>/`：`product_state.json`（预算、已选对标、运行记录）、`product_anchor.txt`（产品锚点，逐字注入每个镜头）。品卡 `product.json` 是已有约定，不要覆盖。
2. **预算先于花钱。** 每品默认 5 元（TokensFactory 视频，按账本估算）。超过时必须先问用户，得到同意后用 `products.py approve-budget` 登记，并写明理由。TikHub 与 Gemini 不限量，只记账。
3. **先问，后花钱。** 任何会产生视频费用的动作（`gen`）之前，先告诉用户条数、档位分布和预计金额，等确认。拆解（Gemini）与调研（TikHub）不需要逐次确认，但要在汇报里写明调用次数。
4. **复刻与自写分开走。** H/M/L 从对标拆解得到灵魂卡后裂变；X 不参考对标，由 `own_script.py` 按 SKILL 规则自写脚本。
5. **计算先于推断。** 机器检查、脚本自检、闸门能算的先算；只有判断类（如档位、产品匹配）才交给模型。
6. **不宣布完成。** 只有看板与账本都核对过，才说某条视频"已完成"；通过闸门也只是"可交给人看"，最终由人决定。

## 标准流程

按品推进，每一步结束更新队列（`products.py queue --set-stage <品> <阶段>`）：

1. **scope（范围）**：确认品名、产品锚点、预算（默认 5 元）、目标条数。若用户要超过 5 元，先沟通。
2. **research（调研）**：`tt_research.py search` → `filter` → `verify`（只传公开链接，不下载视频）→ `report`。用户可以在看板里勾选，也可以直接说"选 C001、C002"。
3. **select（选对标）**：`products.py select --product <品> --ids C001,C002 --batch <批次>`。同一品可以选多条。
4. **breakdown（拆解）**：`loop.py breakdown --batch <批次> --ids ...`。播放链接过期的候选会被跳过（未计费），需要先用 `search` 重新取链接。
5. **tier（定档）**：`replication.classify()` 给出 H/M/L/X。候选没有拆解时一律显示 X，**这不等于不可复刻**，先补拆解再定档。
6. **allocate（分配）**：`fission.allocate(n, available)`，默认 H:M:L:X = 4:3:2:1。用户指定的目标条数优先。
7. **brief（简报）**：H/M/L 用 `fission.py --bench --dirs --out`；X 用 `own_script.py init → 填写 → check → brief`。
8. **gen（生成）**：先 `products.can_spend` 检查预算，再 `loop.py gen --project <run> --brief <file>`。异步任务会写入 `jobs/`，不要重复提交。
9. **review（评审）**：`loop.py review`（三专家 + 机器检查）。通过不等于交付。
10. **board（看板）**：`loop.py board --project <run>`，生成"调研 + 生成"合一的看板。
11. **done（完成）**：汇报条数、档位、预算已用、未通过原因，请用户决定下一步。

## 多品与任务管理

- 多个品同时进行时，用 `products.py queue` 看全部进度（阶段、状态、备注、更新时间）。
- 一次只对一个品发起付费生成；其他品停在队列里等待。
- 任务卡住（未完成的任务、超时、未知状态）先查原任务，不要盲目重提。

## 不要做的事

- 不要打印、复制或写入任何密钥。
- 不要把视频下载到仓库里（`takes/`、`research/**/videos/` 已在 `.gitignore`）。
- 不要用 Hypit 自带的生成中转或 HypiHub。
- 不要把 X 当作"无法复刻"就跳过：X 是自写脚本，仍然要生成。
- 不要为了凑条数降低通过线；条数不够就如实汇报缺口。

## 同步到 GitHub

代码仓库是私密仓库。推送前运行 `kit/tests`，并确认 `.gitignore` 排除了媒体与密钥。推送属于对外动作，先问用户。
