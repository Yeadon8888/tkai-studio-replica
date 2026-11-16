# 任务书 R17：修完试跑遗留问题，文档对齐代码，发 0.10.0

分支：`release/0.10.0` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 PR #25（试跑 TESPERT）。动手前 `git pull`。**用户 2026-11-16 已批准**：更新 SKILL.md 并发版；下面第 4 点的复刻要点规则变更也在批准范围内。
付费：只允许为诊断第 1 点发起 ≤ 3 次文本/理解调用；不调图片、视频、音乐、TTS。

## 要做
1. **专家评审 HTTP 403**：试跑第三阶段 P/H/V/R 评审连续两次 403（报告 `docs/trials/20261116-tespert-20s.md`；运行目录 `/Users/yeadon_1/Desktop/MyProject/tkAI视频增长中台/studio-trials/20261116-tespert-20s/home/products/tespert-16in1/runs/20261116-20s-replica`）。查清是哪个请求（TokensFactory 的 Cloudflare 1010 缺 User-Agent？GitHub 临时媒体地址？），修复并加单测：所有对外 HTTP 请求统一经一个函数，必带 `HTTP_USER_AGENT`。用 `STUDIO_HOME` 指向上述试跑目录的**副本**重跑一次评审验证（≤ 3 次调用），PR 贴结果。
2. **脚本默认模型**：`MODEL_SCRIPT` 默认改为 `gemini-3.8-flash-low`（`settings.py` 默认值、`.env.example`、`references/configuration.md`）；理由：hydrogen 写长脚本 8000/16000 token 全部用于推理（试跑报告有证据）。`models.yaml` 给 hydrogen 加备注。
3. **文本模型价格**：只采用 `~/.claude/skills/tokensfactory-media/references/` 里有文档依据的单价写入 `models.yaml`，账本按 usage 计费；没有文档依据的保持"未知"，并在 `doctor.py` 输出里列出缺价模型。不得猜价。
4. **复刻要点：台词密度与卖点**（试跑结论：成片 20 秒只说约 9 秒；对标每个反派哭喊都顺带说出产品作用，成片没有）：
   - `format_invariants` 增加 `speech_coverage`（对标口播时长 / 总时长，来自转写）；脚本校验：计划口播覆盖率 ≥ 对标 × 0.8（按每秒 2.6 词估算）。
   - 对标里每个反派/节拍台词若含产品作用（拆解模型在 shots.json 每镜给 `benefit_claim: {present, evidence}`），脚本对应镜头必须有一句来自产品事实的作用（代码检查非空且含产品事实中的关键词）。
   - 写进 `essence` 预估与正式评分的代码项（`code-format-speech-coverage`、`code-format-benefit-per-beat`）。
5. **文档对齐代码**：
   - `SKILL.md`：声音路线改为"出镜角色（含特殊角色）用 H3 原声，只有画外音/特殊配音不在画面中才用 TTS"；加入形式不变量、神韵预估、转写、反向 CTA、角色设定表、末句保护、目标时长、能由代码确定的内容由代码补齐、`MODEL_SCRIPT`；按需阅读表补 `audio.md`、`long-take.md`、`keyframe.md`、`video-models.md`；保持简洁（≤ 120 行），细节放 references。
   - 检查 `references/*.md` 与代码不一致处并修正（命令参数、模型默认值、声音路线）。
6. **发版**：`VERSION` → `0.10.0`；`CHANGELOG.md` 顶部加 `## 0.10.0 — 2026-11-16`，按新增/变更/修复/迁移写清 0.9.0 之后的全部合并（R13–R17、PR #16–#25 及直接提交；用 `git log 0.9.0..HEAD` 核对，不漏）。迁移：`.env` 新增/默认变化的项。**不要打标签**（Claude 合并后打）。

## 允许 / 禁止
允许：`kit/**`、`kit/tests/**`、`references/**`、`SKILL.md`、`CHANGELOG.md`、`VERSION`、`.env.example`、`docs/trials/**`。
禁止：`.env`、真实 `products/`、中台仓库、付费媒体调用、打标签、push main。

## 验收
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过；`python3 kit/doctor.py`（离线）通过。
- [ ] 第 1 点在副本上评审成功（或写清仍失败的确切原因）。
- [ ] 交付：commit、push 分支、`gh pr create`；PR 写自测、评审验证结果、已知问题；不合并；不输出密钥。
