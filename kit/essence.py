#!/usr/bin/env python3
"""从对标镜头表生成神韵清单，并合并机器/专家逐项判定。"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402


EXPERT_CATEGORIES = ("hook", "camera", "action", "sound", "caption", "emotion")
SCRIPT_CATEGORIES = {"editing_rhythm", "duration", "narrative_beats", "hook", "sound", "caption", "emotion"}
GENERATE_CATEGORIES = {"camera", "action", "visual"}


def _tc(value):
    value = float(value or 0)
    return f"{int(value // 60):02d}:{value % 60:04.1f}"


def resolve_tier(shots, plan=None):
    """档位只读人工 override 或计划；缺失即停止，不从内容猜。"""
    batch, cid = shots.get("research_batch"), shots.get("candidate_id")
    if batch and cid:
        row = (read_json(Path(batch) / "tier_overrides.json", {}) or {}).get(cid)
        tier = row.get("tier") if isinstance(row, dict) else None
        if tier in ("H", "M", "L", "X"):
            return tier, "tier_overrides.json"
    for source, label in ((plan or {}, "plan"), (shots, "shots.json")):
        tier = source.get("tier") or source.get("benchmark_tier")
        if tier in ("H", "M", "L", "X"):
            return tier, label
    raise ValueError("读不到人工确认档位（tier_overrides.json 或计划），已停止，不猜档位")


def build_items(shots, plan=None):
    tier, tier_source = resolve_tier(shots, plan)
    duration = float(shots["duration"])
    items = [
        {"id": "code-editing-rhythm", "category": "editing_rhythm",
         "what": f"剪辑切点按总时长归一化后与对标镜头边界一致，容差 ±8%（对标 00:00.0–{_tc(duration)}）", "judge": "code"},
        {"id": "code-duration", "category": "duration",
         "what": f"总时长与对标 {duration:g} 秒相差不超过 ±15%", "judge": "code"},
        {"id": "code-narrative-beats", "category": "narrative_beats",
         "what": "叙事节拍顺序与对标一致：" + " → ".join(str(x.get("beat") or "") for x in shots.get("structure") or []),
         "judge": "code"},
        {"id": "expert-hook", "category": "hook",
         "what": "前三秒钩子机制与对标一致（00:00.0–00:03.0）", "judge": "expert"},
        {"id": "expert-camera-sequence", "category": "camera",
         "what": "景别/机位顺序与对标一致：" + "；".join(
             f"{_tc(x['start'])}–{_tc(x['end'])} {x.get('scale','')} / {x.get('camera','')}"
             for x in shots.get("shots") or [] if x.get("keep") == "keep"), "judge": "expert"},
    ]
    for shot in shots.get("shots") or []:
        if shot.get("keep") != "keep":
            continue
        items.append({"id": f"expert-action-{shot['id']}", "category": "action",
                      "what": f"{_tc(shot['start'])}–{_tc(shot['end'])} 关键动作：{shot.get('action','')}",
                      "judge": "expert"})
    items.extend([
        {"id": "expert-sound", "category": "sound",
         "what": f"声音类型与语气对应对标（{shots.get('audio_mode', '未标注')}）：" + "；".join(
             f"{_tc(x['start'])}–{_tc(x['end'])} {x.get('voiceover') or x.get('music_sfx') or '环境声'}"
             for x in shots.get("shots") or [] if x.get("keep") == "keep"), "judge": "expert"},
        {"id": "expert-caption-rhythm", "category": "caption",
         "what": "字幕出现、换句与停留节奏对应对标", "judge": "expert"},
        {"id": "expert-emotion", "category": "emotion",
         "what": "情绪起伏与叙事推进对应对标", "judge": "expert"},
    ])
    return {"version": 1, "candidate_id": shots.get("candidate_id"), "tier": tier,
            "tier_source": tier_source, "reference_duration": duration, "items": items}


def build(project, plan=None):
    project = Path(project)
    shots = read_json(project / "shots.json")
    if not shots:
        raise FileNotFoundError(f"缺少 shots.json：{project / 'shots.json'}")
    data = build_items(shots, plan)
    write_json(project / "essence.json", data)
    return data


def detect_cuts(video, runner=subprocess.run):
    command = ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(video), "-vf",
               "select='gt(scene,0.35)',metadata=print", "-an", "-f", "null", "-"]
    result = runner(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", result.stderr or "")]


def video_duration(video):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "default=nw=1:nk=1", str(video)], text=True).strip())


def _beats(plan):
    rows = plan.get("structure") or plan.get("beats") or []
    return [str(x.get("beat") if isinstance(x, dict) else x) for x in rows]


def code_results(essence_data, shots, video, plan=None, cuts=None, duration=None):
    duration = float(duration if duration is not None else video_duration(video))
    cuts = list(cuts if cuts is not None else detect_cuts(video))
    ref_duration = float(shots["duration"])
    source_shots = shots.get("shots") or []
    expected = [float(source_shots[index]["end"]) / ref_duration for index in range(len(source_shots) - 1)
                if source_shots[index + 1].get("join") != "continuous"]
    actual = [x / duration for x in cuts if 0 < x < duration]
    cut_pass = len(expected) == len(actual) and all(abs(a - b) <= 0.08 for a, b in zip(expected, actual))
    source_beats = [str(x.get("beat") or "") for x in shots.get("structure") or []]
    planned = _beats(plan or {})
    beat_status = "pass" if planned and planned == source_beats else ("fail" if planned else "unclear")
    by_id = {
        "code-editing-rhythm": {"status": "pass" if cut_pass else "fail", "at": "00:00",
                                "detail": f"对标归一化切点 {expected}；成片 {actual}"},
        "code-duration": {"status": "pass" if abs(duration - ref_duration) <= ref_duration * .15 else "fail",
                          "at": "00:00", "detail": f"对标 {ref_duration:.3f}s；成片 {duration:.3f}s"},
        "code-narrative-beats": {"status": beat_status, "at": "00:00",
                                 "detail": f"对标 {source_beats}；计划 {planned or '未提供'}"},
    }
    return [dict(item, **by_id[item["id"]]) for item in essence_data["items"] if item["judge"] == "code"]


def merge_results(essence_data, code_rows, expert_rows):
    supplied = {str(x.get("id")): x for x in [*code_rows, *expert_rows] if isinstance(x, dict)}
    rows = []
    for item in essence_data["items"]:
        result = supplied.get(item["id"], {})
        status = result.get("status") if result.get("status") in ("pass", "fail", "unclear") else "unclear"
        rows.append({**item, "status": status, "at": str(result.get("at") or ""),
                     "detail": str(result.get("detail") or "未返回逐项判定")})
    passed = sum(x["status"] == "pass" for x in rows)
    unclear = [x["id"] for x in rows if x["status"] == "unclear"]
    return {"tier": essence_data["tier"], "items": rows, "pass_count": passed,
            "total": len(rows), "essence_score": passed / len(rows) if rows else 0.0,
            "unclear": unclear}


def retry_from(essence_result):
    failed = {x["category"] for x in essence_result.get("items") or [] if x.get("status") != "pass"}
    if failed & SCRIPT_CATEGORIES:
        return "script"
    if failed & GENERATE_CATEGORIES:
        return "generate"
    return "script"


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成或计算神韵还原度")
    parser.add_argument("--project", required=True)
    parser.add_argument("--video")
    parser.add_argument("--plan")
    args = parser.parse_args(argv)
    plan = read_json(args.plan, {}) if args.plan else {}
    data = build(args.project, plan)
    if args.video:
        shots = read_json(Path(args.project) / "shots.json")
        result = merge_results(data, code_results(data, shots, args.video, plan), [])
        write_json(Path(args.project) / "essence-score.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
