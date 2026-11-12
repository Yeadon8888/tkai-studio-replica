# 命令速查

所有命令在 `kit/` 下运行（`cd kit`）。模型、价格、上限来自 `.env`，见 `configuration.md`。

## 体检（`doctor.py`）

```bash
python3 doctor.py            # 本地检查
python3 doctor.py --online   # 另外确认模型名存在（免费）
```

## 品与预算（`products.py`）

```bash
python3 products.py create --product <品id> --name "<品名>" --anchor "<锚点>"
python3 products.py status --product <品id>                 # 预算与已花金额
python3 products.py approve-budget --product <品id> --cny 13 --note "<理由>"
python3 products.py register-run --product <品id> --run <运行目录名>
python3 products.py select --product <品id> --ids C001,C002 --batch <调研批次>
python3 products.py queue                                   # 全部品的队列
python3 products.py queue --set-stage <品id> <阶段>
```

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

## 起草、挑错、修订循环

```bash
python3 draft_prompt.py --brief <简报.json> --project <运行目录> --benchmark <benchmark.json>
python3 prompt_review.py --script <脚本.json> --project <运行目录> --rounds 3
python3 refine_loop.py --brief <简报.json> --project <运行目录> --product <品id> --refs <参考图> --attempts 3
```

- `draft_prompt.py`：H/M/L 初稿。适配判断为 unfit 时不写入，交给人。
- `prompt_review.py`：X 档脚本的挑错与修改，结果写到 `<脚本>.reviewed.json`，不覆盖原稿。
- `refine_loop.py`：会真实生成视频并花钱。每一轮先查品预算。

## 测试

```bash
python3 -m unittest discover -s tests
```
