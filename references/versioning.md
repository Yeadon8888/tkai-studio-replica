# 版本管理

## 版本号

`VERSION` 文件是唯一来源，格式 `主.次.修`。1.0.0 之前表示流程还在验证。

| 改了什么 | 升哪一位 |
|---|---|
| `.env` 格式不兼容、数据目录结构不兼容、删掉命令 | 主版本，同时升 `CONFIG_VERSION` |
| 新增环节或命令、换默认模型、改评分标准或专家提示词、改通过线 | 次版本 |
| 修 bug、改文档、改措辞，不影响判定结果 | 修订版本 |

评分标准和默认模型的变化会改变"同一条视频的判定结果"，所以算次版本，不算修订。

## 发版步骤

1. 跑测试：`python3 -m unittest discover -s kit/tests`
2. 跑体检：`python3 kit/doctor.py --online`
3. 改 `VERSION`，在 `CHANGELOG.md` 顶部加一节（日期、变更、是否需要迁移）。
4. 提交，打标签并推送：

```bash
git tag -a v$(cat VERSION) -m "studio-replica $(cat VERSION)"
```
```bash
git push origin main --tags
```

## 变更记录怎么写

每个版本分四类，没有的类别不写：
- **新增**：新环节、新命令、新配置项。
- **变更**：默认模型、评分标准、专家提示词、通过线。写明"旧 → 新"和原因。
- **修复**：bug。
- **迁移**：升级的人需要做什么（改 `.env` 的哪一项、是否要重跑评审）。

## 结果怎么对应到版本

- 每条评审记录（`reviews/<id>.json`）都带 `versions`：skill 版本、配置版本、各环节的模型名。
- 换了模型或标准之后，旧记录不会被改写。需要可比的结果时，用 `review_panel.review_take` 重评（旧结果先备份）。

## 依赖

- Python 依赖在 `kit/requirements.txt`。
- 外部工具：ffmpeg、ffprobe。
- 外部服务：`.env` 里的供应方与模型。模型下线或改名时，doctor 的联网检查会报出来。
