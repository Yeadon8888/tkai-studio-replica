# 任务书 R5：产品首帧路线（真实产品图 → 首帧 → 图生视频），与纯提示词路线做对比

分支：`feat/keyframe` · 负责人：Codex · 审核：Claude
**前置**：`main` 已包含 R1（评审看整条视频）。动手前确认 `grep -n "remote_url" kit/loop.py` 有结果；否则停下说明。读 `~/.claude/skills/tokensfactory-media/SKILL.md` 与它的 references 了解 `gpt-image-2` 的编辑接口（multipart `POST /v1/images/edits`）。

## 背景
- 失败原因里"产品细节错误"（11 条问题、7 次否决）和"乱码字"（7 条问题、6 次否决）占绝大多数。提示词里已经写了"标签纯白、没有文字"，起草步骤也强调了，仍然出现。这是视频模型的限制，继续改提示词收益很低。

## 要做
1. 新建 `kit/keyframe.py`：
   - 输入：简报、产品参考图、首帧描述（来自简报 `keyframe.prompt`，没有则从简报提示词前 3 秒的描述生成）。
   - 用 `gpt-image-2` 编辑接口生成首帧：产品必须来自参考图（保留形状、颜色、标签），画面比例与简报一致。保存为 `<运行目录>/keyframes/<id>.png`，用 ffprobe 或 PIL 核验尺寸。
   - 把首帧放到简报 `images` 的第一位，产品参考图放第二位；提示词开头写明"图 1 是首帧，图 2 是产品参考"。写成新的简报（id 加 `-kf`），不改原简报。
2. `.env` 新增 `MODEL_IMAGE`（默认 `gpt-image-2`）和 `IMAGE_PRICE_CNY`（单张估价，未知可留空，账本记为未知）。改 `kit/settings.py` 与 `.env.example`。账本记一条 `kind: image`。
3. 文档：新建 `references/keyframe.md`，写清何时用首帧路线、费用、风险；`references/configuration.md` 补两个配置项。

## 允许 / 禁止
允许：`kit/keyframe.py`（新建）、`kit/settings.py`（只加两个配置项）、`.env.example`、`kit/tests/test_keyframe.py`（新建）、`references/keyframe.md`（新建）、`references/configuration.md`。
禁止：`kit/loop.py`、`kit/review_panel.py`、`kit/refine_loop.py`、`kit/intake.py`、`kit/fission.py`、`kit/dashboard.py`、`kit/production.py`、`references/commands.md`、`VERSION`、`CHANGELOG.md`、`.env`、数据目录。

## 验收
- [ ] 对比实验：用 `projects/20261112-fission-H-test` 的 3 条 r2 简报（F01-r2、F02-r2、F03-r2）各做一条首帧版本，生成并自动评审（`loop gen` 默认会评审）。对照组用已有的 r2 成片，不重新生成。
- [ ] PR 里写一张表：每条的首帧图、旧判定与新判定、产品细节错误与乱码字是否出现、花费。
- [ ] 全部测试通过（接口调用用假函数测试）。

## 自测
- `python3 -m unittest discover -s kit/tests`
- 真实调用上限：`gpt-image-2` ≤ 3 张；视频生成 ≤ 3 条（共 30 秒，约 3.9 元）；文本模型 ≤ 12 次。新运行目录 `projects/<日期>-keyframe-ab/`，用 `products.py register-run` 登记到品 `water-test-strips`。`export STUDIO_HOME=<主仓库数据目录>`。
- 付费接口报错不换模型、不重试付费任务，如实记录。

## 交付
- commit、`git push -u origin feat/keyframe`、`gh pr create --base main`；不合并，不改 `VERSION`、`CHANGELOG.md`
