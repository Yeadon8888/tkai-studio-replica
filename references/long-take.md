# 长镜头与连续动作复刻

## 原则

先看整条视频的钩子、证明、行动等叙事结构，再按真实切镜拆镜。不要为了迁就模型把镜头硬切成 3–6 秒。能在 15 秒内一次生成的连续动作只提交一次；超过 15 秒才在镜头边界处分块，单镜头本身超过 15 秒时才按时间均分。

`shots.json` 的 `join` 表示与上一镜头的关系：`cut` 是硬切，`continuous` 是同一连续镜头，`match_cut` 是动作匹配切。`keep`/`redo` 与 `redo_reason` 决定照做还是换内容，`end_state` 记录结束瞬间手、产品、道具的位置。非法值会直接阻塞。

## 脚本与分组

`script_writer.py` 用 `MODEL_SCRIPT`（留空沿用 `MODEL_TEXT`）一次生成共用 `scene` 和全部镜头的画面、口播、字幕、画面文字、结束状态。代码检查产品事实、竞品词、口播速度、口播段数和画面提示词中的文字渲染要求。第一次失败时把错误清单交回同一模型完整重写一次；第二次仍失败就停止，Agent 不手补。

连续的 `continuous` 镜头合成一个生成单元；`cut` 和 `match_cut` 开新单元。单元超过 15 秒时形成同一 `chain_group` 的多个块。第一块走 cinema 质量路线，后续块走 first-last 首尾帧路线。`match_cut` 新单元也用上一段尾帧接力。

## 定场图、接力和 seed

先用 `keyframe.py scene` 根据 `script.json` 的 `scene` 和真实产品图生成 `scene.png`。每个 cinema 请求把它放在 `images[0]`，产品图放在后面，确保各段处在同一世界。

接力块在计划中使用 `first_frame: chain:prev`。生成时必须等上一段视频落盘，再用 ffmpeg `-sseof` 抽取最后一帧到 `chain/<段>-first.png`；随后用 `MODEL_IMAGE` 和本块 `end_state` 编辑终点图到 `chain/<段>-last.png`，最后才提交 first-last 请求。上一段失败或接力帧失败时，本段不提交，原因写入任务记录。

一次运行生成一个大于等于 1 的 seed，cinema 与 first-last 请求共用。计划按 `VIDEO_PRICES` 记录每段预计金额和总额；价格未知时总额保持未知，不编造。

## 已知风险

- 15 秒以后分块接力的稳定性尚未做付费实测。
- 多次接力后，产品细节、手部和道具可能逐步漂移。
- 定场图和每次终点图都会产生图片编辑费用，必须在明确授权后生成。
- 960p cinema 与最高 768p 的 first-last 段会在拼接前统一缩放到成片尺寸。
