# 任务书 R12.2：确认台显示对标封面

分支：`feat/console-cover` · 负责人：Codex · 审核：Claude
**前置**：`main` 已含 R12.1。不发起付费调用，不写主仓库数据目录。

## 问题
确认台 `verdict` 详情显示"没有可用的对标封面"。TESPERT 试样的对标封面实际存在：`<主仓库上一级>/research/tiktok-benchmark/20261113-US-drinking-water-strips/covers/7682136023844818189.jpg`（`shots.json` 的 `aweme_id`）。调研批次在 `STUDIO_HOME` 之外，而确认台只允许读取 `products/` 下的文件（这是对的安全限制，不要放宽成任意路径）。

## 要做
1. 在运行目录里保存对标封面的本地副本 `<运行>/benchmark/cover.jpg`（必要时也放对标视频本地副本 `benchmark/video.mp4`，如调研批次已有）：
   - `shot_list.py` 建镜头表时复制；
   - `studio adopt` 对旧运行补复制（找不到就记录原因，不报错）；
   - 来源：调研批次 `covers/<aweme_id>.jpg`（批次路径从运行的 `shots.json`、`project.json` 或 `BOARD_RESEARCH` 推出）。
2. 确认台的对标回退顺序改为：运行目录本地视频 → 可访问的远程地址 → 运行目录本地封面 → 文字说明。
3. 评审记录缺少时间字段时（旧记录），"最新评审时间"显示"旧记录（按文件时间）"，不要当作真实评审时间。

## 允许 / 禁止
允许：`kit/shot_list.py`、`kit/engine.py`、`kit/console.py`、`kit/console/*`、`kit/tests/**`、`references/console.md`。
禁止：放宽确认台的文件访问范围到 `products/` 之外、`VERSION`、`CHANGELOG.md`、`.env`、付费调用、主仓库数据写入。

## 验收
- [ ] 单元测试：adopt 补复制封面；shot_list 复制封面；回退顺序；旧记录时间说明。
- [ ] TESPERT 副本（含调研批次的封面路径可读）：adopt 后确认台显示 C023 封面。PR 贴截图。
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
