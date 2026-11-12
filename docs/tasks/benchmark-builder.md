# 任务书 R3：从选中的对标自动生成对标文件 + H 档人工确认

分支：`feat/benchmark-builder` · 负责人：Codex · 审核：Claude
**前置**：`loop.gemini` 支持 `role` 参数（`grep -n 'role="text"' kit/loop.py` 有结果）；测试全部通过。否则停下说明。
**并行说明**：另一个任务（R1）正在改 `kit/loop.py`、`kit/review_panel.py`、`kit/common.py`、`kit/settings.py` 等，本任务不要碰这些文件。解析模型输出的 JSON 用现有的 `tt_research.parse_json_obj`（R1 会把它统一到 `common.parse_json`，接口不变）。

## 背景
- `intake.py plan` 只给出"哪条对标做几条"，没有把对标变成裂变需要的对标文件（`fixed` 的风格、动作、场景、机位、节奏，`must_keep`、`has_person`、`prompt_fixed`、`fixed_keywords`、`product_anchor`）。现在这一步靠人手写（例如 `projects/20261111-loop-water-strips/fission/benchmark_H_test.json`）。
- 同一条视频两次拆解，"可复制"分可能差 1 分，档位在相邻两档间浮动（C002 一次 L、一次 M）。H 档是"像素级复刻"，判错代价最大。

## 要做
1. 新建 `kit/benchmark_builder.py`：
   - 输入：调研批次目录（含 `candidates.json`、`breakdown.json`）、候选 id、品 id（读 `products/<品>/product_anchor.txt`）、档位。
   - 视频地址：链接候选用 `play_urls` 里可访问的那个；本地文件候选用 `media_host.upload`，用完删除。
   - 调用一次文本模型（`role="breakdown"`，用 `tt_research.parse_json_obj` 解析），按档位输出对标文件需要的字段。H 档必须输出 `fixed.style`、`has_person`；所有档必须输出 `must_keep`（列表，按时间顺序）和 `prompt_fixed`（英文，给视频模型）。
   - 输出写到 `<运行目录>/benchmarks/<候选id>.json`，并用 `fission.check_inputs` 校验；不通过就报告缺哪项，不写文件。
2. `intake.py`：
   - `plan` 的每个 H 项标记 `needs_confirm: true`；新增 `confirm-tier --product <品> --id <候选> --tier H|M|L|X --note "<理由>"`，把人工确认的档位写进批次的 `tier_overrides.json`。
   - `research.build` 读取 `tier_overrides.json`，有覆盖时用覆盖的档位并在 `tier_reasons` 里注明"人工确认"。（允许改 `kit/research.py` 的这一处。）
   - 新增 `build --product <品> --run <运行目录>`：对 `plan.json` 里的每个复刻项调用 `benchmark_builder`，未确认的 H 项跳过并提示。
3. 测试：用假模型（注入函数）覆盖字段校验、H 未确认跳过、档位覆盖生效。

## 允许 / 禁止
允许：`kit/benchmark_builder.py`（新建）、`kit/intake.py`、`kit/research.py`（只改读取档位覆盖）、`kit/fission.py`（只为校验需要的小改动）、`kit/tests/test_benchmark_builder.py`（新建）、`references/tiers.md`、`references/commands.md`。
禁止：`kit/loop.py`、`kit/review_panel.py`、`kit/settings.py`、`kit/dashboard.py`、`kit/production.py`、`VERSION`、`CHANGELOG.md`、`.env`、数据目录。

## 验收
- [ ] 对复制进来的 `manual-water-test-strips` 批次的 M001（链接）和 `manual-scratch-4mp4` 的 M001（本地文件）各生成一份对标文件，`fission.check_inputs` 通过；PR 里贴两份文件。
- [ ] H 项未确认时 `build` 跳过并提示；确认后生成。
- [ ] 全部测试通过。

## 自测
- `python3 -m unittest discover -s kit/tests`
- 真实调用：文本模型 ≤ 6 次；本地文件上传 ≤ 1 个且用完删除（`.env` 里已有 `MEDIA_HOST_REPO`）；不发起视频生成。
- 数据隔离：**不要设置 `STUDIO_HOME`，不要写主仓库的任何文件**。把主仓库外的两个调研批次 `<主仓库上一级>/research/tiktok-benchmark/manual-water-test-strips`、`manual-scratch-4mp4` 复制到 clone 里的 `_data/research/tiktok-benchmark/`，并 `export RESEARCH_ROOT=<clone 绝对路径>/_data/research/tiktok-benchmark`（`settings.workspace_root() / RESEARCH_ROOT` 遇到绝对路径会直接用它）。`_data/` 加进 `.gitignore`。
- 本次真实调用写进 clone 的 `ledger/costs.jsonl`；在 PR 里贴出新增的账本行（不含密钥），审核方会并入主账本。

## 交付
- commit、`git push -u origin feat/benchmark-builder`、`gh pr create --base main`；不合并，不改 `VERSION`、`CHANGELOG.md`
