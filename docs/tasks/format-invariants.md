# 任务书 R16：守住对标的"形式不变量"，并在花钱前预估神韵

分支：`feat/format-invariants` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R15（PR #21）。动手前 `git pull`。**不发起视频/图片/音乐/TTS 付费调用**；文本模型（`.env` 的 `MODEL_TEXT`）≤ 8 次，只用于第 7 点的实测。不改中台仓库。

## 问题（证据，2026-11-15 审计）
核心考核：H 档对标要还原 ≥80% 神韵。现在的管线在**花钱之前就注定失分**，而且失分来自代码，不是模型：

1. **字幕被强制**：`kit/compose.py` 第 ~33 行 `if not row["caption"]: raise ValueError("缺少字幕文案")`；`script_writer.FIELDS` 也要求 caption。对标 C023（`products/platform-bf34c96b-.../runs/20261114-platform-fbde4437a2/shots.json`）两段 `caption` 都是 `None`——对标没有字幕。旧样片的神韵评分（`products/tespert-16in1/runs/20261114-r13-verify/existing-sample/essence-score.json`）`expert-caption-rhythm` 失败原因正是"对标无连续解说字幕，成片全程配有底部口播字幕"。
2. **钩子的形式丢了**：对标的钩子是 TikTok 原生的"回复评论"贴纸（`onscreen_text`: "Reply to user…'s comment water safer than the ish we drink now 😒😂"）。脚本模型只得到"写 onscreen_copy"，于是写成了一句产品文案 "Is this yellow water safe? Testing 16 water quality parameters now."，合成时也只是顶部一行白字（`make_overlay` kind=copy）。旧样片 `expert-hook` 失败原因："缺少对标的评论贴纸"。
3. **广告化**：`script_writer._prompt` 开头是"你是产品短视频脚本模型"，没有任何"保持对标形式"的约束。当前 v2 脚本：字幕里两次完整产品名、结尾 CTA "check your water today"——对标里都没有。旧样片 `expert-emotion` 失败："平铺直叙的产品说明书质感"。
4. **神韵只能在出片后量**：要花完视频钱才知道 25%。上面三项其实在脚本阶段就能用代码判定。
5. `essence.build_items` 的 `code-narrative-beats` 在 `shots.structure` 为空时文案变成"…一致："（空），评分记 unclear 但不提示原因。

## 要做
1. **形式不变量**（新模块 `kit/format_invariants.py`，纯代码，不调模型）：从 `shots.json` 推出：
   - `captions`: 对标是否有连续字幕（任一 keep/redo 段 `caption` 非空即 true）；
   - `voice`: 是否有人声（沿用 `audio_mode`）；
   - `onscreen_style`: `reply_sticker`（`onscreen_text` 以 Reply to / Replying to 开头，大小写不敏感）/ `text_overlay`（其它非空）/ `none`；同时记录每段是否有屏幕文字、贴纸出现在哪一段；
   - `cta`: 对标的口播/屏幕文字里有没有行动号召（`shop|buy|order|link|get yours|check .* today|tap|click`，可配置在 `kit/config/rubric.yaml`）；
   - `product_mentions`: 对标口播+屏幕文字里提到品名的次数（无品名即 0）。
   结果写进运行目录 `format.json`，并给出中文说明（例："对标无字幕；钩子是回复评论贴纸；无 CTA"）。
2. **脚本模型遵守不变量**（`script_writer.py`）：
   - 提示词开头改为"你是复刻对标的脚本模型：保持对标的形式（字幕有无、屏幕文字形式、有无 CTA、语气），只把对标里的同类产品换成我们的产品"；把 `format.json` 的说明放进提示词。
   - 输出结构：`caption` 在 `captions=false` 时必须为空字符串；`onscreen_copy` 在 `reply_sticker` 时改为对象 `{"style":"reply_sticker","handle":"@<虚构用户名>","comment":"<一句虚构评论>"}`（只出现在对标贴纸所在的段；评论语气对应对标评论的意图，**不得照抄对标评论原文与真实用户名**）；`text_overlay`/`none` 保持字符串/空。
   - 代码校验（阻塞，按现有规则重写）：违反字幕有无；对标无 CTA 而脚本有 CTA；品名出现次数 > 对标次数 + 1；贴纸格式不对；照抄对标评论（与对标 onscreen_text 的词重合率 > 60% 视为照抄）。
   - 兼容：旧脚本（字符串 onscreen_copy）照常读。
3. **合成**（`compose.py`）：
   - 字幕可空：`captions=false` 时不再报错，也不铺字幕；有口播但无字幕允许。
   - `reply_sticker`：在对标贴纸出现的时间段渲染一个通用的"回复评论"贴纸（白色圆角气泡、深色字，一小行 `Reply to <handle>'s comment` + 评论正文，位置接近对标——上 1/3、居中偏左；**不画 TikTok 标志，不用 TikTok 商标字体**）。用现有 overlay 机制生成 PNG 再叠加；Hypit 路径与 ffmpeg 路径都支持。
4. **花钱前的神韵预估**（`essence.py` 新增 `preview(project)`）：
   - 只用代码判定可在脚本阶段判定的项：字幕有无、屏幕文字形式、CTA、品名次数、时长（计划时长 vs 对标）、口播有无、节拍顺序；输出 `essence_preview.json`：`{"items":[...],"pass_count","total","score","blocking":[...]}`。
   - 正式评分（出片后）增加对应的代码项 `code-format-*`，与预估同一套判定；`expert-caption-rhythm` 在对标无字幕时改为代码项"成片无字幕"。
   - `structure` 为空时，`code-narrative-beats` 说明写"对标镜头表缺少节拍结构"，不要输出空串。
5. **上报**：`platform_runner` 上报脚本节点时 payload 增加可选字段 `essencePreview`（`{score, items:[{label, status, detail}]}`，中文标签）与 `format`（第 1 点的中文说明）。约定 v1 允许附加字段，中台暂不显示也不影响；在 `docs/tasks/checkpoint-contract.md` 末尾"附加字段"一节补写这两个字段（只追加，不改已有字段）。
6. **文档**：`references/` 新增或更新一节"形式不变量"，写清它为什么是神韵的地基、有哪些项、怎么校验。
7. **实测**（文本模型 ≤ 8 次）：把上述真实运行目录**复制**到临时 `STUDIO_HOME`，跑 `format_invariants` → 重写脚本 → `essence.preview`。PR 正文贴：`format.json` 说明、新旧脚本对比（字幕、屏幕文字、CTA、品名次数）、新旧预估分。

## 允许 / 禁止
允许：`kit/format_invariants.py`（新建）、`kit/script_writer.py`、`kit/compose.py`、`kit/essence.py`、`kit/platform_runner.py`（只改脚本节点 payload 组装）、`kit/config/rubric.yaml`、`kit/tests/**`、`references/**`、`docs/tasks/checkpoint-contract.md`（只追加）。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、`kit/platform_client.py`、`platform_runner.py` 的轮询/租约/异常处理（另一个任务在改）、中台仓库、付费媒体调用、改真实运行目录。

## 验收
- [ ] 单元测试：四类不变量的推导（含 C023 夹具：无字幕、回复贴纸、无 CTA）；脚本校验各条阻塞；照抄评论判定；compose 无字幕不报错、贴纸叠加时间段正确；`essence.preview` 分数与出片后代码项一致；空 structure 的说明。
- [ ] 第 7 点实测结果贴在 PR。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
- [ ] 交付：自己 commit、push 分支、`gh pr create`；PR 写自测结果与已知问题；不合并、不 push main；不输出密钥。
