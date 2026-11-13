# 视频模型与用途路线

简报通过 `video_route` 选路线；不写时是 `default`。模型名从 `.env` 的
`VIDEO_ROUTE_*` 读取，不在业务代码里替换模型。所有路线都走 TokensFactory `/v1/videos`。

| 路线 | 默认模型 | 适合 | 不能做 |
|---|---|---|---|
| `default` | `minimax-H3` | 1–9 张图片和可选音频驱动的常规视频 | 不接参考视频；只支持 10/15 秒、768p、9:16/16:9 |
| `quality` | `minimax-H3-cinema` | 需要 960p 的高画质成片 | 不接参考视频；1–15 秒，最高 960p |
| `fast` | `minimax-H3-fast` | 1–15 秒、480p/768p 的快速路线 | 不接参考视频，不支持 960p |
| `keyframe` | `minimax-H3-first-last` | 已有首帧和尾帧，要生成中间过渡 | 不能用通用 `images`、音频或参考视频；首尾帧和提示词都必填 |
| `replace_person` | `minimax_h3_zm_titi` | H 档对标有人物、产品在画面里不重要，只替换单个人物 | 不能换产品、不能多人物、不能加音频或首尾帧；输出 5–25 秒且不能超过参考视频 |
| `motion` | `wan2.2-motion` | 一张人物图跟随公开参考视频的动作 | 不是产品替换；价格未确认时账本金额未知，已登记品的运行会因无法预算而停止 |

## 简报字段

- `default` / `quality` / `fast`：`prompt`、`images`（1–9）、可选 `audio_urls`（最多 3）、
  `duration`、`resolution`、`aspect_ratio`。
- `keyframe`：`first_frame`、`last_frame`、`prompt`、`duration`、`resolution`。
  `keyframe.py` 生成首帧后，只有简报同时提供尾帧才自动切到这条路线。
- `replace_person`：`ref_image`、公开 `ref_video`、`ref_video_duration`、可选短 `prompt`、
  `duration`、`resolution`；还必须明确 `tier: H`、`reference_has_person: true`、
  `product_in_frame_not_important: true`。系统会在简报和看板标记“仅换人，未换产品”。
- `motion`：`ref_image`、公开 `ref_video`、`resolution`。

所有图片字段可以是公开 JPG/PNG/WebP 地址、图片 data URI 或本地图片路径；本地图片只在请求时转成
data URI。参考视频必须是无需登录即可读取的 HTTP(S) 地址。非法时长、分辨率、图片数量或缺失字段会在
付费提交前被拦下。

`minimax_h3_zm_titim` 虽然出现在模型列表，但截至 2026-11-13 公开文档没有输入格式，因此不接入，
也不推测它的字段。

## 费用与预算

价格由 `.env` 的 `VIDEO_PRICES` 按“模型 × 基础分辨率”读取。生成任务的账本行同时保存模型、分辨率、
请求秒数和估算金额。运行登记到某个品后，每一条及每一段在 POST 前调用品预算检查；余额不足不提交。
多段父成片只汇总各段实际账本金额，不按父时长再估一份。
