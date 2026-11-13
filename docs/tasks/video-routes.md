# 任务书 R7：按用途选择 MiniMax H3 系列模型（高画质、首尾帧、人物替换等）

分支：`feat/video-routes` · 负责人：Codex · 审核：Claude
**前置**：`main` 已包含 R4（多段）、R5（首帧）与 R6（清理）。动手前 `git pull`，确认 `kit/keyframe.py` 存在、`kit/studio.py` 已删除；否则停下说明。读 `~/.claude/skills/tokensfactory-media/SKILL.md` 与 `references/models.md`、`references/api.md` 中 H3 系列的输入格式。视频生成走 TokensFactory（见 `AGENTS.md`）。

## 背景
账户里可用的视频模型（`/v1/models` 2026-11-13）：`minimax-H3`、`minimax-H3-cinema`、`minimax-H3-fast`、`minimax-H3-first-last`、`minimax_h3_zm_titi`、`minimax_h3_zm_titim`、`wan2.2-motion`、`wan3`。现在代码只会调用 `minimax-H3`，其它能力用不上：
- `minimax-H3-cinema`：高画质，最高 960p（480p 0.10、768p 0.13、960p 0.23 元/秒）。
- `minimax-H3-fast`：480p/768p，1–15 秒。
- `minimax-H3-first-last`：首帧 + 尾帧 + 提示词，1–15 秒，适合"产品首帧"路线。
- `minimax_h3_zm_titi`：单角色替换，1 张人物图 + 1 段公开参考视频，保留原视频的动作、场景、机位、节奏和声音，只换人；5–25 秒，0.25 元/秒。只能换人物，不能换产品。
- `minimax_h3_zm_titim`：文档未写输入格式，推测为多角色替换。
- `wan2.2-motion`：动作迁移（人物图 + 参考视频）。

## 要做
1. **路线表**：`kit/settings.py` 与 `.env.example` 增加按用途选模型：`VIDEO_ROUTE_DEFAULT=minimax-H3`、`VIDEO_ROUTE_QUALITY=minimax-H3-cinema`、`VIDEO_ROUTE_FAST=minimax-H3-fast`、`VIDEO_ROUTE_KEYFRAME=minimax-H3-first-last`、`VIDEO_ROUTE_REPLACE_PERSON=minimax_h3_zm_titi`、`VIDEO_ROUTE_MOTION=wan2.2-motion`；价格表 `VIDEO_PRICES`（按模型和分辨率，元/秒），`ledger.estimate_cny` 按模型和分辨率计价，未知价格记为未知，不编造。
2. **请求体按模型组装**：在 `kit/loop.py` 的生成步骤里按模型拼不同的请求体并做本地校验（时长、分辨率、图片数量、必填项），不合法就在提交前报错，不花钱：
   - H3 / cinema / fast：`images`（1–9）、可选 `audio_urls`、`duration`、`resolution`、`aspect_ratio`。
   - first-last：`first_frame`、`last_frame`、`prompt`、`duration`、`resolution`（如 `768p竖`）。
   - titi：`ref_image`（人物图）、`ref_video`（公开地址）、可选短 `prompt`（≤2048 字）、`duration`（5–25，且不超过参考视频时长）、`resolution`。
   - motion：`ref_image`、`ref_video`、`resolution`。
   简报新增 `video_route`（`default|quality|fast|keyframe|replace_person|motion`），没有则用默认。
3. **用途接入**：
   - `kit/keyframe.py`：有首帧和尾帧时走 `keyframe` 路线。
   - H 档且参考视频有人物、且产品在画面里不重要（简报写明）时，可以走 `replace_person`：参考视频用对标的公开播放地址（过期就刷新），人物图由用户提供或已有素材。产品仍是对标视频里的产品，这一点要在简报和看板上标明"仅换人，未换产品"。
   - `titim`：先查 https://tokensfactory.cc/docs 是否有输入说明；查不到就不接，PR 里说明。
