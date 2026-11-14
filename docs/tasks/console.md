# 任务书 R12：本地确认台（平台界面雏形）

分支：`feat/console` · 负责人：Codex · 审核：Claude
**前置**：`main` 已含 R11a（`kit/checkpoints.py`、`kit/engine.py`、`kit/studio.py`）与 R11b（`kit/models.py`）。动手前 `git pull`，确认 `kit/checkpoints.py` 存在；否则停下说明。本任务不发起付费调用。

## 目标
把看板升级成"待办 + 确认"页面：人在页面上完成 R11a 的六类确认，所有写入都走 `checkpoints.resolve`（不得另写一套）。这是将来平台的界面雏形，所以页面只通过一层 JSON 接口读写，接口与 `studio.py --json` 的结构一致。

## 要做
0. **先修引擎接真实数据**（`kit/engine.py`，审核 R11a 时用 TESPERT 真实数据副本发现）：
   `STUDIO_HOME=<副本> python3 kit/studio.py status --product tespert-16in1 --run 20261113-hq-c023` 推导为"对标与档位"，但这个运行已有 `shots.json`、`briefs/`、`takes/`、`reviews/`（含 `hq-c023.decision.json` = pass、4 个分段评审）。原因与要求：
   - 引擎上线前的运行没有任何确认节点，于是卡在第一关。新增 `studio adopt --product <品> --run <运行>`：对"已有下游产物"的上游阶段补建 `approved` 节点，`decided_by: "migration"`，`note` 写明依据的文件；不自动执行，由人或 Agent 显式运行；日志可追溯。`status` 遇到这种情况时提示运行 `adopt`，而不是要求重新确认对标。
   - 已选对标只看 `product_state.selected_benchmarks`，漏了运行目录里的 `shots.json`（`candidate_id`、`reference_url`）与调研批次的 `tier_overrides.json`；补上这些来源。
   - 成片判定取 `sorted(*.decision.json)[-1]`，多段运行会取到某个分段（如 `hq-c023-s4`）而不是父成片。改为按成片逐条处理：父成片优先，分段只作为父成片的附属；每条成片各自一个 `verdict` 节点。
   - 评审命令里 `--refs ""` 与 `shot_plan` 的产品图用 `pdir.rglob` 收集所有图片（会混入封面、关键帧、截图）：产品参考图改为读取产品卡登记的参考图列表（没有就停下要求登记，不猜）。
   - 加测试：用与 TESPERT 运行同结构的假数据，`adopt` 后推导为 `verdict`，节点指向 `hq-c023`。
1. `kit/console.py`：用 Python 标准库起本地服务，**只绑定 127.0.0.1**，默认端口 8765，`python3 kit/studio.py console` 启动。接口：`GET /api/status`、`GET /api/pending`、`GET /api/checkpoint/<id>`、`POST /api/checkpoint/<id>/resolve`、`POST /api/checkpoint/<id>/edit-script`、本地媒体文件只读访问（限 `STUDIO_HOME` 下的 products 目录，防目录穿越）。
2. 页面（单个 HTML，无外部依赖或仅用固定版本 CDN）：
   - 首页：各品阶段（R11a 推导）、预算已花/上限、待确认数量；待办列表按"是否阻塞付费"排序。
   - `benchmark`：封面、链接、播放数据、档位与理由；选择、确认档位。
   - `script`：逐段显示画面提示词、口播、字幕、时长与口播词数上限；可就地修改，保存时后端重新校验，错误逐条显示。
   - `budget`：每个生成单元的模型（来自 `models.select` 的理由）、时长、单价、合计、品剩余预算。
   - `verdict`：成片播放器 + 对标播放器并排；专家问题列表，点时间码跳转；"可用 / 返修 / 放弃"三键，非"可用"必须写原因；显示闸门判定与无证据低分说明。
   - `publish`：是否发布、账号、发布链接。
   - 统计区：专家与人工一致率（`calibrate.py`）、每元产出（人判可用条数 / 花费）。
3. 原 `board.html` 静态看板保留（便于分享），并加一行指向确认台的说明。
4. `references/console.md` 与 `SKILL.md`：人工确认优先用确认台；Agent 在 `studio next` 退出码 3 时告诉用户打开确认台。

## 允许 / 禁止
允许：`kit/engine.py`（只为第 0 步）、`kit/products.py`（如需登记参考图）、`kit/console.py`、`kit/console/*`（静态资源，如需要）、`kit/studio.py`（只加 `console` 子命令）、`kit/dashboard.py`、`kit/production.py`、`kit/calibrate.py`、`kit/tests/**`、`references/console.md`、`references/commands.md`、`SKILL.md`。
禁止：`kit/checkpoints.py` 的写入规则（只调用，不改语义；确需改动先停下说明）、`VERSION`、`CHANGELOG.md`、`.env`、付费调用。

## 验收
- [ ] 单元测试：每个接口的读写、resolve 规则透传（驳回无原因被拒）、脚本编辑校验失败返回错误、目录穿越被拒、只绑定 127.0.0.1。
- [ ] 用 TESPERT 只读副本启动确认台，用浏览器走一遍：看到试样的 `verdict` 节点，能播放成片、点时间码跳转；在副本里完成一次判定并确认写入日志。PR 贴截图。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
