# 仓库约定（给 Codex / Claude）

- 这是一个 skill 仓库：`SKILL.md` + `references/` 给 agent 读，代码在 `kit/`，规则在 `kit/config/*.yaml`。
- 语言：文档、日志、提示、PR 正文都用简体中文，平铺直叙。
- 测试：`python3 -m unittest discover -s kit/tests`，必须全部通过；新增功能要有测试。
- 配置：所有模型名、价格、上限、路径都从 `kit/settings.py`（读 `.env`）取，不在代码里写死模型名或个人路径（有测试守着）。
- 密钥：只在 `.env` 或它列出的密钥文件里；不打印、不写进日志、commit、PR。
- 数据目录 `projects/`、`ledger/`、`media/`、`research/`、`products/<品>/` 是运行数据，不要提交，不要删除。
- 版本：`VERSION` 和 `CHANGELOG.md` 由审核方（Claude）在合并后统一更新，任务 PR 里不要改这两个文件。
- 付费调用：只做任务书允许的次数；上游报错不换模型、不批量重试，如实写进 PR。
- 把 `.env` 的 `STUDIO_HOME` 指向主仓库数据目录时，账本会记到主账本；这是为了花费可追溯，按任务书要求设置。
