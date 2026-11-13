# 任务书 R9：按连续性分段 + 首尾帧接力 + 由配置模型写脚本

分支：`feat/continuity-script` · 负责人：Codex · 审核：Claude
**前置**：`main` 已含 R8（`kit/shot_list.py`、`kit/shot_plan.py`、`kit/compose.py`）。动手前 `git pull`。视频只走 TokensFactory（见 `AGENTS.md`），禁止 AutoDL。**本任务不发起任何付费视频/图片生成**；文本模型真实调用 ≤ 20 次（只为验证脚本模型输出），全部用离线假实现做单元测试。

## 背景（用户 2026-11-13 的要求）
1. 长镜头、长视频要能复刻。现状：`shot_plan.expand_beats` 把所有镜头硬切成 3–6 秒、每段独立生成，连续动作在接缝处会跳；段与段之间没有交接；所有段只拿到产品图，没有共用场景，试样第 11 秒场景漂移到产品盒内。
2. **尽量剥离 Agent（Claude）本身带来的不确定性**：脚本（每段画面提示词、口播、字幕、画面文字）必须由 skill 在 `.env` 里配置的模型生成、由代码校验，而不是由 Agent 临场手写。现状反例：`kit/shot_plan.py` 里写死了 C023 专用的四个动作阶段（`phases`）和 `rewrite_lines` 的四句英文台词——这是试样时手写进代码的，换个品就错。

方法论参考 Hypit 的视频指导（`~/.claude/skills/hypit/references/playbooks/craft/video-direction.md`、`generated-dependencies.md`）：模型一次能生成多长就一次生成；必须分段时切在动作或机位的自然转折处；用一张定场图让各段处在同一世界；只有确实要求无缝时才用上一段结尾画面做下一段首帧。

## 要做

### 1. 拆解：先整体、再逐段、再分保留与重做（`kit/shot_list.py`）
- 提示词去掉"尽量 3–6 秒"，改为**按真实切镜**输出。
- 顶层新增 `structure`：钩子 / 证明 / 行动等叙事节拍及其起止时间（整体）。
- 每个镜头新增：
  - `join`：与上一镜头的衔接，`cut`（硬切）| `continuous`（同一连续镜头，无切）| `match_cut`（动作匹配切）。第一个镜头写 `cut`。
  - `keep`：`keep`（我方照做）| `redo`（我方换内容，如对标品牌、人物、不适配的动作），以及 `redo_reason`。
  - `end_state`：镜头结束那一刻的画面状态（手、产品、道具的位置），供接力用。
- `validate` 校验以上字段的取值；非法值报错，不静默改写。

### 2. 新模型角色 `script` + 新模块 `kit/script_writer.py`
- `kit/settings.py`：`TEXT_ROLES` 增加 `script`，新增 `MODEL_SCRIPT=""`（留空沿用 `MODEL_TEXT`）；`.env.example` 与 `references/configuration.md` 同步。
- `script_writer.py` 输入：`shots.json`、产品卡/产品事实（`product_state.json` 与 `product_anchor.txt`，以及用户确认的卖点列表）、对标的竞品品牌词（从调研候选的作者、标题、拆解里取；拿不到就空）。
- 让 `MODEL_SCRIPT` 输出 JSON：整体 `scene`（共用场景描述，英文）+ 每个镜头 `visual_prompt`（英文，只描述画面与动作，不要求渲染任何文字）、`voiceover`（我方台词，英文，按节奏改写，不照抄对标）、`caption`、`onscreen_copy`、`end_state`。
- **代码校验（阻塞）**，失败把错误清单回给模型重写一次，仍失败就报错停止，绝不让 Agent 手补：
  - 台词与画面文字里出现的数字和参数，必须能在产品事实里找到（防编造卖点）。
  - 不得出现竞品品牌词；产品只能称为产品锚点里的名字或 "the product"。
  - 每段口播词数 ≤ 该段时长 × 2.6（英文语速上限），且不为空的段数与镜头表 `voiceover` 非空的段数一致。
  - `visual_prompt` 不得要求画字、logo、字幕。
- 输出写 `script.json`，并记录：模型名、提示词哈希、温度、原始输出、校验结果、重写次数。温度默认 0.2。
- CLI：`python3 kit/script_writer.py --project <运行目录> --facts <json 或逗号分隔>`。

