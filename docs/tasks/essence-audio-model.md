# 任务书 R13：神韵还原度指标 + 按配音类型选声音 + 主模型换 gemini-4-hydrogen

分支：`feat/essence-audio-model` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R12.2（178 项测试）。动手前 `git pull`。视频只走 TokensFactory（`AGENTS.md`），禁止 AutoDL。
**付费上限（用户 2026-11-14 批准 2 元）**：视频生成合计 ≤ 1.5 元（一条 10 秒 768p H3 = 1.3 元）；TikHub ≤ 3 次；文本模型 ≤ 40 次；TTS ≤ 2 次；禁止图片生成。生成前 `products.can_spend("tespert-16in1", ...)`；品剩余约 5.86 元。

## 用户要求（2026-11-14 原话要点）
1. "我们最重要考核的一个点……给了它对标参考视频，已经达到 H 的置信度了，能不能还原到 80% 的神韵？这个是很关键的一个点。"
2. "TTS……根据我们的对标，如果它是这种话外音，或者是比较特殊的配音，才用它；不然我们默认用 Minimax H3 的音画同步去生成。"
3. "主力的视频语言模型，我想换成 gemini-4-hydrogen。"

## A. 神韵还原度（第一指标）
1. **清单**：新模块 `kit/essence.py`，从对标的 `shots.json` 生成 `essence.json`——需要还原的项目列表，每项有 `id`、`category`、`what`（具体描述，带对标时间码）、`judge`（`code` | `expert`）。
   - 代码判的项：剪辑节奏（我方成片用 ffmpeg 场景切换检测得到切点，与对标镜头边界按时长归一化后比对，容差 ±8% 时长）、总时长（±15%）、叙事节拍顺序（`structure` 的节拍顺序与我方计划一致）。
   - 专家判的项：前三秒钩子机制、景别/机位顺序、每个 `keep` 镜头的关键动作、声音类型与语气、字幕节奏、情绪起伏。项数随对标变化，`keep: redo` 的镜头不进清单（只检查其替代内容是否承担同样的叙事作用）。
2. **评分**：R 专家改为逐项输出 `pass | fail | unclear` + 时间码 + 说明；`essence_score = pass 数 / 总项数`（`unclear` 不计为通过，单独列出）。代码项由代码算。评审记录写 `essence`（逐项结果、分数、未判清的项）。
3. **闸门**（规则变更，已获用户批准）：`kit/config/rubric.yaml` 新增 `essence_min: {H: 0.80, M: 0.60, L: 0.40}`（X 档不适用）。达不到 → `repair`，`retry_from` 按失败项类别：节拍/钩子/台词 → `script`；动作/机位/画面 → `generate`。原 `replication_fidelity` 维度保留作展示，但通过与否以 `essence_score` 为准。
4. **人工校准**：确认台 `verdict` 详情显示清单逐项结果；人可以逐项改判，并回答一问"神韵是否达到 80%"。`calibrate.py` 增加神韵一致率（人判 vs `essence_score ≥ 阈值`）。
5. 档位从对标的档位确认记录读取（`tier_overrides.json` 或计划），读不到就停下，不猜。

## B. 按配音类型选声音
1. `shot_list.py` 顶层新增 `audio_mode`：`on_camera_speech`（出镜说话、口型可见）| `voiceover`（画外音）| `special_voice`（角色音、ASMR、变声等特殊配音）| `music_only` | `ambient`；每个镜头新增 `speaker_on_screen`。校验取值。
2. 路线：`on_camera_speech`、`music_only`、`ambient` → **默认 H3 原生音画同步**：`script_writer` 把台词写进 `visual_prompt`（"The person says in English, in a <语气> tone: '...'"），不另配 TTS；`voiceover`、`special_voice` → TTS（现有路径）。`shot_plan` 每个单元写 `audio_route: native | tts`。`.env` 可用 `AUDIO_ROUTE_DEFAULT` 覆盖（默认 `auto`）。
3. `compose.py`：`native` 单元保留 H3 原声（拼接、响度统一），只叠加字幕与可选音乐（音乐自动压低）；`tts` 单元走现有口播路径。
4. 先确认 `minimax-H3-first-last` 的输出是否带音轨：用仓库已有 first-last 成片（`products/*/runs/*/takes/` 里任务记录模型为 first-last 的）做 ffprobe 与响度检测；若无音轨或静音，接力块不能走 native，计划自动改为 `tts` 并写明原因。
5. 机器检查：`native` 单元音轨不得静音（平均响度 > -50 dB）；V 专家核对说出的台词与脚本一致（已有口播检查，补上 native 模式）。

