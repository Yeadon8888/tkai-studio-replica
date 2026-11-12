# 人工判定与专家校准

- `human_decisions.jsonl`：每行一条人工决定（格式见 `studio/schemas/video_hitl_decision.schema.json`）。只追加，不改写旧行。
- 专家判定来自 `projects/<项目>/reviews/<take>.decision.json`。
- 运行 `python3 kit/calibrate.py --decisions kit/calibration/human_decisions.jsonl --projects <项目目录>...`，输出：
  - 专家 pass / repair 与人工 accept / iterate / redo 的一致率；
  - 不一致的条目列表（用于改 experts.yaml 的 prompt，或提出 candidate_rule_changes）。
- 规则：改动专家 prompt 之前，先看不一致样本；新 prompt 只在下一批对照验证后才替换旧版。
