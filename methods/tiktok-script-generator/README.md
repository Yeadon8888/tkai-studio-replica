# TikTok 脚本生成（X 档：自写脚本）

- 来源：用户本地文件 `tiktok_script_generator_skill (1).md`（Downloads，原作者与许可证未确认）。
- 用途：X 档（不参考、自建方法库）视频的脚本与双轨提示词。不用于 H/M/L 档复刻。
- 公开到 GitHub 前必须先确认来源与授权。
- Macro 开关：由 agent 按每条视频判断。镜头里的机制肉眼不可见（化学、生物）时开启，走微观 3D 单轨；纯物理用品（数码、家居）关闭，全程实拍双轨。判断依据写进该视频的脚本说明。
- 产品锚点：每个镜头的提示词必须逐字包含 `studio/projects/<项目>/product_anchor.txt` 中的产品锚点，不由 skill 自行改写产品细节。

## 引导式流程（kit/own_script.py）

1. `python3 kit/own_script.py init --id <id> --out projects/<项目>/scripts/<id>.json`：生成带提示的骨架。
2. 按 SKILL.md 顺序填写：敌人 → 痛点 → 机制 → Macro 与理由 → 镜头（时间轴、台词、提示词）。
3. `python3 kit/own_script.py check --script <脚本> --project projects/<项目>`：计算型自检。阻塞项必须清零。
   条款：必填字段；时间轴连续；产品不早于 25% 出现；锚点逐字；单手约束；第一个镜头有敌人。
4. `python3 kit/own_script.py brief --script <脚本> --project projects/<项目>`：通过后写入 `briefs/X-<id>.json`，与 H/M/L 同结构，可直接进入 gen。
   需要项目 `refs/` 下有产品参考图（png）。

示例：`examples/X-sample-pool-strips.json`（通过自检，未生成视频）。
