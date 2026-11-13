#!/usr/bin/env python3
"""整条对标视频 → 连续镜头表 ``shots.json``。"""
import argparse
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402
import loop  # noqa: E402
import tt_research as tt  # noqa: E402


PROMPT = """你是短视频导演。完整观看并听完给出的对标视频，按真实切镜和叙事节拍输出镜头表。
每个镜头尽量为 3–6 秒；起止时间必须首尾相接、覆盖整条视频，不得有空隙或重叠。
只依据画面和声音，看不清或听不清就写空字符串，不要编造。
只输出一个 JSON 对象：
{"duration": 18.2, "summary": "为什么整条视频抓人", "shots": [
 {"id":"S01","start":0.0,"end":4.2,"scale":"景别","camera":"机位与运动",
  "action":"动作","product_role":"subject|supporting|none","hand_action":true,
  "onscreen_text":"原画面文字","voiceover":"口播原文","music_sfx":"音乐与音效",
  "rhythm":"节奏点","hook_reason":"为什么抓人"}]}
"""

REQUIRED = ("id", "start", "end", "scale", "camera", "action", "onscreen_text",
            "voiceover", "music_sfx", "rhythm", "hook_reason")


def validate(data, tolerance=0.08):
    """校验镜头表结构与连续时间轴，返回规范化副本。"""
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list) or not data["shots"]:
        raise ValueError("shots 必须是非空列表")
    try:
        duration = float(data["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("duration 必须是正数") from exc
    if duration <= 0:
        raise ValueError("duration 必须是正数")
    shots, cursor = [], 0.0
    for index, raw in enumerate(data["shots"], 1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 个镜头不是对象")
        missing = [key for key in REQUIRED if key not in raw]
        if missing:
            raise ValueError(f"第 {index} 个镜头缺字段：{', '.join(missing)}")
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 个镜头时间不是数字") from exc
        if end <= start:
            raise ValueError(f"第 {index} 个镜头结束时间必须晚于开始时间")
        if abs(start - cursor) > tolerance:
            kind = "空隙" if start > cursor else "重叠"
            raise ValueError(f"第 {index} 个镜头与上一镜头有{kind}：{cursor:g} → {start:g}")
        row = dict(raw, id=f"S{index:02d}", start=round(cursor, 3), end=round(end, 3))
        row["product_role"] = row.get("product_role") if row.get("product_role") in ("subject", "supporting", "none") else "none"
        row["hand_action"] = bool(row.get("hand_action"))
        shots.append(row)
        cursor = end
    if abs(cursor - duration) > tolerance:
        raise ValueError(f"镜头表结尾 {cursor:g} 与视频时长 {duration:g} 不一致")
    shots[-1]["end"] = round(duration, 3)
    return {**data, "duration": round(duration, 3), "shots": shots}


def candidate_source(batch, candidate_id, reachable=tt.media_reachable):
    batch = Path(batch)
    candidates = read_json(batch / "candidates.json", [])
    candidate = next((row for row in candidates if row.get("candidate_id") == candidate_id), None)
    if not candidate:
        raise SystemExit(f"调研批次里没有 {candidate_id}")
    urls = list(candidate.get("play_urls") or [])
    if candidate.get("url"):
        urls.append(candidate["url"])
    source = next((url for url in urls if reachable(url)), "")
    if not source:
        raise SystemExit(f"{candidate_id} 没有可读取的视频地址；未调用模型")
    return candidate, source


def build(project, batch, candidate_id, model_call=loop.gemini, reachable=tt.media_reachable):
    project, batch = Path(project), Path(batch)
    candidate, source = candidate_source(batch, candidate_id, reachable)
    text = model_call([{"type": "text", "text": PROMPT}, {"type": "video_url", "video_url": source}],
                      project=project.name, ref=f"{candidate_id}:shots", max_tokens=5000,
                      temperature=0.1, role="breakdown")
    parsed = tt.parse_json_obj(text)
    if parsed is None:
        raise ValueError("镜头拆解输出不是 JSON 对象")
    result = validate(parsed)
    result.update(candidate_id=candidate_id, aweme_id=candidate.get("aweme_id"),
                  reference_url=candidate.get("url") or source, source_video_url=source)
    write_json(project / "shots.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="完整读取对标视频并输出连续镜头表")
    parser.add_argument("--project", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    result = build(args.project, args.batch, args.id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
