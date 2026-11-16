# 声音路线

每个生成单元的 `audio_route` 只能表达一种声音职责。

| 路线 | 使用场景 | 生成与合成规则 |
|---|---|---|
| `native` | 出镜角色说话，包含反派等特殊角色 | 保留 H3 原声；完整台词和语气必须已写入画面提示词 |
| `tts` | 画外音，或说话角色不在画面中的特殊配音 | 丢弃对应单元的原声，使用脚本口播生成 TTS；有配乐时压低配乐 |
| `music` | `audio_mode: music_only` | 画面提示词由代码追加“无对话/无歌唱”；合成时彻底丢弃 H3 原声，只铺原创纯音乐 |
| `ambient` | 只要环境声 | 同样追加“无对话/无歌唱”，但保留 H3 的自然环境声 |

## 曲库复用

曲库索引位于 `STUDIO_HOME/assets/music/library.jsonl`。每条记录包含 `id`、`file`、
`style`、`duration`、`source`、`task_id`、`created` 和 `license_note`。引擎先匹配
`mood + era_style`，并确认时长不短于成片；命中就直接复用，预算为 0 元。

未命中时，预算节点列出“配乐：新生成 2.5 元”。批准后，视频生成完成才提交
`suno_music_open`。任务 ID 立即写入运行目录的 `jobs/music-<id>.json`；后续只轮询该任务，
未知状态或提交结果不明时不重提。成功后下载第一首，用 `ffprobe` 核验时长再入库。

配乐描述只能写心情、速度、时代风格、乐器和能量曲线，不得出现歌名、艺人名、
`in the style of` 或 `like <name>`。

## 质检

`music` 路线的 V 专家必须完整听取成片。Hydrogen 听写到任何对话、口播或歌唱时，
以 `cause: audio_unwanted_voice` 失败，退回合成。