4. **评审与看板**：评审记录、看板的每条成片显示所用模型与分辨率；成本按模型计价。
5. **多段视频逐段评审**（用户 2026-11-13 确认）：拼接后的长视频是本地文件，模型读不到 GitHub 上的临时地址，只能抽帧、听不到口播。改为：
   - 每一段生成完成后，用该段任务记录里的 `remote_url`（生成服务自己的地址，模型能读到）做整段视频评审，评审记录按段保存（`reviews/<id>-s<k>.json`）。
   - 父成片的判定由各段汇总：任一段否决或不通过，整条不通过；问题的时间码换算到整条视频的时间（加上前面各段的时长）。
   - 另外对拼接后的整条视频做一次机器检查（时长、分辨率、音轨、拼接处有无黑帧或跳帧），不需要模型。
   - 修订循环（`refine_loop`）只重做出问题的那一段，沿用 R4 的逻辑。
   - 某一段的 `remote_url` 失效时，这一段回退到抽帧，并在记录里写明。
   - **看板**：分段（`<id>-s<k>`）归到父成片下面显示，不单独成为系列；父成片的花费等于各段花费之和（不再按"时长 × 单价"另算一份估算，避免重复计价）；父成片下展开可看每段的视频、判定和问题。（2026-11-13 看板实测：两段被当成两个独立系列，父成片又被估算一次 1.3 元，系列合计 3.9 元，实际只花了 2.6 元。）
6. **预算硬检查**：`loop gen` 在提交每个付费任务（含每一段）前，如果运行目录已登记到某个品，就用 `products.can_spend` 检查，不够就停止，不提交。现在只有修订循环检查预算。
7. **文档**：`references/configuration.md`（路线表与价格）、新建 `references/video-models.md`（每个模型能做什么、不能做什么、何时用）、`SKILL.md` 的出提示词与生成步骤补一句"按用途选模型"。

## 允许 / 禁止
允许：`kit/settings.py`、`.env.example`、`kit/loop.py`、`kit/ledger.py`、`kit/keyframe.py`、`kit/production.py`、`kit/dashboard.py`、`kit/review_panel.py`、`kit/refine_loop.py`、`kit/tests/test_video_routes.py`（新建）、`kit/tests/test_core.py`（只改失效断言）、`references/**`、`SKILL.md`。
禁止：`kit/config/rubric.yaml`、`kit/config/experts.yaml`、`VERSION`、`CHANGELOG.md`、`.env`、已有数据目录。

## 验收
- [ ] 每个路线的请求体有单元测试（用假接口），非法输入在提交前被拦下。
- [ ] 多段视频逐段评审：单元测试覆盖汇总规则和时间码换算；真实调用时，用 R4 生成的 `projects/20261113-segments-test` 两段（如果 `remote_url` 仍可访问）各做一次整段评审，PR 里写明每段是否真的读到视频（prompt_tokens）和是否听到口播。
- [ ] 账本按模型和分辨率计价，历史行金额不变。
- [ ] 真实调用（预算内，见下）各成功 1 条，评审完成，看板显示模型。
- [ ] 全部测试通过。

## 自测
- `python3 -m unittest discover -s kit/tests`
- 真实调用：`export STUDIO_HOME=<主仓库数据目录>`；`minimax-H3-cinema` 960p 5 秒 1 条（约 1.15 元）；`minimax_h3_zm_titi` 5 秒 1 条（约 1.25 元，参考视频用 C002 的公开播放地址，人物图用已有成片的一帧）。生成前 `products.can_spend`，品 `water-test-strips` 预算不够就停下说明。新运行目录建在主仓库 `projects/` 下并 `register-run`。不重试付费任务。

## 交付
- commit、`git push -u origin feat/video-routes`、`gh pr create --base main`；不合并，不改 `VERSION`、`CHANGELOG.md`
