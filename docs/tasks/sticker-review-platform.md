# 任务书 R16.2-中台：制作台显示与编辑回复评论贴纸、形式说明和神韵预估

仓库：`content-dashboard-next`（本地 clone，分支 `feat/sticker-review`）· 负责人：Codex · 审核：Claude
**授权边界**：只在本地开发和测试；**不 push、不开 PR、不部署、不远程迁移**；在 clone 里本地 commit。不调用付费模型或 TikHub。先读仓库 `AGENTS.md`、`harness/policies/current.md`，按 harness 要求建 run 留证。
**约定**：`/Users/yeadon_1/Desktop/MyProject/tkAI视频增长中台/studio/docs/tasks/sticker-review-studio.md` 第 1 点（执行机一侧由另一个 Codex 同时实现）。

## 问题（证据）
真实请求第 3 版脚本节点：S01 的 `onscreenCopy` 是对象 `{"style":"reply_sticker","handle":"@daily_sip_check","comment":"Is that cloudy stored water…"}`，`src/v1/production-desk.tsx` 把它当字符串塞进 textarea，**"画面文字"列显示为空**——这是对标神韵的核心钩子，人看不到就没法判断。payload 里的 `format`（"对标无字幕；对标无人声；钩子是回复评论贴纸（出现在 S01）；无 CTA；品名出现 0 次"）和 `essencePreview` 也没显示。

## 要做（`src/v1/production-desk.tsx` 及必要的样式、类型）
1. `onscreenCopy` 为对象且 `style==='reply_sticker'`：画面文字列渲染一个贴纸预览（白色圆角气泡、深色字；第一行小字 `Reply to <handle>'s comment`，第二行评论正文；不画 TikTok 标志），下方两个可编辑输入：用户名、评论；提交 `edits` 时该段 `onscreenCopy` 发对象（保留 `style`），只在改过时发送。字符串保持现状。
2. 表格上方"对标形式"一行显示 `format` 原文（字符串）；缺失时不显示。
3. "花钱前神韵预估"卡片：分数百分比、`passCount/total`、逐项（标签、通过/未通过/待定、说明）；`essencePreview` 为 null 且有 `essencePreviewError` 时显示该原因；全部缺失时不显示。
4. 客户不可见：这些都只在内部制作台显示（沿用现有权限）。
5. 测试：扩展 `scripts/test-production-mvp.mjs`（对象型 onscreenCopy 的显示数据与编辑格式）；UI 测试覆盖贴纸渲染。
6. 浏览器实操（独立端口如 8896，不占 8886）：用假 payload 建一个含贴纸、format、essencePreview 的脚本节点，截图；改评论后提交，确认 `edits` 格式。

## 验收
- [ ] `test-production-mvp`、`test:production`、`test:ui`、`npm run build`、`check:bundle` 通过。
- [ ] 截图与 harness run 证据；本地 commit；中文汇报 commit、改动、自测、截图位置、风险。
