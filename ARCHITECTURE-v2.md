# Studio 架构 v2（2026-11-12）

状态：草稿 → 随第一批真实测试更新。v1 见 [ARCHITECTURE.md](ARCHITECTURE.md)，v1 的 §13–§15 仍有效，本文件优先。

## 0. 要解决的问题

用户想要的效果：从一个对标视频出发，找出可以迁移的方向，每个方向裂变出 N 条脚本，生成 AI 视频，并且每条成片都能说清楚"为什么通过、为什么不通过"。人只在关键节点介入（范围、试样、例外、整批、发布），其余由机器完成。系统要越用越准，而不是每次都从零开始。

要避免的问题：
- 只看一个总分就通过，产品走样或人物畸变被平均掉。
- 生成器自己宣布"完成"，没有独立检查（驾驭工程的"虚假胜利"）。
- 失败后反复付费重试，不知道原因（"惰性生成"与"无证据低分"）。
- 人工判断只留在脑子里，下一批又犯同样的错（"隐式约定"）。
- 规则越加越多，互相矛盾（"指令文件腐化"）。

## 1. 业务对象

```
对标 benchmark ──档位 H/M/L/X──► 方向 directions（固定项 + 变化轴）
                                   │ 裂变（§15.7，N 条）
                                   ▼
                                 简报 brief ──生成──► 成片 take
                                                       │ 机器检查 + 三专家
                                                       ▼
                                                   判定 decision（pass / repair / escalate）
                                                       │ 人工门 H0–H4
                                                       ▼
                                                   人工决定 decision record ──► 规则候选 candidate
```

- 档位决定"固定什么、变化什么"（§15.2、§15.7）。X 档不裂变，走自建方法库（§15.6）。
- 裂变：一个对标 + 一组方向 → N 条简报（`kit/fission.py`）。同批 N 条之间至少一个变化轴不同。

## 2. 两个闭环（借鉴 POD 双闭环 v3.0）

**生产闭环**：`简报 → 生成 → 机器检查 → 三专家评审 → 闸门判定 → 人工门 → 返修或交付`
回答："这条视频现在能不能用、要不要重做？"

**迭代闭环**：`人工决定 + 专家判定 + 失败归因 → 校准 → 规则候选 → 人工批准 → 新版专家 prompt / 规则 → 对照验证`
回答："下一轮为什么要改？改什么？什么时候才升级成正式规则？"

两个闭环通过同一组键关联：`project + brief_id + take + attempt + prompt_sha256 + video_sha256 + rules_version`，不靠文件名或口头记忆。

## 3. 人工门 H0–H4（借鉴 POD H0–H4，改为视频语境）

| 门 | 何时 | 人决定什么 | 去向 |
|---|---|---|---|
| H0 范围/预算 | 选对标、选档位、定 N、定预算 | 哪些对标入选；裂变方向；预算上限 | approved_scope 或 hold |
| H1 试样 | 每个新方向的前 1–3 条 | 方向与风格是否成立；专家判定是否合理 | accept 才扩量；iterate 定向返修；redo 换方向 |
| H2 例外 | 连续两次同类失败、专家分歧、provider_unknown | 继续、收窄，还是停止花钱 | revise / hold / abandon |
| H3 批次 | 一个方向的 N 条都过了机器和专家 | N 条之间是否真有差异；哪些特征可继承、哪些禁止重复 | batch_accept / batch_iterate |
| H4 发布 | 交付前 | 内容、授权、标注是否一致 | release 或退回 H3 |

人工不逐条确认正常生成。正常的机器检查、专家评审和已批准范围内的返修自动推进。

## 4. 传感器：计算型先行，推断型后置（借鉴驾驭工程 05、13）

**计算型（阻塞级，不花钱）** — `review_panel.machine_checks`
- 时长与简报差 ≤ 1 秒；分辨率短边与简报一致；宽高比一致；文件不是空的。
- 不过：直接 `repair → generate`，不调用专家。

**推断型（三专家，各自只负责自己的维度）** — `config/experts.yaml`
| 专家 | 维度 | 否决项 |
|---|---|---|
| E1 质量与镜头 | visual_quality、pacing_structure、platform_signals、audio_text | product_deformed |
| E2 产品一致 | product_consistency、soul_fidelity | product_wrong_detail、product_deformed、soul_must_keep_missing |
| E3 人物与动作 | person_consistency、visual_quality（手与肢体） | person_mismatch |

