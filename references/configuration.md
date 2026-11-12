# 配置（.env）

所有可替换的东西都在 `.env`。优先级：进程环境变量 > `.env` > `kit/settings.py` 的默认值。
`.env` 默认在 skill 根目录；也可以用环境变量 `STUDIO_ENV` 指向别处。

改完任何一项，运行：

```bash
python3 kit/doctor.py --online
```

## 常见操作

**换文本/理解模型（所有环节一起换）**
```
MODEL_TEXT=<新模型名>
```

**只换某个环节的模型**（例如审判用更强的模型，其余不变）
```
MODEL_REVIEW=<新模型名>
```
可单独设置的环节：`MODEL_VERIFY`（验证是否同款）、`MODEL_BREAKDOWN`（拆解）、`MODEL_DRAFT`（起草提示词）、`MODEL_CRITIC`（给自写脚本挑错）、`MODEL_REVIEW`（专家审判）、`MODEL_REVISE`（按审判意见改提示词）。留空就沿用 `MODEL_TEXT`。

**换视频模型**（模型名和单价一起改）
```
MODEL_VIDEO=<新模型名>
VIDEO_PRICE_CNY_PER_SECOND=<元/秒>
VIDEO_DURATION=10
VIDEO_RESOLUTION=768p
VIDEO_ASPECT=9:16
```
旧账本行的金额不会变：`minimax-H3` 的历史价格保留在 `kit/ledger.py`。

**改预算与上限**
```
PRODUCT_BUDGET_CNY=5          # 每个品的默认预算
LIMIT_TIKHUB_CALLS=100        # 单次查询的请求上限
LIMIT_TEXT_CALLS=100          # 单个运行的文本模型请求上限
MAX_ATTEMPTS=3                # 生成—审判—修订循环的唯一轮数上限
REVIEW_MAX_TOKENS=2000        # 每位专家评审的输出 token 上限
TIER_WEIGHTS=H:4,M:3,L:2,X:1  # 条数分配权重
FISSION_MAX_N=5               # 单个对标一批最多几条
```

旧配置 `REFINE_MAX_ATTEMPTS` 仍可读取，但只作兼容别名；新配置统一写 `MAX_ATTEMPTS`。

**整条视频评审与临时上传**
```
MEDIA_UPLOAD=github_temp      # 无可用 remote_url 时临时上传，评审后删除
MEDIA_HOST_REPO=owner/repo    # 用户自己的公开临时媒体仓库
```
设为 `MEDIA_UPLOAD=off` 时，如果任务记录也没有可访问的 `remote_url`，评审回退到抽帧并记录 `mode: frames`。

**密钥**
推荐只在 `.env` 里写密钥文件的路径：
```
CREDENTIALS_FILES=~/.config/tokensfactory/credentials.env,~/.config/tikhub/credentials.env
```
这些文件里需要有 `TOKENSFACTORY_API_KEY` 和 `TIKHUB_API_KEY`。也可以把两个密钥直接写进 `.env`（它不进仓库）。

**数据放到别处**
```
STUDIO_HOME=/path/to/data     # products/、ledger/ 的位置；留空 = skill 根目录
```
注意：目前 `products/` 和 `ledger/` 跟随 `STUDIO_HOME`；运行目录（`projects/`）由命令行的 `--project` 指定。

## 换供应方

- **接口与 OpenAI 兼容**（`/v1/chat/completions`、`/v1/videos`）：改 `TOKENSFACTORY_BASE_URL` 和密钥即可。
- **接口形状不同**：需要改代码。文本请求在 `kit/loop.py` 的 `gemini()`，视频请求在 `_api()` 和 `cmd_gen()`，搜索在 `kit/tt_research.py` 的 `Client`。改完补测试，发一个次版本。

## 评分标准不在 .env

通过线、维度权重、专家清单、提示词标准在 `kit/config/*.yaml`。它们属于规则，改动需要人批准并写进 `CHANGELOG.md`。

## 配置版本

`CONFIG_VERSION` 表示 `.env` 的格式版本。代码要求的版本变了，doctor 会报阻塞，并在 `CHANGELOG.md` 里写明怎么迁移。
