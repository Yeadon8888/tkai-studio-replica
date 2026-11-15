# 任务书 R15：中台 ↔ 执行机 节点数据约定 v1（两边各一份实现）

审核：Claude · 用户 2026-11-15 在中台实测发现：脚本节点时长全显示 0 秒、字幕为空、画面文字与配乐描述看不到、旧版脚本仍显示"待处理"。原因：两边字段名不一致，且中台没有"作废"状态。

## 约定 v1（两边都以此为准，写进各自文档）
**脚本节点 payload（执行机 → 中台）**
```json
{"contractVersion":1,"audioMode":"music_only","scene":"...","music":{"mood":"...","tempo_bpm":72,"era_style":"...","instruments":["..."],"energy_curve":"..."},
 "segments":[{"index":0,"id":"S01","durationSeconds":11.5,"visualPrompt":"...","voiceover":"","subtitle":"...","onscreenCopy":"...","audioRoute":"music"}]}
```
**脚本修改（中台 → 执行机，节点 `edits`）**
```json
{"segments":[{"index":0,"voiceover":"...","subtitle":"...","onscreenCopy":"..."}]}
```
只传被改过的段与字段。执行机映射：`index` → 脚本第 index 段，`subtitle` → `caption`，`onscreenCopy` → `onscreen_copy`，`voiceover` → `voiceover`；不认识的字段报校验错误。
**预算节点 payload（执行机 → 中台）**
```json
{"contractVersion":1,"units":[{"label":"S01 视频","model":"minimax-H3-cinema","durationSeconds":12,"unitPriceCny":0.23,"totalCny":2.76}],"totalCny":4.95,"requestBudgetCny":5}
```
配乐、定场图、TTS 各占一行（`durationSeconds` 可为空，`unitPriceCny` 为单次价）。`totalCny` 为空表示有未知价格，中台禁止批准。
**作废**：执行机调用 `POST /api/agent/production-requests/:id/checkpoints/:externalId/supersede`，`{"reason":"..."}`；中台把该节点标记作废（`superseded_at`、`superseded_reason`），作废节点不能再被决定（409 `CHECKPOINT_SUPERSEDED`），页面默认折叠为"已作废（第 N 版）"。

## 附加字段

脚本节点 payload 可以附加以下字段；它们不改变 v1 既有字段的含义，旧中台可以忽略：

- `format`：字符串。由 `format.json` 生成的中文形式说明，例如“对标无字幕；对标无人声；钩子是回复评论贴纸（出现在 S01）；无 CTA；品名出现 0 次”。
- `essencePreview`：`{"score":1.0,"items":[{"label":"字幕有无","status":"pass","detail":"..."}]}`。`score` 是付费前纯代码预估分；`items` 使用中文标签，`status` 为 `pass | fail | unclear`。
