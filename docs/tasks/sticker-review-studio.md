# 任务书 R16.2-执行机：贴纸可审可改、神韵预估随脚本上报

分支：`feat/sticker-review-studio` · 负责人：Codex · 审核：Claude
**前置**：`main` 含 R16（PR #23）与 `bf58fcc`。动手前 `git pull`。不调用任何付费接口（含文本模型）。不改中台仓库（中台一侧由另一个 Codex 同时做，约定以本文第 1 点为准）。

## 问题（证据）
- 真实请求 `bf34c96b-…` 的第 3 版脚本节点 payload 有 `format`，但**没有 `essencePreview`**：`platform_runner._checkpoint_payload`（第 ~127 行）只读已存在的 `essence_preview.json`，引擎重写脚本后没人生成它。
- `onscreenCopy` 现在可能是对象 `{"style":"reply_sticker","handle","comment"}`；中台 `edits` 映射（第 ~188 行）和 `checkpoints.edit`（`kit/checkpoints.py` 第 ~157 行）只按字符串处理——人在中台改贴纸评论会被代码校验挡掉（屏幕文字形式变成 text_overlay）。

## 要做
1. **约定 v1 追加**（`docs/tasks/checkpoint-contract.md` 末尾"附加字段"，只追加）：
   - 脚本 payload 段内 `onscreenCopy` 可为字符串或 `{"style":"reply_sticker","handle":"@x","comment":"..."}`；
   - `edits.segments[].onscreenCopy` 同样可为字符串或该对象；对象时 `style` 必须保持 `reply_sticker`；
   - `essencePreview`: `{"score":0-1,"passCount","total","items":[{"label","status":"pass|fail|unclear","detail"}]}`（已有字段名如不同，以现有实现为准并写清）。
2. **上报前必算预估**：`_checkpoint_payload` 组装脚本节点时调用 `essence.preview(run_dir)`（零成本代码判定）并写 `essence_preview.json`；失败时 payload 写 `essencePreview: null` 并在 `essencePreviewError` 给出中文原因，不中断上报。
3. **贴纸编辑**：中台 `edits` 里对象型 `onscreenCopy` → 本地 `onscreen_copy` 对象；`checkpoints.edit` / `studio edit-script` 允许 `onscreen_copy` 为对象（CLI 增加 `--json-value`）；编辑后照常跑 `script_writer.validate`（含照抄评论、用户名规则），失败按现有逻辑回写中台。
4. 测试：payload 含预估；预估异常不中断；对象编辑通过；改成字符串被形式校验挡住并回写；照抄对标评论被挡。

## 允许 / 禁止
允许：`kit/platform_runner.py`、`kit/checkpoints.py`、`kit/studio.py`、`kit/essence.py`（只为暴露 preview 所需的小改）、`kit/tests/**`、`docs/tasks/checkpoint-contract.md`（只追加）、`references/platform.md`。
禁止：`VERSION`、`CHANGELOG.md`、`.env`、中台仓库、付费调用、真实运行目录。

## 验收
- [ ] `python3 -m unittest discover -s kit/tests` 全部通过。
- [ ] 交付：自己 commit、push 分支、`gh pr create`；PR 写自测与已知问题；不合并、不 push main；不输出密钥。
