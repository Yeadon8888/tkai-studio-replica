#!/usr/bin/env python3
"""镜头表 → 可逐段生成的 ``segments`` 简报。"""
import argparse
import json
import math
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402
import keyframe  # noqa: E402
import settings  # noqa: E402


NO_TEXT = "Do not render captions, letters, numbers, logos or watermarks. Keep label areas exactly as in the reference images; do not add text."


def _duration(shot):
    return round(float(shot["end"]) - float(shot["start"]), 3)


def validate_durations(shots):
    for shot in shots:
        duration = _duration(shot)
        if duration < 3 or duration > 6:
            raise ValueError(f"{shot.get('id')} 时长 {duration:g} 秒，不在 3–6 秒范围")


def expand_beats(shots):
    """连续长镜头按动作节拍拆成 3–6 秒生成段；源 ``shots.json`` 保持不变。"""
    out = []
    for shot in shots:
        duration = _duration(shot)
        if duration <= 6:
            out.append(dict(shot))
            continue
        count = max(2, math.ceil(duration / 6))
        while duration / count > 6:
            count += 1
        while duration / count < 3 and count > 2:
            count -= 1
        source_step = duration / count
        target_total = int(round(duration))
        base, remainder = divmod(target_total, count)
        generated_durations = [base + (1 if index < remainder else 0) for index in range(count)]
        cursor = float(shot["start"])
        phases = (
            "Establish the overhead view: hold the exact product bottle and a clean test strip above the cup.",
            "Dip one test strip into the water with a natural hand movement.",
            "Lift the strip out and hold it steady as the test pads change colour.",
            "Place the strip beside the bottle colour chart for comparison and hold the result.",
        )
        for index in range(count):
            row = dict(shot)
            row["id"] = f"{shot['id']}-B{index + 1}"
            row["source_start"] = round(float(shot["start"]) + source_step * index, 3)
            row["source_end"] = round(float(shot["start"]) + source_step * (index + 1), 3)
            row["start"] = round(cursor, 3)
            cursor += generated_durations[index]
            row["end"] = round(cursor, 3)
            row["source_shot_id"] = shot["id"]
            row["keyframe_preferred"] = index in (0, count - 1)
            row["action"] = phases[index] if count == 4 else f"Phase {index + 1} of {count}: {shot.get('action', '')}"
            out.append(row)
    validate_durations(out)
    return out


def rewrite_lines(shots, selling_points):
    """按镜头节奏分配我方事实台词；不沿用对标原句。"""
    points = [str(x).strip() for x in selling_points if str(x).strip()]
    if not points:
        raise ValueError("至少提供一条我方卖点")
    if len(shots) == 4 and len(points) >= 2:
        return [f"Let's test this water across {points[0]}.",
                "Dip one strip into the water.",
                "Lift it out and let the test pads develop.",
                f"Compare the colors on the bottle chart. This pack includes {points[1]}."]
    templates = ("Let's see what is really in this water.", "This test covers {point}.",
                 "Dip the strip, then let the test pads develop.", "Compare the result for {point}.")
    lines = []
    for index, shot in enumerate(shots):
        point = points[index % len(points)]
        lines.append(templates[index % len(templates)].format(point=point))
    return lines


def choose_route(shot, keyframe_path=None):
    if shot.get("product_role") == "subject" and shot.get("keyframe_preferred", True) and keyframe_path:
        return "keyframe"
    return "quality"


def make_plan(shots_data, product_refs, selling_points, brief_id="hq-c023", keyframes=None):
    shots = expand_beats(shots_data.get("shots") or [])
    refs = [str(Path(path).expanduser().resolve()) for path in product_refs]
    if len(refs) < 2:
        raise ValueError("生成计划至少需要 2 张真实产品图")
    if any(not Path(path).is_file() for path in refs):
        raise FileNotFoundError("真实产品图有缺失")
    lines = rewrite_lines(shots, selling_points)
    frames = keyframes or {}
    segments = []
    for index, (shot, line) in enumerate(zip(shots, lines), 1):
        frame = frames.get(shot["id"]) or frames.get(shot.get("source_shot_id"))
        route = choose_route(shot, frame)
        prompt = (
            "Vertical 9:16 realistic handheld UGC, first-person overhead view. "
            f"Shot scale: {shot.get('scale')}. Camera: {shot.get('camera')}. "
            f"Action: {shot.get('action')}. Match the reference pacing: {shot.get('rhythm')}. "
            "Use the supplied product photos only for product appearance. " + NO_TEXT
        )
        images = refs[:3] if shot.get("hand_action") else refs[:2]
        segment = {"shot_id": shot["id"], "prompt": prompt, "duration": _duration(shot),
                   "video_route": route, "resolution": "768p" if route == "keyframe" else "960p", "aspect_ratio": "9:16",
                   "images": images, "voiceover_line": line, "caption": line,
                   "onscreen_copy": selling_points[index - 1] if index <= len(selling_points) else ""}
        if route == "keyframe":
            segment.update(first_frame=str(frame), last_frame=str(frame))
        segments.append(segment)
    return {
        "id": brief_id, "title": f"高质量复刻 {shots_data.get('candidate_id') or ''}".strip(),
        "summary": shots_data.get("summary", ""), "duration": round(sum(s["duration"] for s in segments), 3),
        "aspect_ratio": "9:16", "resolution": "768p", "segments": segments,
        "voiceover_lines": lines, "must_keep": [shot.get("action", "") for shot in shots],
        "replication_reference": shots_data.get("source_video_url") or shots_data.get("reference_url"),
        "shot_list": "shots.json", "product_facts": list(selling_points), "takes": [brief_id],
    }


def build(project, product_refs, selling_points, brief_id="hq-c023", prepare_keyframes=False):
    project = Path(project)
    shots = read_json(project / "shots.json")
    frames = {}
    if prepare_keyframes:
        for shot in expand_beats(shots.get("shots") or []):
            if shot.get("product_role") != "subject" or not shot.get("keyframe_preferred", True):
                continue
            frames[shot["id"]] = str(keyframe.generate_frame(
                project, f"{brief_id}-{shot['id']}", product_refs[0], shot.get("action") or shot.get("camera") or "product close-up",
                settings.get("VIDEO_ASPECT")))
    plan = make_plan(shots, product_refs, selling_points, brief_id=brief_id, keyframes=frames)
    destination = project / "briefs" / f"{brief_id}.json"
    write_json(destination, plan)
    return destination, plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="把 shots.json 转成逐镜头生成简报")
    parser.add_argument("--project", required=True)
    parser.add_argument("--refs", required=True, help="真实产品图，逗号分隔")
    parser.add_argument("--selling-points", required=True, help="只允许使用的卖点，竖线分隔")
    parser.add_argument("--id", default="hq-c023")
    parser.add_argument("--prepare-keyframes", action="store_true", help="为产品主体镜头各调用一次 GPT Image")
    args = parser.parse_args(argv)
    path, plan = build(args.project, [x for x in args.refs.split(",") if x.strip()],
                       [x for x in args.selling_points.split("|") if x.strip()], args.id, args.prepare_keyframes)
    print(f"生成计划 → {path}")
    print(json.dumps({"segments": len(plan["segments"]), "duration": plan["duration"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
