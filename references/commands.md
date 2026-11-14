# 命令速查

所有命令在 `kit/` 下运行（`cd kit`）。模型、价格、上限来自 `.env`，见 `configuration.md`。

## 流程引擎（`studio.py`）

```bash
python3 studio.py next --product <品> [--run <运行>] [--dry-run]
python3 studio.py status [--product <品>]
python3 studio.py pending
python3 studio.py approve <节点id> [--note "<说明>"] [--by <名字>]
python3 studio.py reject <节点id> --note "<原因>" [--by <名字>]
python3 studio.py changes <节点id> --note "<修改要求>" [--by <名字>]
python3 studio.py edit-script <节点id> --segment 2 --field voiceover --value "..." [--by <名字>]
python3 studio.py --json status --product <品>
```

- 所有推进都从 `next` 开始。需要确认时创建幂等节点并以退出码 3 停止。
- `--dry-run` 不写文件、不执行命令，只显示下一动作和预计金额。
- `--json` 放在子命令前，输出给平台或其它 Agent 使用的结构化结果。
- 每次最多执行一个付费步骤；批准后仍会检查批准额度和品剩余预算。
- 脚本编辑只在重新通过代码校验后写入，修改前后内容进入审计日志。

## 体检（`doctor.py`）

```bash
python3 doctor.py            # 本地检查
python3 doctor.py --online   # 另外确认模型名存在（免费）
```

## 品与预算（`products.py`）

```bash
python3 products.py create --product <品id> --name "<品名>" --anchor "<锚点>"
python3 products.py new-run --product <品id> --name "<运行名称>" # 创建 products/<品>/runs/<日期>-<名称>/ 并登记
python3 products.py status --product <品id>                 # 预算与已花金额
python3 products.py approve-budget --product <品id> --cny 13 --note "<理由>"
python3 products.py register-run --product <品id> --run <运行目录名>
python3 products.py select --product <品id> --ids C001,C002 --batch <调研批次>
python3 products.py queue                                   # 全部品的队列
python3 products.py queue --set-stage <品id> <阶段>
```

`queue --set-stage` 只保留为人工备注。`studio status` 与队列会显示数据推导阶段以及是否和备注不同。

## 手动对标（`intake.py`）

```bash
python3 intake.py add-url   --product <品id> --urls <链接,链接> --relation same
python3 intake.py add-file  --product <品id> --files <路径,路径> --relation similar
python3 intake.py breakdown --product <品id>
python3 intake.py tiers     --product <品id>
python3 intake.py plan      --product <品id> --n 10 --all
python3 intake.py confirm-tier --product <品id> --id M001 --tier H --note "<人工复核理由>"
python3 intake.py confirm-tier --batch <调研批次> --id C023 --tier H --note "<人工复核理由>"
python3 intake.py build     --product <品id> --run <运行目录>
```

- `--relation`：`same` 同款或同类（可到 H）；`similar` 同类不同形态（最高 L）；`different` 不同品类，只借展示方式（最高 L）。
- 链接：用 TikHub 取一次视频信息（1 次请求），模型直接看视频拆解。
- 本地文件：不上传到任何公开地址，只在本机抽帧后交给文本模型；听不到声音，记录里会注明。
- `plan` 三选一：`--pick M001,M002`（指定对标）、`--tiers H,M`（指定档位）、`--all`（全部）。只出计划和预计金额，不花钱。结果写到 `products/<品>/plan.json`。
- H 档计划项带 `needs_confirm: true`。`confirm-tier` 把人工档位与理由写入调研批次的 `tier_overrides.json`；也可把误判的 H 改为 M/L/X。
- 对调研产出的现有批次用 `confirm-tier --batch`；`--batch` 与 `--product` 二选一。
- `build` 按 `plan.json` 逐条生成 `<运行目录>/benchmarks/<候选id>.json`。每条复刻项调用一次拆解模型；未确认的 H 档直接跳过。已有对标文件默认不重复调用。
- 新手动对标存到 `products/<品id>/research/manual/`。旧外部 `manual-<品id>` 批次只读兼容，可把路径显式传给看板。

也可对任意调研批次单独构建一条：

```bash
python3 benchmark_builder.py --batch <调研批次> --id M001 --product <品id> --tier M --run <运行目录>
```

链接候选使用第一个可访问的 `play_urls`；本地候选临时上传，调用后立即删除。生成结果必须通过 `fission.check_inputs` 才会写入。

## 封面（`covers.py`）

TikTok 封面地址会过期。调研和手动登记时会自动把封面存到批次的 `covers/`，看板内嵌图片。旧批次补封面：

```bash
python3 covers.py backfill --batch <调研批次目录> --refresh
```

`--refresh`：封面已过期时用分享链接向 TikHub 刷新一次（每条 1 次请求）。只存封面图片，不下载视频。

## 调研（`tt_research.py`）

```bash
python3 tt_research.py search ...   # 关键词搜索
python3 tt_research.py filter ...   # 去重、主题门控、硬筛、爆款类型
python3 tt_research.py verify ...   # TokensFactory 验证（只传公开链接）
python3 tt_research.py report ...   # report.html
```

参数以 `python3 tt_research.py <子命令> --help` 为准。

## 拆解与生成（`loop.py`）

