# M0 地基状态

日期：2026-11-11

## 已完成
- 目录骨架：kit/、schemas/、ledger/、products/、projects/。
- 契约：product、project、ledger_entry 三个 JSON Schema，`kit/schema.py` 做无依赖校验。
- 统一账本：`kit/ledger.py` 写入 `ledger/costs.jsonl`。调研脚本的 TikHub 调用和 Gemini 验证都已接入。
- 历史回填：从 2026-11-10 和 11-11 的 calls.jsonl、verify_calls.jsonl 回填 47 条（TikHub 45、Gemini 2）。
- 调研脚本迁移：`kit/tt_research.py` 为唯一实现；skill 里的 `scripts/tt_research.py` 变为兼容 shim。
- CLI：`kit/studio.py` 提供 validate、ledger、research 三个子命令。

## 验证结果
- 离线：`research filter`、`report`、`verify --dry-run` 在 round1 批次通过；`validate` 对样例产品和项目通过；账本行校验全部通过。
- 未验证：账本的实时写入钩子没有经过一次真实付费调用（避免为测试花钱）。钩子代码已编译，但只有下一次真实调用时才会实际执行。

## 未做（属于后续里程碑）
- 产品卖点仍是占位内容，M1 替换。
- 批次目录仍在 research/tiktok-benchmark/，M1 起按 studio/projects 组织。
- 未 git init，未提交。
