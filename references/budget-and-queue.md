# 预算与任务队列

## 预算规则（用户约定）

- 每个品默认 **5 元**（`.env` 的 `PRODUCT_BUDGET_CNY`；建品时写入 `product_state.json` 的 `budget_cny`）。
- 超出默认预算：先问用户，同意后执行
  `products.py approve-budget --product <品> --cny <金额> --note "<理由>"`。没有理由不能登记。
- TikHub、Gemini：**不限量**，只写账本、不设上限。
- TokensFactory 视频：按账本 `estimate_cny` 计入品预算（单价见 `.env` 的 `VIDEO_PRICE_CNY_PER_SECOND`）。
- 账本里 `estimate_cny` 为 `null` 的行是未知金额，单独报告，不要当作 0。

### 历史账本回填与费用口径

- `ledger_backfill.py` 只处理视频模型的 `submitted` 行，金额按该行 `units × kit/settings.py` 读取到的视频单价计算；终态行、文本模型和 TikHub 行不回填。
- 默认只 dry-run。`--apply` 写入前会在原文件旁创建带时间戳的备份，并保持未回填行原样。
- 视频费用只合计视频 `submitted` 行的已知 `estimate_cny`，视频秒数也只合计这些提交行；同一 `ref` 多次提交会全部计费，并单列超出首次的重复提交次数。
- 文本模型和 TikHub 当前只报告调用次数；文本模型另报失败次数及 prompt / completion token。价格未配置时不换算金额，也不把未知金额当作 0。
- 看板的“每条成品单价”仍按成片记录的全部视频花费除以通过条数；顶部“视频（元）”来自统一账本，两者口径分别用于成片摊销和账本核对。

## 品目录

```
studio/products/<品id>/
  product.json          品卡（已有约定，只读）
  product_state.json    预算、已选对标、运行记录（runs）
  product_anchor.txt    产品锚点，逐字注入每个镜头
```

运行目录（一次批次）放在 `projects/<日期>-<名称>/`，并用 `products.py register-run` 登记到品下。登记后，账本里该运行的花费才会计入这个品。

## 任务队列

- 文件：`products/_queue.json`。
- 阶段顺序：scope → research → breakdown → brief → generate → review → board → done。
- 查看：`products.py queue`。推进：`products.py queue --set-stage <品> <阶段>`。
- 多品并行时，队列只是看板；真正的互斥靠"一次只对一个品发起付费生成"。

## 检查清单（每次汇报前）

- 本品已花金额 / 上限（`products.py status --product <品>`）。
- 未知金额行数。
- 当前阶段与下一步。

## 调用上限（数值在 `.env`）

- TikHub：单次查询最多 `LIMIT_TIKHUB_CALLS` 次请求（默认 100）。
- 文本模型：单个运行最多 `LIMIT_TEXT_CALLS` 次（默认 100），含拆解、验证、起草、挑错、审判、修订。到上限即停止。
- 视频：每品默认预算 `PRODUCT_BUDGET_CNY`（默认 5 元），超出先沟通并登记理由。
