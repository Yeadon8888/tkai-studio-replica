# 视频模型与用途路线

模型能力、价格、策略和验证依据的唯一来源是 [`kit/config/models.yaml`](../kit/config/models.yaml)。
本页只说明如何使用目录，不再复制一份容易过期的限制表。

固定路线继续从 `.env` 的 `VIDEO_ROUTE_*` 读取模型：`default`、`quality`、`fast`、
`keyframe`、`replace_person`、`motion`。付费提交前，`kit.models.check_request` 会按目录检查
时长、分辨率、宽高比、图片/音频数、首尾帧、参考视频和 seed。显式指定的模型不满足需求时直接报错，
不会静默换模型。

简报写 `video_route: auto` 时，代码从 `policy: allowed`、`MODELS_ALLOWED` 白名单和最近体检健康的模型中，
选择满足简报需求且目录估价最低的一项。需要 `ask_first` 模型时，必须由人确认后在简报写
`allow_ask_first: true`；`retired` 模型不能选择。

常用简报需求字段：`duration`、`resolution`、`aspect_ratio`、`images`、`audio_urls`、`first_frame`、
`last_frame`、`ref_video` 和 `seed`。`replace_person` 的 H 档、单人物和参考视频时长等业务条件仍会在请求
组装阶段检查。

价格优先读取 `.env` 的 `VIDEO_PRICES`，用于兼容现有部署；没有显式配置时从模型目录读取。
未知价格保持 `null`，不猜测，也不会被描述成“最便宜”。
