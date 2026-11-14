# 流域中台执行机

中台保存项目、请求、人工决定和成片版本；studio 在本机执行拆解、脚本、生成、合成与评审。执行机只按引擎给出的下一步推进，不替人批准脚本、预算或成片。

## 创建执行机令牌

1. 用 `platform_admin` 或 `content_operator` 登录中台，在设置中的 Agent 令牌页面新建令牌。
2. 同时勾选 `production:run` 和 `uploads:write`。前者用于制作请求，后者用于 `POST /api/v3/uploads` 上传成片。
3. 令牌只显示一次。把完整的 `ffa_` 令牌写进本机私有文件，然后收紧权限：

```bash
mkdir -p ~/.config/flowfield
chmod 700 ~/.config/flowfield
printf '%s\n' '在这里粘贴令牌' > ~/.config/flowfield/platform-runner.token
chmod 600 ~/.config/flowfield/platform-runner.token
```

不要把令牌写进 `.env`、仓库、日志或 PR。执行机会拒绝权限不是 `600` 的令牌文件。

## 配置

在仓库 `.env` 中配置以下值。`STUDIO_HOME` 指向主仓库数据目录，确保产品、运行和账本仍落在同一处。

```dotenv
STUDIO_HOME=/Users/yeadon_1/Desktop/MyProject/tkAI视频增长中台/studio
PLATFORM_BASE_URL=http://127.0.0.1:8886
PLATFORM_TOKEN_FILE=~/.config/flowfield/platform-runner.token
RUNNER_ID=
MODEL_ANCHOR=
IMAGE_PRICE_CNY=
TTS_PRICE_CNY=
```

`RUNNER_ID` 留空时使用主机名。`MODEL_ANCHOR` 留空时沿用 `MODEL_TEXT`。产品锚点模型必须根据产品图输出一句英文，并逐字包含中台产品名。

预算节点会分别列出视频、定场图和 TTS。`IMAGE_PRICE_CNY` 或需要 TTS 时的 `TTS_PRICE_CNY` 未配置，预算总额会保持未知，不应批准；先补齐经确认的人民币价格。请求预算还必须不超过 `PRODUCT_BUDGET_CNY` 单品上限。

## 启动

先用不写文件、不调用上游的假流程检查步骤序列：

```bash
python3 kit/platform_runner.py --once --dry-run
```

处理当前一轮后退出：

```bash
python3 kit/platform_runner.py --once
```

常驻轮询，默认每 20 秒一次：

```bash
python3 kit/platform_runner.py --loop
```

`Ctrl-C` 会在当前 Python 步骤边界安全退出。请求状态保存在 `STUDIO_HOME/platform-runner/`；重启后先恢复等待人工或等待审核的请求，再领取新请求。成片上传 ID 由请求和成片确定性派生，重启不会制造重复版本。

## 人工节点与失败规则

- `script`：中台展示逐段时长、画面提示词、口播、字幕和声音路线。中台修改会先走本地脚本校验；失败时执行机创建一个带校验错误的新待处理节点，原脚本不覆盖。
- `budget`：中台展示每个视频单元、定场图和 TTS 明细。批准额度仍受请求预算与本地品预算双重限制。
- `verdict`：不单独上报确认节点。成片上传并 `complete` 后，执行机读取现有审核结果，写回本地 verdict 节点和 `human_decisions.jsonl`。
- 上游失败不会换模型或自动重提。已有视频任务先查询原任务；失败原因上报为 `failed`。

## 常见故障

| 现象 | 处理 |
|---|---|
| `令牌文件权限必须为 600` | 对令牌文件执行 `chmod 600`，不要放在共享目录。 |
| HTTP 403 | 重新创建同时包含 `production:run`、`uploads:write` 的内部角色令牌。 |
| HTTP 409 领取冲突 | 请求已被另一执行机领取；本机跳过，不强抢。 |
| 请求预算超过单品上限 | 调低中台预算，或由负责人明确调整 `.env` 的 `PRODUCT_BUDGET_CNY` 后重新建请求。 |
| 预算金额未知 | 补齐经确认的 `IMAGE_PRICE_CNY` / `TTS_PRICE_CNY` 或模型价格；不要猜价格。 |
| 脚本修改校验失败 | 在中台新待处理节点按错误修正；本地原脚本仍保留。 |
| 视频生成超时或网络中断 | 重新运行执行机；它会读取原 task id 查询，不会再次提交。 |
| 成片已上传但没有结束 | 现有审核尚未给出 `approved`、`changes_requested` 或 `rejected`，执行机会继续轮询。 |

