# 任务书 R12：本地确认台（平台界面雏形）

分支：`feat/console` · 负责人：Codex · 审核：Claude
**前置**：`main` 已含 R11a（`kit/checkpoints.py`、`kit/engine.py`、`kit/studio.py`）与 R11b（`kit/models.py`）。动手前 `git pull`，确认 `kit/checkpoints.py` 存在；否则停下说明。本任务不发起付费调用。

## 目标
把看板升级成"待办 + 确认"页面：人在页面上完成 R11a 的六类确认，所有写入都走 `checkpoints.resolve`（不得另写一套）。这是将来平台的界面雏形，所以页面只通过一层 JSON 接口读写，接口与 `studio.py --json` 的结构一致。

## 要做
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
允许：`kit/console.py`、`kit/console/*`（静态资源，如需要）、`kit/studio.py`（只加 `console` 子命令）、`kit/dashboard.py`、`kit/production.py`、`kit/calibrate.py`、`kit/tests/**`、`references/console.md`、`references/commands.md`、`SKILL.md`。
禁止：`kit/checkpoints.py` 的写入规则（只调用，不改语义；确需改动先停下说明）、`VERSION`、`CHANGELOG.md`、`.env`、付费调用。

## 验收
- [ ] 单元测试：每个接口的读写、resolve 规则透传（驳回无原因被拒）、脚本编辑校验失败返回错误、目录穿越被拒、只绑定 127.0.0.1。
- [ ] 用 TESPERT 只读副本启动确认台，用浏览器走一遍：看到试样的 `verdict` 节点，能播放成片、点时间码跳转；在副本里完成一次判定并确认写入日志。PR 贴截图。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
