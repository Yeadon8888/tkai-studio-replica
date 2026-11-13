# 任务书 R6：清理重复入口、重复小工具、过时引用；新品的运行目录归到品下

分支：`feat/cleanup` · 负责人：Codex · 审核：Claude
**前置**：`main` 已包含 R4（多段）与 R5（首帧）。动手前 `git pull`，确认 `kit/keyframe.py` 存在且 `grep -n "segments" kit/loop.py` 有结果；否则停下说明。测试全部通过。

## 背景（审视结论第三批）
- 重复入口：`kit/studio.py` 没有任何模块引用，功能与 `loop.py board` 重复；选对标有三条路（看板导出 `selection.json` + `loop select/produce`、`products.py select`、`intake plan --pick`）。
- 重复小工具：读写 JSON 4 份（`common`、`intake`、`products`、`refine_loop`）；读取产品锚点 4 份（`draft_prompt`、`own_script`、`refine_loop`、`review_panel`）；默认时长 `10` 写死在 6 处；HTTP 请求散在 `loop`、`tt_research`、`doctor`、`media_host`。
- `kit/prompt_review.py`（X 档挑错）只在演示中跑过，还曾误读规则把脚本改坏；它不在主流程里。
- 过时引用：代码和看板还引用 `GATES.md`、`ARCHITECTURE.md`（安装包里没有）。
- 数据分散："一品一文件夹"只是名义上的：品的状态在 `products/<品>/`，运行在 `projects/<日期>-<名称>/`，手动对标批次在仓库外的 `research/`。

## 要做
1. 删除 `kit/studio.py`。删除 `loop.py` 的 `select`、`produce` 子命令；看板调研页的"导出 selection.json"按钮改为说明"用 `products.py select` 或 `intake.py plan --pick` 选对标"。选对标只保留 `products.py select`（记录）和 `intake.py plan`（分配）。
2. 小工具合并到 `kit/common.py`：`read_json(path, default=None)`、`write_json(path, obj)`、`read_anchor(dir)`；所有模块改用它们（保留旧名的薄包装也可以，但只有一份实现）。默认时长一律用 `settings.get_float("VIDEO_DURATION")`。
3. HTTP：在 `kit/common.py` 加一个 `http_json(method, url, headers, body=None, timeout)`，`loop._api`、`loop.gemini`、`doctor` 改用它；`tt_research.Client` 保持现状（它有自己的记账和上限）。
4. `prompt_review.py`：保留文件，在模块说明和 `references/commands.md` 标注"实验功能，不在主流程"；`SKILL.md` 主流程不再提它。
5. 过时引用：代码、看板副标题里的 `GATES.md`、`ARCHITECTURE.md` 引用改为 `references/architecture.md` 或删除。
6. 新品的运行目录归到品下：`products.py` 加 `new-run --product <品> --name <名称>`，在 `products/<品>/runs/<日期>-<名称>/` 建运行目录并自动登记；`intake.py` 的手动对标批次默认放到 `products/<品>/research/manual/`。旧位置（`projects/`、仓库外 `research/`）**只读兼容**，不迁移、不删除旧数据。账本按运行目录名记账的规则不变。
7. 文档同步：`SKILL.md`、`references/architecture.md`、`references/commands.md`、`references/budget-and-queue.md`。

## 允许 / 禁止
允许：`kit/**`（除 `kit/config/**` 的评分标准）、`SKILL.md`、`references/**`、`README.md`、`kit/tests/**`。
禁止：`kit/config/rubric.yaml`、`kit/config/experts.yaml`（规则变更要人批准）、`VERSION`、`CHANGELOG.md`、`.env`、所有已有数据目录（不移动、不删除）。

## 验收
- [ ] `kit/studio.py` 已删除；`loop.py --help` 不再有 `select`、`produce`。
- [ ] `grep -rn "def _read\|def _write" kit/*.py` 只剩 `common.py`（或仅为薄包装）；读取锚点只有一份实现；`"duration", 10)` 不再出现。
- [ ] `grep -rn "GATES.md\|ARCHITECTURE.md" kit SKILL.md references` 无结果（`references/architecture.md` 自身除外）。
- [ ] `products.py new-run` 可用，新运行目录在品下，账本与预算统计正确；旧项目看板仍能生成。
- [ ] 文档一致性测试（`kit/tests/test_skill_docs.py`）与全部测试通过；`python3 kit/package.py --out <临时目录>` 导出成功。

## 自测
- `python3 -m unittest discover -s kit/tests`
- 用 `projects/20261112-fission-H-test`（复制进 clone）生成一次看板，确认旧位置仍兼容。
- 不需要任何付费调用。

## 交付
- commit、`git push -u origin feat/cleanup`、`gh pr create --base main`；不合并，不改 `VERSION`、`CHANGELOG.md`