## C. 主模型换 gemini-4-hydrogen
1. `kit/config/models.yaml` 增加 `gemini-4-hydrogen`（2026-11-14 体检：严格 JSON 通过、视频画面问题答对；推理模型，120 个输出 token 中 117 个是推理，输出上限过小会截断）与 `gemini-4-hydrogen-low`（2026-11-14 两次 503，状态未知）。目录增加 `min_output_tokens` 字段，推理模型设 2000。
2. `loop.gemini` 与 `doctor` 的 `_chat`：实际 `max_tokens` 取 `max(调用方给的值, 模型的 min_output_tokens)`；返回 `finish_reason == "length"` 或只有推理没有正文时，视为截断，按现有重试逻辑放大一次上限重试。`doctor` 体检的默认输出上限改为可配置 `PROBE_MAX_TOKENS=3000`。`video_read` 体检要尊重 `--probe <模型id>`（现在只读 `MODEL_VIDEO_READ_PROBE`）。
3. **切换前的验证**（见 D.2）：只有 hydrogen 在真实对标上读到画面**和声音**，才把 `kit/settings.py` 与 `.env.example` 的 `MODEL_TEXT` 默认值改为 `gemini-4-hydrogen`；否则保持 `gemini-3.8-flash-low` 并在 PR 写明原因。（`.env` 由 Claude 合并后修改，你不要改。）

## D. 真实验证（付费，按上方上限）
1. 刷新 C023 播放地址（TikHub；对标 `https://www.tiktok.com/@mrs.fallout/video/7682136023844818189`，调研批次 `<主仓库上一级>/research/tiktok-benchmark/20261113-US-drinking-water-strips`，只读——刷新结果写到新运行目录，不改批次文件）。
2. 新运行：`products.py new-run --product tespert-16in1 --name r13-verify`。分别用 `gemini-4-hydrogen` 与 `gemini-3.8-flash-low` 跑 `shot_list.py`，对比镜头数、`audio_mode`、口播原文（与旧 `runs/20261113-hq-c023/shots.json` 对照），判断 hydrogen 是否读到声音。PR 贴对比表。
3. 用新清单给已有试样 `runs/20261113-hq-c023/takes/hq-c023.mp4` 算 `essence_score`（只读已有成片，评审写新运行目录）。PR 贴逐项结果。
4. 用新运行的脚本生成**一条** 10 秒 768p `minimax-H3` native 单元（台词写进提示词），检查：有音轨、能听出台词（用 hydrogen 听写并与脚本比对）、口型大致同步（专家判）。PR 贴结果与花费。若 C023 判为 `voiceover`（画外音）就改选一段适合原生说话的测试提示词，仍只生成一条。

## 允许 / 禁止
允许：`kit/essence.py`（新建）、`kit/shot_list.py`、`kit/script_writer.py`、`kit/shot_plan.py`、`kit/compose.py`、`kit/review_panel.py`、`kit/gate.py`、`kit/calibrate.py`、`kit/loop.py`（只限 `gemini()` 的输出上限与截断重试）、`kit/doctor.py`、`kit/models.py`、`kit/settings.py`、`kit/config/{experts,rubric,models}.yaml`、`kit/console.py`、`kit/console/*`、`kit/engine.py`（只为读取档位与神韵结果）、`.env.example`、`kit/tests/**`、`references/**`、`SKILL.md`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、改写已有运行/调研批次文件、AutoDL、图片生成、超出上限的付费调用、付费任务失败后自动重提。

## 验收
- [ ] 单元测试：清单生成（keep/redo）、代码项计算（切点比对、时长、节拍顺序）、R 专家逐项解析、`essence_score` 与档位阈值、`retry_from` 分类、`audio_mode` 校验与路线选择、first-last 无音轨时改走 TTS、native 合成保留原声、输出上限与截断重试、`--probe <模型id>`。
- [ ] D.1–D.4 的结果与总花费贴在 PR。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
