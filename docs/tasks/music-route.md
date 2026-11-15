# 任务书 R14：纯音乐对标的配乐路线（不出人声 + Suno 原创配乐 + 曲库复用）

分支：`feat/music-route` · 负责人：Codex · 审核：Claude
**前置**：`main` 含执行机（PR #17）。动手前 `git pull`。**不发起付费调用**（Suno、视频、图片、TTS 都不调）；文本模型 ≤ 10 次。用户 2026-11-15 决定"先修配乐再批预算"。

## 问题（证据）
- 中台第一条真实请求（品 `platform-bf34c96b-e6eb-48bf-8916-ab2d2c51e5c8`，运行 `runs/20261114-platform-fbde4437a2`）：hydrogen 拆出 C023 为 `audio_mode: music_only`，原配乐是有版权的老歌《I Don't Want to Set the World on Fire》，不能使用。
- R13 规则让 `music_only` 走 H3 原生声音；R13 实测 H3 原生声音会出现含混乱码人声（`runs/20261114-r13-verify`）。照现在的规则，成片会是乱码人声 + 没有配乐。
- `compose.make_music` 只是一个 196Hz 正弦波音床，不是音乐。

## 要做
1. **路线**（`shot_plan.py`、`script_writer.py`）：`audio_mode` 为 `music_only` 时：
   - 所有生成单元的 `visual_prompt` 末尾追加固定句：`No dialogue, no speech, no singing, no voice-over; only quiet natural ambient sound.`（代码追加，不靠模型）。
   - 单元 `audio_route: music`。
   - `ambient` 同样追加"不说话"句，但保留原生环境声。
2. **配乐描述**（`script_writer.py`）：脚本模型额外输出 `music`：`{"mood","tempo_bpm","era_style","instruments","energy_curve"}`，只描述风格，**不得出现歌名、歌手名或"像某首歌"的表述**；代码校验（拒绝包含镜头表 `music_sfx` 里出现的歌名/人名的描述，以及 "in the style of"/"like <name>"），不过就按现有规则重写一次。
3. **曲库与生成**（新模块 `kit/music.py`）：
   - 本地曲库 `STUDIO_HOME/assets/music/library.jsonl`：每条记录 `id`、`file`、`style`（上面的结构）、`duration`、`source: suno`、`task_id`、`created`、`license_note`。
   - `pick(style, min_duration)`：按风格字段匹配已有曲目（mood + era_style 相同、时长足够）就复用，不花钱。
   - 没有合适的 → `generate(style)`：TokensFactory `suno_music_open`，`POST /suno/submit/music`，`gpt_description_prompt` 由风格字段拼成（加 "Original instrumental, no imitation of existing songs or artists."），`make_instrumental: true`，`mv: chirp-v5`；提交后立即把 task id 写进运行目录 `jobs/music-<id>.json`；`POST /suno/fetch` 按 ≥20 秒间隔轮询；未知状态先查原任务不重提；成功后下载第一首到曲库并 ffprobe 核验。接口细节以 `~/.claude/skills/tokensfactory-media/references/api.md` 为准。
   - 价格：`.env` `MUSIC_PRICE_CNY`（默认 2.5，按文档"每次创作请求 2.50 元"），记账 `kind: music`。
4. **预算与引擎**（`engine.py`）：`music` 路线的运行，在预算节点的明细里加一行"配乐：复用曲库 0 元 / 新生成 2.5 元"；配乐生成作为一个付费步骤，排在视频生成之后、合成之前，同样要预算批准。
5. **合成**（`compose.py`）：`music` 路线：丢弃 H3 原生音轨，铺配乐（按成片时长裁切、首尾淡入淡出、响度统一到约 -16 LUFS）；有口播时自动压低配乐（现有逻辑）。删除正弦波音床，没有配乐时报错停止，不静默出片。
6. **评审**：V 专家与机器检查在 `music` 路线下检查"不应出现人声"（hydrogen 听写到人声即失败，`cause: audio_unwanted_voice`）。
7. **文档**：`references/long-take.md` 或新 `references/audio.md` 写清四种声音路线（native / tts / music / ambient）与曲库复用；`references/configuration.md` 加 `MUSIC_PRICE_CNY`。

## 允许 / 禁止
允许：`kit/music.py`（新建）、`kit/shot_plan.py`、`kit/script_writer.py`、`kit/compose.py`、`kit/engine.py`、`kit/review_panel.py`、`kit/config/experts.yaml`、`kit/config/rubric.yaml`（如需新 cause）、`kit/settings.py`、`kit/ledger.py`（如需 `kind: music`）、`.env.example`、`kit/tests/**`、`references/**`、`SKILL.md`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、付费调用、改已有运行目录（可以只读用于测试夹具复制）。

## 验收
- [ ] 单元测试：music_only/ambient 的提示词追加句；配乐描述拒绝歌名/人名/"in the style of"；曲库命中复用与未命中生成（假 Suno：提交、排队、成功、失败、未知状态不重提）；预算明细含配乐；合成丢弃原生音轨并铺配乐、无配乐时报错；V 专家 `audio_unwanted_voice`。
- [ ] 对上述真实运行的副本离线跑 `shot_plan`（不调用付费），PR 贴出新计划的路线、追加句与预算明细。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
