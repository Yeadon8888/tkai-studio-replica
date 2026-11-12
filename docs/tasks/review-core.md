# 任务书 R1：评审改看整条视频 + 生成后必评审 + JSON 解析稳健 + 止损规则统一

分支：`feat/review-core` · 负责人：Codex · 审核：Claude
**前置**：`kit/review_panel.py`、`kit/media_host.py`、`kit/refine_loop.py` 存在；`python3 -m unittest discover -s kit/tests` 在动手前全部通过。不满足就停下说明。

## 背景（运行数据）
- 评审用"产品参考图 + 8 张抽帧"×3 位专家：80 次调用，25 次 524 超时（31%），用掉文本模型 83% 的 token；拆解直接看视频链接，0 失败。
- h-style 的 4 次评审全部"专家输出无法解析"（输出被截断；专家在检查项里写整句话）。
- 抽帧听不到声音：口播测试的 5 条视频无法确认是不是英文。
- 生成接口返回了视频公网地址，但 `loop.cmd_gen` 只用它下载，没有存进任务记录。
- 28 条成片中 16 条从未评审：生成后不会自动评审。
- 轮数上限有三个数字（rubric `max_attempts=5`、`escalate_after_attempts=5`、`.env REFINE_MAX_ATTEMPTS=3`）；"同一缺陷连续两次就停"写在文档里但没有实现；rubric 的 `second_judge` 没有代码读取。

## 要做
1. **保存远程地址**：`loop.cmd_gen` 在成功时把视频的远程地址写进 `jobs/<id>.json`（字段 `remote_url`、`remote_url_at`）。不要打印这个地址。
2. **整条视频评审**：`review_panel.review_take` 改为把整条视频交给模型（`video_url`），同时附产品参考图。
   - 地址来源：优先用任务记录里的 `remote_url`（先用 HEAD 或小范围 GET 确认可访问）；不可用时用 `media_host.upload` 上传，评审结束后 `media_host.delete` 并写 `media_host.log`。
   - `MEDIA_UPLOAD=off` 且没有可用的远程地址时，回退到原来的抽帧方式，评审记录写 `mode: frames`；正常情况写 `mode: video`。
   - 专家提示词（`kit/config/experts.yaml`）：删掉"只看抽帧、听不到声音"的说法；时间码按视频实际时间写；`audio_text` 改为真正听：简报有 `voiceover` 或台词（`voiceover_lines`，可选字段）时，检查是否有人声、语言是否与简报一致、台词是否与简报一致、字幕是否与口播一致。
   - 三位专家的分工、否决项、通过条件不变。
3. **JSON 解析统一**：在 `kit/common.py` 加 `parse_json(text)`（支持代码块围栏、前后多余文字；失败返回 None）。`draft_prompt.py`、`refine_loop.py`、`prompt_review.py`、`review_panel.py` 改用它；`tt_research.parse_json_obj` 改为调用它（保留函数名以兼容）。
   - 用 1 次真实调用测试 TokensFactory 的文本模型是否支持 `response_format: {"type": "json_object"}`。支持就给 `loop.gemini` 加 `json_mode` 参数，评审、起草、修订、拆解默认打开；不支持就在 PR 里写明，不加。
   - 专家检查项（`checks`）只允许"通过 / 失败 / 看不清"，说明文字只写在 `issues` 和 `notes`。
   - 评审输出上限做成 `.env` 的 `REVIEW_MAX_TOKENS`（默认 2000）。
4. **生成后必须评审**：`loop gen` 保存成片后自动评审（参考图取简报的 `images`）；加 `--no-review` 跳过，跳过时在任务记录里写 `review_skipped` 和原因。`loop produce` 不要重复评审。
5. **止损规则只保留一套**：`.env` 新增 `MAX_ATTEMPTS`（默认 3），`gate.py` 与 `refine_loop.py` 都只读它；删除 rubric 里的 `max_attempts`、`escalate_after_attempts`、`second_judge`（保留注释说明已迁移）。`REFINE_MAX_ATTEMPTS` 保留为 `MAX_ATTEMPTS` 的兼容别名或删除，二选一，在 `.env.example` 写清。
   - `refine_loop.run` 实现"同一缺陷连续两次就停"：相邻两轮的失败原因（否决项 + 专家 issues 的 cause）有交集时，结束状态为 `repeat_defect`，不再修订。
6. **文档**：更新 `references/experts.md`、`references/reading-and-loop.md`、`references/configuration.md`、`.env.example`、`SKILL.md` 中关于抽帧、听不到声音、轮数的描述。

## 允许 / 禁止
允许：`kit/loop.py`、`kit/review_panel.py`、`kit/common.py`、`kit/gate.py`、`kit/refine_loop.py`、`kit/draft_prompt.py`、`kit/prompt_review.py`、`kit/tt_research.py`（只改 `parse_json_obj`）、`kit/settings.py`、`kit/media_host.py`、`kit/doctor.py`、`kit/config/experts.yaml`、`kit/config/rubric.yaml`、`kit/tests/test_review_core.py`（新建）、`kit/tests/test_core.py`（只改因本任务失效的断言）、`references/experts.md`、`references/reading-and-loop.md`、`references/configuration.md`、`.env.example`、`SKILL.md`。
禁止：`kit/ledger.py`、`kit/production.py`、`kit/dashboard.py`、`kit/calibrate.py`、`kit/products.py`、`kit/intake.py`、`kit/fission.py`、`references/commands.md`、`VERSION`、`CHANGELOG.md`、`.env`、所有数据目录（另一个任务在改这些文件）。

## 验收
- [ ] 新成片的任务记录里有 `remote_url`；评审记录里有 `mode: video` 或 `mode: frames`。
- [ ] 评审一条成片的模型调用不超过 3 次（不含重试），每次只附参考图和视频地址，不再附 8 张抽帧。
- [ ] 用 `media_host` 上传的临时文件，评审后都已删除（`media_log.jsonl` 里 `deleted: true`）。
- [ ] 四处 JSON 解析只剩 `common.parse_json` 一个实现。
- [ ] `loop gen` 默认自动评审；`--no-review` 有记录。
- [ ] 只有一个轮数上限；`repeat_defect` 有单元测试。
- [ ] 全部测试通过。

## 自测
- `python3 -m unittest discover -s kit/tests`
- 真实调用（必须做，结果写进 PR）：
  - 设置环境变量 `STUDIO_HOME` 为主仓库数据目录（见补充说明），让账本记到主账本。
  - 用复制进来的 `projects/20261112-fission-H-test`（6 条）和 `projects/20261113-voice-test`（5 条）重评。重评前把原 `reviews/` 备份到 `reviews/v2-frames/`。
  - 上限：文本模型调用 ≤ 40 次（含 1 次 json_mode 探测）；`media_host` 上传 ≤ 11 个文件且全部删除；**不发起任何视频生成**。
  - PR 里写一张表：每条成片的旧判定（抽帧）与新判定（整条视频），调用次数、失败次数、token；口播测试 5 条的语言和台词是否被识别。
  - 上游报错不换模型、不批量重试，如实记录。

## 交付
- 自己 `git add -A && git commit`，`git push -u origin feat/review-core`，`gh pr create --base main`
- PR 正文：改了什么、自测结果、对比表、成本、已知问题
- **不要合并，不要 push main**；不要改 `VERSION`、`CHANGELOG.md`
- 密钥只用复制进来的 `.env`，不输出明文
