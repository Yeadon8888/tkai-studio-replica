# 任务书 R11b：模型目录、按需求选模型、模型体检

分支：`feat/model-catalog` · 负责人：Codex · 审核：Claude
**前置**：`main` 为 0.8.0。动手前 `git pull`。另一个 Codex 并行做 R11a（`kit/engine.py`、`kit/checkpoints.py`、`kit/studio.py`、`kit/products.py`、`kit/human.py`），不要碰这些文件。视频只走 TokensFactory（`AGENTS.md`），禁止 AutoDL。真实调用上限：`/v1/models` 不限；文本模型 ≤ 15 次；TTS ≤ 2 次；**禁止视频、图片、音乐生成**。

## 背景
- 模型能力现在散落在三处：`references/video-models.md`、`kit/loop.py build_video_request` 里写死的每条路线的时长/分辨率/图片数限制、`.env` 的 `VIDEO_PRICES`。改一处容易忘另一处。
- 2026-11-13 `gemini-3.8-flash` 悄悄不再读视频并编造评审，靠人发现。模型能力会退化，需要定期实测。
- 用户要求：用 `.env` 自己选模型；不让 AI 临场挑模型（会引入不确定性），改为代码按需求从允许的模型里选，并写明理由。

## 要做

### 1. 模型目录 `kit/config/models.yaml`（唯一来源）
每个模型一条：`id`、`provider`（tokensfactory）、`kind`（text | video | image | tts | music）、能力（文本：`reads_video`、`reads_audio`、`json_reliable`；视频：`durations`（列表或区间）、`resolutions`、`aspects`、`images_min/max`、`audio_max`、`first_last`、`ref_video`、`seed` 范围）、`prices`（元，按分辨率/每秒或每次）、`policy`（`allowed` | `ask_first` | `retired`）、`verified`（日期与依据）、`notes`。
- 种子数据来自 `references/video-models.md`、`~/.claude/skills/tokensfactory-media/references/{models,api}.md` 与账户 `/v1/models`（免费）。不确定的能力写 `null` 并在 notes 说明，**不推测**。
- 必须写入的事实：`gemini-3.8-flash` `reads_video: false`（2026-11-13 实测）；`gemini-3.8-flash-low` 能读视频与声音；MiniMax H3 系列为 `allowed`；`seedance*`、`wan3`、`muse-video` 等非 MiniMax 视频为 `ask_first`；`nano-banana-*`、`veo-3-1`、`veo-omni-flash`、`seedance2.0-900` 为 `retired`；`minimax_h3_zm_titim` 无公开输入格式 → `ask_first` 并注明。

### 2. `kit/models.py`
- `load()`、`get(id)`、`select(kind, need)`：从 `policy=allowed` 且未被体检标记为异常、且在 `.env` 的 `MODELS_ALLOWED`（逗号分隔，留空 = 目录里全部 allowed）中的模型里，挑满足 `need`（如 `{"duration": 12, "resolution": "960p", "first_last": True, "reads_video": True}`）且单价最低的一个；返回 `{"model", "reason", "estimate_cny"}`。没有满足的 → 抛错并列出每个候选不满足的原因；`ask_first` 模型只在 `need` 明确 `allow_ask_first=True`（人已同意）时可选。
- `.env` 里的 `MODEL_*` / `VIDEO_ROUTE_*` 显式指定仍然优先；但被指定的模型若不满足需求，报错而不是静默换。
- `check_request(model, body)`：按目录校验时长、分辨率、图片数、首尾帧、seed 等。`kit/loop.py build_video_request` 改为调用它，删除写死的限制（保持现有路线行为与测试结果不变）；支持 `video_route: auto` + 简报里的需求字段走 `select`。
- 价格：`settings.video_unit_price` 优先读 `.env` 的 `VIDEO_PRICES`（兼容），否则读目录。

### 3. 模型体检 `kit/doctor.py --probe [role|model]`
- 文本：发一个固定小问题，要求严格 JSON，检查能解析。
- 视频理解：用一段固定的公开短视频（优先 `MEDIA_HOST_REPO` 里已有的测试片；没有就用 `media_host` 临时上传一段 2 秒的 ffmpeg 生成测试片，结束删除），检查 `loop.video_ingested` 的 token 判定；同时问一个只有看了画面才答得出的问题（测试片里画一个指定颜色/数字），答错视为没读到。
- TTS：合成一个短句，检查音频时长 > 0。
- 视频/图片生成：只检查 `/v1/models` 是否列出，不生成。
- 结果写 `ledger/probes.jsonl` 与 `STUDIO_HOME/.cache/model_health.json`；失败的模型在 `select` 中被跳过，`doctor` 输出阻塞并提示改 `.env`。体检的文本/TTS 调用记账。
- `references/configuration.md`：增加"模型清单"一节，教用户怎么看目录、怎么在 `.env` 里选、怎么跑体检；`references/video-models.md` 改为从目录生成的说明（或指向目录）。

## 允许 / 禁止
允许：`kit/config/models.yaml`、`kit/models.py`（新建）、`kit/doctor.py`、`kit/loop.py`（只动 `build_video_request` 与价格读取）、`kit/settings.py`（加 `MODELS_ALLOWED`，价格读取）、`kit/media_host.py`（如体检需要）、`.env.example`、`kit/tests/**`、`references/configuration.md`、`references/video-models.md`。
禁止：R11a 的文件、`VERSION`、`CHANGELOG.md`、`.env`、主仓库数据写入、任何视频/图片/音乐生成。

## 验收
- [ ] 单元测试：目录加载与字段校验；`select` 的过滤（policy、白名单、体检异常、需求不满足时的原因列表）与最便宜选择；显式指定不满足需求时报错；`check_request` 覆盖现有路线的全部旧测试；`video_route: auto`；体检结果写入与被跳过。
- [ ] 真实跑一次 `doctor.py --online --probe text,video_read,tts`，PR 贴出结果（`gemini-3.8-flash` 应被判为读不到视频）。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