- 合并：逐维度取较低值；否决项取并集（`gate.merge_judges`）。
- 判定：加权 ≥ 8.0、任一维度 ≥ 6.5、无否决（`gate.evaluate`）。专家不能自己宣布通过。
- 证据规则：低于 6.5 的维度必须有带时间码的具体问题，否则记为无证据低分（`uncited_low`），需要人看。

**分级反馈**：阻塞（不过即退回）/ 警告（进入人工门）/ 信息（只记录）。成功静默。

## 5. 前馈：生成前限定范围（借鉴驾驭工程 12）

- 简报模板：产品锚点逐字注入每个镜头；固定项原样保留；变化轴只允许 §15.2 规定的轴。
- 档位与裂变规则在 `fission.py` 里检查，违反则拒绝生成。
- skill 的文本自检（敌人前置、28% 延迟、机制对立、单手约束）在生成前执行。

## 6. 状态与证据（借鉴驾驭工程 04、15）

每个项目目录：
- `project.json`：范围、预算、阶段目标。
- `briefs/<id>.json`：简报（不覆盖；返修生成新 attempt）。
- `jobs/<id>.json`：任务号与状态（有任务号未结束，不重提）。
- `takes/<id>.mp4`：成片。
- `reviews/<id>.json`、`reviews/<id>.decision.json`：机器检查、三位专家、合并、判定。
- `human_decisions.jsonl`：人工决定（只追加）。
- `iteration_manifest.json`：本轮范围、基线、预算、停止条件（每轮一份，checkpoint）。

规则：`provider_unknown`（超时、空响应、计费不明）不重提同一条；未完成任务先查原任务。

## 7. 规则登记表（借鉴驾驭工程 11）

`kit/config/rules.json`（待建）：每条规则有 id、内容、来源、适用范围、状态（active / candidate / superseded）。
- 候选规则不进入专家 prompt 或 rubric，必须经人批准。
- 专家 prompt 与 rubric 的版本号写进每条判定，便于追溯。

## 8. 学习与训练（你提出的"越练越好"）

1. 人工决定写入 `calibration/human_decisions.jsonl`（格式见 `schemas/video_hitl_decision.schema.json`）。
2. `kit/calibrate.py` 计算专家与人工的一致率，列出不一致样本。
3. 不一致样本分三类：专家太严、专家太松、维度归属错。分别对应改 prompt、改阈值、改维度分工。
4. 新 prompt 只在下一批对照验证后替换旧版。
5. 指标：一致率、误放行率（专家 pass、人工否决）、误拦截率（专家 repair、人工 accept）。误放行比误拦截更要紧。

## 9. 预算与停止

- 每批有预算上限（写在 project.json）。单条最多 2 次返修（本轮）；rubric 的 max_attempts 为 5，超过交给人。
- 同一缺陷连续两次未解决 → H2，不再自动付费。
- 本轮授权：TokensFactory 100 元 / 100 刀；视频只用 MiniMax H3；图片只用 GPT Image。

## 10. 与 v1 的差异

| 项目 | v1 | v2 |
|---|---|---|
| 评审 | Gemini 两个视角（常规、挑错） | 三位专家，各负责维度；先机器检查 |
| 人工 | 看板勾选 | H0–H4 门，结构化决定 |
| 学习 | 无 | 校准脚本 + 规则登记表 |
| 裂变 | §15.7 定义，未实现 | `fission.py` 已实现，H 档测试通过 |

## 11. 对应关系（业务 ↔ 双闭环 ↔ 驾驭工程）

| 业务 | POD 双闭环 | 驾驭工程 |
|---|---|---|
| 档位与裂变规则 | Rule Snapshot | 前馈：指令与约束 |
| 生成 | Pilot Generation | 行动 |
| 机器检查 + 三专家 | Machine Audit | 反馈：计算型与推断型传感器 |
| 人工门 H0–H4 | Human Decision | 调整：人只处理关键处 |
| 决定记录与校准 | Learning Proposal | 知识固化：规则登记表 |
| take / reviews / manifest | 关联键 | 状态子系统、checkpoint |

## 12. 需要你定的事

1. 专家阈值（加权 8.0、单项 6.5）是否沿用 VideoRea 的数值，还是按视频类型单独定？
2. H1 试样：每个新方向的前几条需要你逐条看，还是只看专家不通过的？
3. 规则候选由谁批准：你本人，还是允许专家改动 prompt 后先跑对照？

## 13. 当前进展

见本文件末尾的"测试记录"（随运行补充）。

## 测试记录

（待补充）
