# 任务书 R4：多段视频（如 20 秒 = 2 × 10 秒）进入生成—评审流程

分支：`feat/segments` · 负责人：Codex · 审核：Claude
**前置**：`main` 已包含 R1（评审看整条视频、生成后自动评审）。动手前确认 `grep -n "remote_url" kit/loop.py` 有结果；否则停下说明。

## 背景
- 用户要 20 秒视频，但简报只支持单段。分支会话手工拼接了 6 个 20 秒或分段文件（例如 `TSP-L20-20s.mp4`），全部没有评审，或评审解析失败。

## 要做
1. 简报支持 `segments`：`[{"prompt": "...", "duration": 10, "images": [...可选]}]`。没有 `segments` 时行为不变。
2. `loop gen`：有 `segments` 时逐段生成（每段一个子任务 `<id>-s<k>`，复用现有去重、预算、轮询逻辑），全部成功后用 ffmpeg 拼成 `takes/<id>.mp4`（统一分辨率、帧率、音轨；拼接失败要报错，不要静默）。任一段失败就停止，不拼接，已生成的段保留。
3. 评审对拼好的整条视频进行（本地文件走 `media_host` 上传，评审后删除）；机器检查的时长按各段之和。
4. `refine_loop`：修订时可以只改出问题的那一段（根据 issues 的时间码落在哪一段），其余段的提示词不变；新一轮只重新生成被修改的段，再拼接。
5. 文档：`references/reading-and-loop.md` 写清多段的写法和费用（每段按秒计费）。

## 允许 / 禁止
允许：`kit/loop.py`、`kit/review_panel.py`、`kit/refine_loop.py`、`kit/tests/test_segments.py`（新建）、`references/reading-and-loop.md`。
禁止：`kit/intake.py`、`kit/fission.py`、`kit/research.py`、`kit/settings.py`、`kit/dashboard.py`、`kit/production.py`、`references/commands.md`、`VERSION`、`CHANGELOG.md`、`.env`、数据目录。

## 验收
- [ ] 单元测试：拼接、段失败不拼接、修订只重生成出问题的段（用假生成函数）。
- [ ] 真实跑通一条 20 秒视频（2 段 × 10 秒），自动评审完成；PR 里贴任务号、评审结论、花费。
- [ ] 全部测试通过。

## 自测
- `python3 -m unittest discover -s kit/tests`
- 真实调用：`export STUDIO_HOME=<主仓库数据目录>`；视频生成最多 2 段（共 20 秒，约 2.6 元），不重试付费任务；文本模型 ≤ 6 次。简报用 `projects/20261112-fission-H-test/briefs/H-test-C002-F01-r2.json` 的提示词拆成两段（前 10 秒举瓶与蘸水，后 10 秒对照结果），放在新运行目录 `projects/<日期>-segments-test/`，并用 `products.py register-run` 登记到品 `water-test-strips`。

- 预算：用户已批准品 `water-test-strips` 上限提到 19.5 元（已花约 10.4 元）。本任务视频费用不超过 2.6 元；生成前用 `products.can_spend` 确认，不够就停下说明。
- 临时视频托管已改为 GitHub Release 附件（`media_host`），网络断流会自动重试；上传仍失败时如实记录，不要改用别的托管方。

## 交付
- commit、`git push -u origin feat/segments`、`gh pr create --base main`；不合并，不改 `VERSION`、`CHANGELOG.md`
