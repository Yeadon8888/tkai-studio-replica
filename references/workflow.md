# 数据驱动工作流与确认节点

## 唯一推进入口

每一步先运行：

```bash
python3 kit/studio.py next --product <品>
```

Agent 只能执行这条命令给出的一个动作。需要人确认时停止；不得绕过确认节点直接调用模型或付费命令。`--dry-run` 只显示动作和预计金额，不创建节点、不执行命令。

`studio next` 以退出码 3 停在确认节点时，Agent 应告诉用户运行 `python3 kit/studio.py console` 打开确认台；命令行 `approve/reject/changes` 仍保留作无浏览器环境的备用入口。

## 阶段如何推导

引擎不读取人工设置的阶段来决定下一步，而是依次检查实际数据：产品卡 → 已选对标与档位 → `plan.json` → `shots.json` → `script.json` → 带预计金额的生成简报 → 成片 → 评审结论 → 人工判定 → 发布记录。

同一条成片可以有多次评审。评审记录必须写 `take`、`review_id` 和 `reviewed_at`；`take` 始终是被评成片 ID，文件名可以用独立的复评 ID。读取历史记录时先按记录里的 `source_take` / `take` 归组；旧记录没有这些字段时，按文件名前缀匹配实际成片 ID。每组以记录时间最新的一条作为当前闸门判定，其余记录作为历史保留。旧记录没有时间字段时，以评审文件修改时间兼容排序。

`products.py queue --set-stage` 仍可写人工备注阶段。`studio status` 和队列同时显示备注阶段、推导阶段以及两者是否不同；看板应使用推导阶段。

本地计算可以直接执行。镜头拆解以及任何模型或付费步骤必须有对应批准；一次 `studio next` 最多执行一个付费步骤，随后停止。执行前还会复核批准额度和品剩余预算。

## 六类确认节点

节点当前状态写在 `products/<品>/checkpoints/<id>.json`，每次创建、自动批准或人工处理追加到 `products/<品>/checkpoints/log.jsonl`。

| 类型 | 用途 | 是否阻塞 |
|---|---|---|
| `benchmark` | 确认对标、档位与制作范围 | 是 |
| `script` | 代码校验通过后确认脚本、字幕与画面提示词 | 是 |
| `budget` | 确认生成计划预计金额 | 是 |
| `verdict` | 闸门 pass 或 escalate 后由人判断是否可用 | 是 |
| `publish` | 记录是否发布、账号与链接；不自动发布 | 是 |
| `review_data` | 为实发数据回看预留 | 否，只通知 |

同一品、运行、类型和 subject 只会生成一个节点。驳回或要求修改必须说明原因。脚本编辑会重新运行 `script_writer.validate`；校验失败时不会覆盖 `script.json`，校验通过后在日志保留修改前后文本。

成片复评改变判定时，已有 `verdict` 节点保留原 ID，更新 subject 中的最新评审路径、时间与判定，并重新置为待确认；原主题与处理结果随 `subject_updated` 事件保留在 `log.jsonl`。

## 预算自动批准

`.env` 的 `AUTO_APPROVE_CNY` 默认是 `0`，表示永不自动批准。只有预计金额同时满足以下两项，预算节点才由系统批准并留下审计记录：

1. 预计金额不超过 `AUTO_APPROVE_CNY`；
2. 预计金额不超过该品当前剩余预算。

金额未知时不能自动批准。自动批准只批准预算节点，不代表可以越过脚本、人工判定或发布节点。

## 接管旧运行

引擎上线前已经产生 `shots.json`、简报、成片或评审的运行，不会自动假定历史步骤已经获批。`studio status` 会提示显式执行：

```bash
python3 kit/studio.py adopt --product <品> --run <运行>
```

`adopt` 只依据已经存在的下游产物补建上游 `benchmark`、`script`、`budget` 批准节点，决定人固定为 `migration`，依据文件写入审计日志。它不会生成内容、调用模型或替人判定成片。
