# studio-replica

按品做 TikTok 带货视频的完整流程：选品与产品事实 → 找对标（调研或手动给链接/视频）→ 拆解定档（H/M/L/X）→ 模型起草提示词 → 生成视频 → 三位专家审判 → 不达标则修改提示词再生成 → 看板。

## 安装（三步）

1. 解压，进入文件夹。
2. 运行：
   ```bash
   bash setup.sh
   ```
   它会检查 Python 和 ffmpeg、安装依赖，然后问你几个问题：
   - TokensFactory 密钥（必填）
   - TikHub 密钥（只用手动对标可以不填）
   - 你的公开 GitHub 仓库（只有要处理本地视频时才需要；仓库要先在 GitHub 上建为公开仓库）
   - 每个品的默认预算（默认 5 元）

   密钥输入时不会显示，写入 `.env`（权限 600），不会被打印或提交。
3. 完成后会自动运行体检。全部通过即可使用。

不想交互？编辑 `.env`（参考 `.env.example` 里的注释），然后运行：
```bash
python3 kit/doctor.py --online
```

## 日常使用

- 在 agent 里直接说："为某某品做视频，对标用这两个链接"，agent 会按 `SKILL.md` 的流程走。
- 新运行：`python3 kit/products.py new-run --product <品id> --name <名称>`，目录会建在品下并自动登记。
- 看板：`python3 kit/loop.py board --project <运行目录>`，生成 `board.html`，用浏览器打开。
- 换模型、改预算：只改 `.env`，然后重新运行体检。

## 文件说明

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 流程与规则（agent 读这个） |
| `references/` | 架构、配置、档位、专家标准、版本管理、本地视频处理等 |
| `.env.example` | 所有可配置项与默认值 |
| `setup.sh`、`kit/setup_wizard.py` | 一键安装与配置 |
| `kit/doctor.py` | 体检 |
| `kit/` | 代码、评审标准（`kit/config/`）、测试（`kit/tests/`） |
| `products/` | 每个品的数据（新品会在这里生成子文件夹） |
| `CHANGELOG.md`、`VERSION` | 版本与变更记录 |

## 安全

- 密钥只放在 `.env` 或你指定的密钥文件里，不要提交到公开仓库。
- 本地视频只在你同意时上传到你自己的公开仓库，拆解后立即删除。
- 运行数据（`products/<品>/runs/`、`ledger/`、`media/`）只留在本机；旧 `projects/` 不迁移、不删除。

## 版本

见 `VERSION` 和 `CHANGELOG.md`。升级时先备份 `.env` 与 `products/`，再替换代码。
