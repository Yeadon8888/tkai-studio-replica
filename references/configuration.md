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
可单独设置的环节：`MODEL_VERIFY`（验证是否同款）、`MODEL_BREAKDOWN`（拆解）、`MODEL_DRAFT`（起草提示词）、`MODEL_REVIEW`（专家审判）、`MODEL_REVISE`（按审判意见改提示词）。实验功能 `prompt_review.py` 使用 `MODEL_CRITIC`。留空就沿用 `MODEL_TEXT`。

**按用途选择视频模型**
```
VIDEO_ROUTE_DEFAULT=minimax-H3
VIDEO_ROUTE_QUALITY=minimax-H3-cinema
VIDEO_ROUTE_FAST=minimax-H3-fast
VIDEO_ROUTE_KEYFRAME=minimax-H3-first-last
VIDEO_ROUTE_REPLACE_PERSON=minimax_h3_zm_titi
VIDEO_ROUTE_MOTION=wan2.2-motion
VIDEO_DURATION=10
VIDEO_RESOLUTION=768p
VIDEO_ASPECT=9:16
```
简报写 `video_route`：`default | quality | fast | keyframe | replace_person | motion`；不写时用 `default`。
能力、字段和限制见 [视频模型与用途路线](video-models.md)。`MODEL_VIDEO` 与
`VIDEO_PRICE_CNY_PER_SECOND` 只供旧 `.env` 兼容。

价格表是 JSON，键为公开模型名与基础分辨率，金额单位是人民币元/请求秒：
```
VIDEO_PRICES={"minimax-H3":{"768p":0.13},"minimax-H3-cinema":{"480p":0.10,"768p":0.13,"960p":0.23},"minimax-H3-fast":{"480p":0.10,"768p":0.13},"minimax-H3-first-last":{"480p":0.10,"768p":0.13},"minimax_h3_zm_titi":{"768p":0.25}}
```
`768p竖`、`768p横` 按 `768p` 查价。没有确认价格的模型不要写入价格表，账本的
`estimate_cny` 保持 `null`。旧账本已有金额不会重算。

**换首帧图片模型与单张估价**
```
MODEL_IMAGE=gpt-image-2
IMAGE_PRICE_CNY=
```
`MODEL_IMAGE` 用于产品首帧的文件编辑接口。`IMAGE_PRICE_CNY` 是人工确认的单张人民币估价；价格未知时留空，账本的 `estimate_cny` 记为 `null`。使用方法和风险见 [产品首帧路线](keyframe.md)。

**改预算与上限**
```
PRODUCT_BUDGET_CNY=5          # 每个品的默认预算
LIMIT_TIKHUB_CALLS=100        # 单次查询的请求上限
LIMIT_TEXT_CALLS=100          # 单个运行的文本模型请求上限
MAX_ATTEMPTS=3                # 生成—审判—修订循环的唯一轮数上限
REVIEW_MAX_TOKENS=6000        # 每位专家评审的输出 token 上限（推理 token 也计入，太小会截断）
VIDEO_MIN_TOKENS=150          # 视频至少占多少 token 才算模型真的读到；没读到就作废并改抽帧
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
多段视频逐段使用各自任务记录的 `remote_url` 评审，父成片只做时长、分辨率、音轨和拼接点机器检查；
父判定汇总各段，问题时间码会换算成整条视频时间。

模型没有读到视频时（按 token 判断，常见原因是链接会跳转或被托管方限流），这一轮作废，自动改抽帧重评，评审记录写 `video_fallback`。新成片优先用生成服务返回的 `remote_url`，它最可靠。

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
注意：`products/` 和 `ledger/` 跟随 `STUDIO_HOME`；新运行由 `products.py new-run` 建在 `products/<品>/runs/`。旧 `projects/` 可继续作为只读输入显式传给命令。

## 换供应方

- **接口与 OpenAI 兼容**（`/v1/chat/completions`、`/v1/videos`）：改 `TOKENSFACTORY_BASE_URL` 和密钥即可。
- **接口形状不同**：需要改代码。文本请求在 `kit/loop.py` 的 `gemini()`，视频请求在 `_api()` 和 `cmd_gen()`，搜索在 `kit/tt_research.py` 的 `Client`。改完补测试，发一个次版本。

## 评分标准不在 .env

通过线、维度权重、专家清单、提示词标准在 `kit/config/*.yaml`。它们属于规则，改动需要人批准并写进 `CHANGELOG.md`。

## 配置版本

`CONFIG_VERSION` 表示 `.env` 的格式版本。代码要求的版本变了，doctor 会报阻塞，并在 `CHANGELOG.md` 里写明怎么迁移。
