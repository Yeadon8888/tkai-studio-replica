# 任务书 R8：高质量复刻流程（镜头级拆解 → 逐镜头生成 → 后期合成 → 还原度审判）并用 TESPERT 跑一条 20 秒试样

分支：`feat/hq-replica` · 负责人：Codex · 审核：Claude
**前置**：`main` 已包含 R6（清理）与 R7（按用途选模型、逐段评审、生成前预算检查）。动手前 `git pull`，确认 `grep -n VIDEO_ROUTE_ kit/settings.py` 有结果；否则停下说明。视频生成只走 TokensFactory（见 `AGENTS.md`），禁止 AutoDL。视频理解用 `.env` 的 `MODEL_TEXT`（当前 `gemini-3.8-flash-low`，能读画面和声音）。

## 目标
用户要的是高质量的爆款复刻。数据：28 条成片 0 条通过，失败一半以上是"产品细节错误"和"乱码字"——视频模型画不好产品标签和文字。原则：**视频模型只负责画面与动作；产品、文字、声音不交给它**。

## 对标与产品（已确定，用户已批准试样预算）
- 对标：调研批次 `<主仓库上一级>/research/tiktok-benchmark/20261113-US-drinking-water-strips` 的 **C023**（`https://www.tiktok.com/@mrs.fallout/video/7682136023844818189`，54.5 万播放，18 秒，第一人称俯拍，手拿 16 项参数试纸蘸水后对照色卡；验证 A，可复制 2）。档位 H，**用户已于 2026-11-13 确认 C023 = H**（原话："确认 C023"）。现在的 `intake.py confirm-tier` 只支持手动对标批次（`manual-<品>`），请给它加 `--batch <调研批次目录>` 参数，然后用它把这条确认写进该调研批次的 `tier_overrides.json`（理由写"用户确认 C023 为 TESPERT 试样 H 档对标"），并补测试。
- 产品：品 `tespert-16in1`，真实产品图在 `<主仓库>/projects/20261112-tespert-16in1/refs/`（`1.jpg`、`2.jpg`、`3.jpg`、`product_1_box_bottle.jpg`、`product_3_features.jpg`）。卖点只用产品图上写的：16 项参数、125 条、浸入后比色。

## 要做
1. **镜头级拆解**（`kit/shot_list.py`）：模型读对标整条视频（含声音），输出镜头表：每个镜头的起止时间、景别、机位与运动、动作、画面上的文字、口播原文、音乐与音效、节奏点、为什么抓人。校验时间轴连续。保存到运行目录 `shots.json`。
2. **镜头 → 生成计划**（`kit/shot_plan.py`）：
   - 每个镜头 3–6 秒，单独生成；按镜头内容选路线：产品是主体的镜头 → 用 `kit/keyframe.py` 以真实产品图做首帧，走 `keyframe`（首尾帧）或 `quality`（cinema）路线；手部动作镜头 → `quality` 路线，附 2–3 张真实产品图。
   - 提示词里不要求模型画任何文字；标签区域写"保持参考图原样，不新增文字"。
   - 口播：按对标的节奏用我方卖点改写台词（不照抄原文），记录到计划里。
   - 输出与 R4 相同结构的多段简报（`segments`），每段带 `video_route`。
3. **后期合成**（`kit/compose.py`）：用 Hypit（`~/.agents/skills/hypit`）做时间线、字幕、画面文字、口播与音乐合成并导出；口播用 `gemini-3.8-flash-tts`（TokensFactory 原生接口，见 tokensfactory-media skill）；画面文字按镜头表的位置与时间重做（用我方文案）。Hypit 不可用时用 ffmpeg 实现同样效果，并在 PR 说明。
4. **还原度专家**（规则变更已获用户批准，属于本次试样方案）：`kit/config/experts.yaml` 新增专家 R（还原度），同时给它对标视频和成片，按镜头对比结构顺序、节奏、景别与机位、动作、风格、字幕节奏，输出 `replication_fidelity` 分与问题；`rubric.yaml` 新增该维度（权重 0.15，其余维度按比例下调，总和 1）。
5. **品牌 logo 检查**（同上已批准）：V 专家清单新增"画面里不应出现的第三方品牌 logo（衣服、帽子、道具）"，失败 cause=`compliance_fail`。
6. **人工确认**：成片生成后，看板显示镜头表与每段，等用户用 `kit/human.py` 判定；不自动扩量。

## 允许 / 禁止
允许：`kit/intake.py`（只为 confirm-tier 的 --batch）、`kit/shot_list.py`、`kit/shot_plan.py`、`kit/compose.py`（新建）、`kit/keyframe.py`、`kit/loop.py`、`kit/review_panel.py`、`kit/production.py`、`kit/dashboard.py`、`kit/config/experts.yaml`、`kit/config/rubric.yaml`、`kit/tests/**`、`references/**`、`SKILL.md`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、已有数据目录（只读）、AutoDL 或其它非 TokensFactory 视频服务。

## 验收
- [ ] 单元测试覆盖镜头表校验、生成计划的路线选择、合成的时间轴与字幕对齐、还原度专家的提示词组装。
- [ ] 真实试样一条：TESPERT 复刻 C023，约 18–20 秒，有口播与字幕，成片与镜头表、每段评审、还原度评分都在看板上。
- [ ] 全部测试通过。

## 自测与上限
- `python3 -m unittest discover -s kit/tests`
- 真实调用：`export STUDIO_HOME=<主仓库数据目录>`；新运行目录用 `products.py new-run --product tespert-16in1 --name hq-c023`（R6 提供）。
  - 视频生成合计 ≤ 9 元（例如 cinema 960p 每秒 0.23 元 × 20 秒 ≈ 4.6 元，允许对出问题的镜头重做一次）；生成前用 `products.can_spend` 检查，品 `tespert-16in1` 上限 19.1 元（已花 9.1 元）。
  - GPT Image ≤ 6 张；TTS ≤ 5 次；文本模型 ≤ 60 次。
  - 付费任务失败不自动重提；上游报错如实记录。

## 交付
- commit、`git push -u origin feat/hq-replica`、`gh pr create --base main`；PR 正文附镜头表、每段评审、还原度评分、成片路径、花费明细。不合并，不改 `VERSION`、`CHANGELOG.md`。