### 3. 生成计划：按连续性分组（`kit/shot_plan.py`）
- 删除 `phases` 与 `rewrite_lines` 的写死内容；台词、提示词全部来自 `script.json`。没有 `script.json` 就报错，提示先跑 `script_writer.py`。
- **生成单元** = 连续的 `continuous` 镜头合并成一组；`cut` / `match_cut` 开新组。
- 单元 ≤ 15 秒：一次请求（`quality` 路线 cinema，时长取整 1–15 秒）。
- 单元 > 15 秒：在组内镜头边界处切成若干 ≤ 15 秒的块（尽量均匀，切点优先选镜头边界；单镜头本身 > 15 秒时按时间均分）。第 2 块起走 `keyframe` 路线（`minimax-H3-first-last`，最高 768p）：
  - `first_frame: "chain:prev"` 占位，生成时替换为上一块成片的最后一帧；
  - `last_frame`：由 `keyframe.py` 基于上一块最后一帧 + 本块 `end_state` 用 `MODEL_IMAGE` 编辑出终点画面（生成时现做）。
  - 简报标 `chain_group` 与块序号。
- `match_cut` 的下一单元若是独立请求，也可用 `chain:prev` 做首帧（按镜头表决定，写测试）。
- **共用定场图**：新增 `keyframe.py scene` 子命令，用 `script.json` 的 `scene` + 真实产品图生成一张场景图（`scene.png`），所有 `quality` 单元的 `images[0]` 放它，其后放产品图。没有产品图时报错。
- **固定 seed**：每个运行生成一个 seed（记录在计划里），所有请求带同一个 seed（cinema 允许 0 以上、first-last 需 ≥1，按 `references/video-models.md` 与 tokensfactory-media 文档的约束处理）。
- 计划里写每个单元的预计金额与总额（按 `VIDEO_PRICES`），不在这里花钱。
- 去掉 `validate_durations` 的 3–6 秒限制，改为每个请求 1–15 秒。

### 4. 生成时接力（`kit/loop.py cmd_gen` 的多段分支）
- 无 `chain:prev` 的段保持现有行为。
- 含 `chain:prev` 的段必须在上一段落盘后才提交：用 ffmpeg 取上一段最后一帧（`-sseof`），转 data URI（文档允许首尾帧用 data URI，无需公开上传），再生成 `last_frame`，然后提交。上一段失败则本段不提交并记录原因。
- 每次接力的帧文件存到运行目录 `chain/<段>-first.png`、`<段>-last.png`，任务记录里写来源。
- 不同分辨率（960p 与 768p）的段在拼接前统一缩放到成片分辨率（若已有逻辑就复用）。

### 5. 文档
- 新增 `references/long-take.md`：长镜头复刻方法论（拆解字段、分组规则、接力流程、定场图、seed、预算估算、已知风险：15 秒后段稳定性与多次接力后的细节漂移尚未实测）。
- `SKILL.md` 的"高质量逐镜头复刻"一节与 `references/architecture.md`、`commands.md` 同步；在"必须遵守"加一条：**脚本、提示词、口播、字幕由 `script_writer.py` 用配置模型生成并经代码校验，Agent 不手写、不手改；校验不过就停下报告，由人决定。**

## 允许 / 禁止
允许：`kit/shot_list.py`、`kit/shot_plan.py`、`kit/script_writer.py`（新建）、`kit/keyframe.py`、`kit/loop.py`（只动多段生成与接力）、`kit/compose.py`（只为读 `script.json` 的字幕与口播）、`kit/settings.py`、`.env.example`、`kit/tests/**`、`references/**`、`SKILL.md`。
禁止：`kit/gate.py`、`kit/review_panel.py`、`kit/config/*.yaml`（另一任务 R10 在改）、`VERSION`、`CHANGELOG.md`、`.env`、数据目录写入、任何付费生成。

## 验收
- [ ] 单元测试：拆解新字段校验；`script_writer` 的四条阻塞校验与重写一次；分组（cut/continuous/match_cut、≤15 秒、>15 秒切块、单镜头 >15 秒）；`chain:prev` 的顺序提交与上一段失败时不提交；定场图放在 `images[0]`；seed 写入请求；预计金额。
- [ ] 用 TESPERT 的 `products/tespert-16in1/runs/20261113-hq-c023/shots.json`（只读，复制到临时目录）离线跑一遍新计划，PR 里贴出单元划分与预计金额。
- [ ] 真实文本模型调用一次 `script_writer`（同一临时目录，≤ 20 次文本调用），PR 里贴出 `script.json` 摘要与校验结果。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