```bash
python3 loop.py breakdown --batch <调研批次> --ids C001,C002 --cap 3
python3 loop.py gen --project <运行目录> --brief <简报.json> --cap 3 --interval 20
python3 loop.py review --project <运行目录> --take <take> --refs <参考图>
python3 loop.py assemble --project <运行目录>
python3 loop.py board --project <运行目录>                   # 默认 <运行目录>/board.html
```

- `breakdown` 每次最多 `--cap` 条；播放链接过期的候选跳过且不计费。
- `gen` 提交前先检查预算（`products.can_spend`）。
- `board` 的调研批次默认 `BOARD_RESEARCH`，可用 `project.json` 的 `research_dir` 覆盖。

## 高质量逐镜头复刻

```bash
python3 shot_list.py --project <运行目录> --batch <调研批次> --id C023
python3 script_writer.py --project <运行目录> --facts "16 parameters,125 strips"
python3 keyframe.py scene --project <运行目录> --product-reference <产品图1>
python3 shot_plan.py --project <运行目录> --refs <产品图1,产品图2,产品图3> \
  --id hq-c023
python3 loop.py gen --project <运行目录> --brief <运行目录>/briefs/hq-c023.json --cap 6 --interval 20
python3 compose.py --project <运行目录> --brief <运行目录>/briefs/hq-c023.json \
  --refs <产品图1,产品图2,产品图3> --review
python3 loop.py assemble --project <运行目录>
python3 loop.py board --project <运行目录>
```

- `shot_list.py` 整条读取画面和声音，先给整体叙事结构，再按真实切镜输出，`shots.json` 必须从 0 秒连续覆盖到视频结尾。
- `script_writer.py` 用 `MODEL_SCRIPT` 生成 `script.json`；产品事实、竞品词、口播速度与画面文字要求由代码阻塞校验，最多重写一次。
- `keyframe.py scene` 会真实调用一次图片编辑，生成共用定场图；没有付费授权时不要执行。
- `shot_plan.py` 按 `cut`/`continuous`/`match_cut` 分组，单次 1–15 秒；计划记录统一 seed、接力块和预计金额，不发起视频生成。完整规则见 `long-take.md`。
- `loop.py gen` 仍在每个视频请求前调用 `products.can_spend`；每段独立落盘、独立评审。
- `compose.py` 的 TTS 只提交一次且失败不重试；优先用 Hypit 做时间线、字幕/画面文字图层、口播与音乐混合并导出，Hypit 执行失败才回退 ffmpeg，并在 `compose/compose.json` 记录原因。
- 合成后最终评审会新增 `replication_fidelity`，R 专家同时读取对标与成片。最终人工决定仍用 `human.py record`，流程不会自动扩量。

## 人工判定与校准（`human.py` / `calibrate.py`）

```bash
python3 human.py record --project <运行目录> --take <成片id> --verdict accept --note "复查后确认可以接受" --reviewer <名字>
python3 human.py list --project <运行目录>
python3 calibrate.py --projects <运行目录> [<运行目录> ...]
python3 calibrate.py --decisions ../calibration/human_decisions.jsonl --projects <运行目录>  # 兼容旧集中记录
```

- 人工结论可选 `accept|iterate|redo|hold|abandon|release`，同一成片可以追加多次；校准和看板取最后一条。
- 判定追加到 `<运行目录>/human_decisions.jsonl`，记录当时的专家结论，不修改专家规则。

## 账本回填（`ledger_backfill.py`）

```bash
python3 ledger_backfill.py                         # 默认账本，只预览
python3 ledger_backfill.py --ledger <costs.jsonl> # 指定账本，只预览
python3 ledger_backfill.py --ledger <costs.jsonl> --apply
```

- 默认 dry-run，只打印缺金额的视频提交和回填后的项目金额。
- `--apply` 会先生成同目录的 `costs.jsonl.bak-<时间>`，再只补视频 `submitted` 行中为空的 `estimate_cny`。
- 对主账本执行 `--apply` 前必须先确认范围；不要用文本模型或 TikHub 的未知价格补金额。

## 裂变（`fission.py`）

```bash
python3 fission.py --bench <benchmark.json> --dirs <directions.json> --out <run>/briefs [--n 5]
```

只生成简报，不调用付费接口。X 档会被拒绝（应走 `own_script.py`）。

## 自写脚本（`own_script.py`）

```bash
python3 own_script.py init  --id <id> --out <run>/scripts/<id>.json
python3 own_script.py check --script <脚本.json> --project <品或运行目录>
python3 own_script.py brief --script <脚本.json> --project <运行目录>   # 需要 refs/ 下有产品参考图
```

check 通过（无阻塞项）后才能 brief。示例：`methods/tiktok-script-generator/examples/`。

## 起草与修订循环

```bash
python3 draft_prompt.py --brief <简报.json> --project <运行目录> --benchmark <benchmark.json>
python3 refine_loop.py --brief <简报.json> --project <运行目录> --product <品id> --refs <参考图> --attempts 3
```

- `draft_prompt.py`：H/M/L 初稿。适配判断为 unfit 时不写入，交给人。
- `refine_loop.py`：会真实生成视频并花钱。每一轮先查品预算。

实验功能（不在主流程）：`prompt_review.py` 可对 X 档脚本做模型挑错与修改，结果写到 `<脚本>.reviewed.json`，不覆盖原稿。

## 测试

```bash
python3 -m unittest discover -s tests
```
