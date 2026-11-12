# Studio 使用说明（闭环）

## 凭据（只从这些位置读取，不写进项目文件）
| 用途 | 位置 | 说明 |
|---|---|---|
| TikHub | `content-dashboard-next/.local-secrets/glumoo-tikhub.env` 的 `TIKHUB_API_KEY` | `tt_research.load_key` 会按顺序查找环境变量、`--env-file`、`~/.config/tikhub/credentials.env`、此文件 |
| TokensFactory（Gemini、gpt-image-2、minimax-H3） | `~/.config/tokensfactory/credentials.env` | `tt.load_tf_env()` 读取；`TOKENSFACTORY_BASE_URL` 末尾的 `/v1` 会自动去掉 |

## 闭环步骤（每一步都记账，都可单独运行）
```bash
S="studio/kit"
B="research/tiktok-benchmark/<批次>"          # 调研批次
P="studio/projects/<项目>"                    # 项目（含 briefs/ takes/ reviews/ jobs/ refs/）

# 1 检索（TikHub，计费）— 写 items_search_<sort>.json
python3 ~/.claude/skills/tiktok-benchmark-research/scripts/tt_research.py search --out "$B" --keywords "$B/keywords.json" --sort relevance --budget-calls 8
python3 ~/.claude/skills/tiktok-benchmark-research/scripts/tt_research.py filter --out "$B"

# 2 验证（Gemini，计费）— 只传公开播放链接，不下载视频
python3 ~/.claude/skills/tiktok-benchmark-research/scripts/tt_research.py verify --out "$B" --top 3 --budget-calls 3

# 3 拆解（Gemini 七维，计费）— 只对 A 组或你选定的条目
python3 $S/loop.py breakdown --batch "$B" --ids C003 --cap 1

# 4 简报：手写 briefs/T01.json（prompt、images、duration、resolution、aspect_ratio、summary）
#   简报只能用产品事实中 certain 的内容；不用拆解判定为 AI 难复刻的动作

# 5 生成（TokensFactory minimax-H3，计费，异步）— 任务号先落盘到 jobs/，轮询 20 秒一次，失败不重提
python3 $S/loop.py gen --project "$P" --brief "$P/briefs/T01.json" --cap 1 --interval 20 --max-polls 60

# 6 评审（Gemini 双评委，计费）— 抽 8 帧 + 参考图 → reviews/
python3 $S/loop.py review --project "$P" --take T01 --refs "$P/refs/product_synth.png" --judges 2

# 7 汇总 → takes.json（闸门在 production.py 判定）
python3 $S/loop.py assemble --project "$P"

# 8 看板（调研 + 生成）
python3 $S/loop.py board --project "$P" --research "$B" --out "$P/board.html"
```

## 闸门与规则
- 规则与预算：`studio/GATES.md`。
- 评分规则：`studio/kit/config/rubric.yaml`（来自电商一致性项目，已与源代码逐条对照，12/12 结果一致）。
- 判定：`studio/kit/gate.py`。结果为 `pass` / `repair`（带 `stage` 与 `retry_from`）/ `escalate`。

## 常见问题
- **链接过期**：TikTok 封面和播放链接约一天失效。拆解或验证前先检查 `media_reachable`，过期就重新 search。
- **检索不够**：每个关键词少于 1 条硬筛通过时，只换一次词，不放宽门槛。
- **生成轮询超时**：任务仍在，先查原任务，不要重新提交。
- **评委给低分但没证据**：按规则视为中性 8.0，并标记"请人看一眼"。
- **预算超出**：脚本直接拒绝；要增加预算，先写进 GATES.md 并告知用户。
