# 架构

## 一张图

```
定品 ─► 调研 ─► 拆解/定档 ─► 分配条数 ─► 出提示词 ─► 生成 ─► 审判 ─► 看板
                                              ▲                   │
                                              └── 修订提示词 ◄─────┘（不达标时）
```

## 分层（为了能单独替换）

| 层 | 文件 | 负责 | 换的时候改哪里 |
|---|---|---|---|
| 配置 | `.env`、`kit/settings.py` | 模型名、价格、上限、预算、路径、密钥位置 | 只改 `.env` |
| 供应方 | `kit/common.py` 的 `http_json()`、`kit/loop.py`、`kit/tt_research.py` 的 `Client` | 发请求、记账 | OpenAI 兼容接口只改 `.env`；TikHub 保留独立记账和上限 |
| 规则 | `kit/config/rubric.yaml`、`experts.yaml`、`prompt_critic.yaml` | 评分维度、通过线、专家清单、提示词标准 | 改 yaml，人批准，写变更记录 |
| 流程 | `kit/*.py`（见下表） | 每一步的逻辑 | 一般不动 |
| 数据 | `products/<品>/`、`ledger/`（位置由 `STUDIO_HOME` 决定） | 每个品、品下运行、调研与证据 | 不进仓库；旧 `projects/`、外部 `research/` 只读兼容 |
| 文档 | `SKILL.md`、`references/` | 给 agent 的操作说明 | 与代码一起发版 |

## 每一步

| 步骤 | 模块 | 谁做判断 | 产出 |
|---|---|---|---|
| 定品与运行 | `products.py` | 人确认产品卡与预算 | `products/<品>/product_state.json`、`product_anchor.txt`、`runs/<日期>-<名称>/` |
| 调研 | `tt_research.py` | 代码做硬筛；模型（`MODEL_VERIFY`）判断是否同款 | 调研批次目录：`candidates.json`、`verify.json` |
| 手动对标 | `intake.py` | 用户说明产品关系；链接由模型看视频，文件由模型看抽帧 | `products/<品>/research/manual/`，结构与调研批次相同 |
| 选范围 | `intake.py plan` | 用户选对标、档位或全部；代码分条数 | `products/<品>/plan.json` |
| 拆解 | `loop.py breakdown` | 模型（`MODEL_BREAKDOWN`）按七个维度拆 | `breakdown.json` |
| 定档 | `replication.py` | 代码按规则算 H/M/L/X | 档位与理由 |
| 分配 | `fission.py allocate` | 代码按权重算 | 各档条数与缺口 |
| 骨架（H/M/L） | `fission.py` | 代码 | 简报：固定项原样、变化项替换、`must_keep` |
| 起草（H/M/L） | `draft_prompt.py` | 模型（`MODEL_DRAFT`）判断适配并改写；代码查硬约束 | 简报的 `prompt`、`draft_fit` |
| 自写（X） | `own_script.py` | 代码自检 | 脚本与简报；`prompt_review.py` 是实验功能，不在主流程 |
| 生成 | `loop.py gen` | 代码查预算与在途任务 | `takes/<id>.mp4`、`jobs/<id>.json` |
| 审判 | `review_panel.py`、`gate.py` | 代码做机器检查；模型（`MODEL_REVIEW`）按清单判；代码合并与裁决 | `reviews/<id>.json`、判定 |
| 修订 | `refine_loop.py` | 模型（`MODEL_REVISE`）改提示词；代码查硬约束、预算、轮数 | `refine/<id>.jsonl`、下一轮简报 |
| 看板 | `loop.py assemble`、`board`、`production.py` | 代码：按提示词系列归组、分级、算废片率与成品单价 | `board.html`（每组显示当前成片，过往废片可展开） |
| 校准 | `calibrate.py` | 人给判定，代码算一致率 | 是否要改专家提示词的依据 |

## 四个置信度

| 档 | 含义 | 提示词怎么来 |
|---|---|---|
| H | 只换人物和产品，动作、场景、机位、节奏保留 | 骨架 + 模型起草 |
| M | 保留必须项，场景和动作可换 | 骨架 + 模型起草 |
| L | 只保留钩子、证明、行动的逻辑，视觉重做 | 骨架 + 模型起草 |
| X | 不参考对标 | 按方法论自写，代码自检 |

## 三条不变的原则

1. **能算的先算**。预算、上限、锚点、时长、分辨率、时间轴都由代码检查，不交给模型。
2. **模型分工单一**。起草的不审判，审判的不改提示词，改提示词的不决定通过。
3. **每条结果可追溯**。评审记录里有 `versions`（skill 版本、配置版本、各环节模型名），账本里有每次调用。

## 已知的限制

- 档位依赖模型给的"可复制"分数。同一条视频两次拆解可能差 1 分，档位会在相邻两档之间变动（实测 C002 一次是 L、一次是 M）。重要的对标建议人工确认档位。
- 本地文件的拆解只看抽帧，听不到口播。
- 专家只看抽帧，听不到声音。有口播的视频，口播一项会标"需人工听"。
- 语音转写的模型还没有确认，台词时间只能靠视频理解模型读出，精度有限。
- 拆解问卷仍是七个维度的固定问卷，还没有改成"先整体、再逐段、最后分保留与重做"的读法。
- 旧 `projects/` 和外部 `research/` 数据不会迁移或删除；已有运行仍可直接传给看板等只读命令。
