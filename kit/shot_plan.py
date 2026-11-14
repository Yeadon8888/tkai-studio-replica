#!/usr/bin/env python3
"""把镜头表与已校验脚本转成按连续性分组的视频生成计划。"""
import argparse
import json
import math
import secrets
import re
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402
import settings  # noqa: E402
import shot_list  # noqa: E402

NO_TEXT = "Do not render captions, letters, numbers, logos or watermarks."


def _duration(shot):
    return round(float(shot["end"]) - float(shot["start"]), 3)


def validate_durations(segments):
    """TokensFactory H3 单次请求只能是 1–15 秒整数。"""
    for row in segments:
        value = row.get("duration") if "duration" in row else _duration(row)
        if isinstance(value, bool) or float(value) != int(float(value)) or not 1 <= int(float(value)) <= 15:
            raise ValueError(f"{row.get('id') or row.get('shot_id')} 请求时长 {value} 秒，不在 1–15 秒整数范围")


def continuity_groups(shots):
    groups = []
    for shot in shots:
        if not groups or shot.get("join") != "continuous":
            groups.append([shot])
        else:
            groups[-1].append(shot)
    return groups


def _atoms(group):
    atoms = []
    for shot in group:
        duration = _duration(shot)
        count = max(1, math.ceil(duration / 15))
        each = duration / count
        for index in range(count):
            atoms.append({"shot": shot, "duration": each, "part": index + 1, "parts": count})
    return atoms


def split_group(group):
    """优先在镜头边界切分；单镜头超过 15 秒时才在镜头内均分。"""
    atoms = _atoms(group)
    chunks, current, total = [], [], 0.0
    remaining_total = sum(x["duration"] for x in atoms)
    while atoms:
        atom = atoms.pop(0)
        remaining_groups = max(1, math.ceil(remaining_total / 15))
        target = remaining_total / remaining_groups
        if current and (total + atom["duration"] > 15 or (total >= target and len(atoms) + 1 >= remaining_groups)):
            chunks.append(current)
            remaining_total -= total
            current, total = [], 0.0
        current.append(atom)
        total += atom["duration"]
    if current:
        chunks.append(current)
    return chunks


def _script_rows(script_data):
    return {str(row.get("id")): row for row in script_data.get("shots") or []}


def _estimate(route, resolution, duration):
    price = settings.video_unit_price(settings.video_route_model(route), resolution)
    return round(price * duration, 4) if price is not None else None


def _portion(text, part, parts):
    """单镜头跨请求时按词均分文案，避免每块重复整段口播。"""
    words = str(text or "").split()
    if parts <= 1 or not words:
        return " ".join(words)
    start = round(len(words) * (part - 1) / parts)
    end = round(len(words) * part / parts)
    return " ".join(words[start:end])


def audio_route(audio_mode, override=None):
    override = (override or settings.get("AUDIO_ROUTE_DEFAULT")).strip().lower()
    if override not in ("auto", "native", "tts"):
        raise ValueError("AUDIO_ROUTE_DEFAULT 只能是 auto、native 或 tts")
    if override != "auto":
        return override
    return "tts" if audio_mode in ("voiceover", "special_voice") else "native"


def _mean_volume(path):
    result = subprocess.run(["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
                             "-af", "volumedetect", "-f", "null", "-"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", result.stderr or "")
    return float(match.group(1)) if match else None


def first_last_audio_evidence():
    """只用已有 first-last 任务作证；没有证据或静音都禁止 native。"""
    model = settings.video_route_model("keyframe")
    roots = settings.studio_home() / "products"
    checked = []
    for job_path in roots.glob("*/runs/*/jobs/*.json") if roots.exists() else ():
        job = read_json(job_path, {}) or {}
        if job.get("model") != model:
            continue
        video = Path(job.get("video") or job_path.parents[1] / "takes" / f"{job_path.stem}.mp4")
        if not video.is_file():
            continue
        loudness = _mean_volume(video)
        checked.append({"job": str(job_path), "video": str(video), "mean_volume_db": loudness})
        if loudness is not None and loudness > -50:
            return {"usable": True, "reason": f"已有 {model} 成片平均响度 {loudness:g} dB", "checked": checked}
    reason = f"没有可证明原声可用的 {model} 历史成片" if not checked else f"已有 {model} 成片无音轨或平均响度不高于 -50 dB"
    return {"usable": False, "reason": reason, "checked": checked}


def make_plan(shots_data, script_data, product_refs, brief_id="hq-replica", scene_path=None, seed=None,
              first_last_audio=None):
    shots_data = shot_list.validate(shots_data)
    shots = shots_data.get("shots") or []
    rows = _script_rows(script_data)
    missing = [shot.get("id") for shot in shots if shot.get("id") not in rows]
    if missing:
        raise ValueError("script.json 缺少镜头：" + ", ".join(missing))
    refs = [str(Path(path).expanduser().resolve()) for path in product_refs]
    if not refs:
        raise ValueError("生成计划至少需要 1 张真实产品图")
    if any(not Path(path).is_file() for path in refs):
        raise FileNotFoundError("真实产品图有缺失")
    scene = Path(scene_path).expanduser().resolve() if scene_path else None
    if not scene or not scene.is_file():
        raise FileNotFoundError("缺少共用定场图 scene.png；先运行 keyframe.py scene")
    seed = int(seed if seed is not None else secrets.randbelow(2_147_483_646) + 1)
    if seed < 1:
        raise ValueError("连续性计划 seed 必须大于等于 1")
    groups = continuity_groups(shots)
    selected_audio = audio_route(shots_data["audio_mode"])
    first_last_audio = first_last_audio or {"usable": False, "reason": "未提供 first-last 原声音轨证据"}
    segments = []
    for group_index, group in enumerate(groups, 1):
        chunks = split_group(group)
        starts_match_cut = group[0].get("join") == "match_cut"
        for chunk_index, chunk in enumerate(chunks, 1):
            script_rows = [rows[atom["shot"]["id"]] for atom in chunk]
            chained = chunk_index > 1 or starts_match_cut
            route = "keyframe" if chained else "quality"
            resolution = "768p" if chained else "960p"
            source_duration = sum(atom["duration"] for atom in chunk)
            request_duration = max(1, min(15, int(round(source_duration))))
            timed, cursor = [], 0.0
            for atom, row in zip(chunk, script_rows):
                part = f" Part {atom['part']} of {atom['parts']}." if atom["parts"] > 1 else ""
                timed.append(f"{cursor:.1f}-{cursor + atom['duration']:.1f}s:{part} {row['visual_prompt']}")
                cursor += atom["duration"]
            prompt = (f"Shared scene: {script_data['scene']}. " + " ".join(timed) + " " + NO_TEXT).strip()
            shot_ids = list(dict.fromkeys(atom["shot"]["id"] for atom in chunk))
            voice = " ".join(_portion(row.get("voiceover"), atom["part"], atom["parts"])
                             for atom, row in zip(chunk, script_rows)).strip()
            caption = " ".join(_portion(row.get("caption"), atom["part"], atom["parts"])
                               for atom, row in zip(chunk, script_rows)).strip()
            copy = " ".join(str(row.get("onscreen_copy") or "").strip()
                            for atom, row in zip(chunk, script_rows) if atom["part"] == 1).strip()
            last_atom, last_row = chunk[-1], script_rows[-1]
            if last_atom["parts"] > 1 and last_atom["part"] < last_atom["parts"]:
                end_state = (f"Natural intermediate state after part {last_atom['part']} of "
                             f"{last_atom['parts']}: {last_row['visual_prompt']}")
            else:
                end_state = str(last_row.get("end_state") or last_atom["shot"].get("end_state") or "").strip()
            segment = {
                "shot_id": shot_ids[0], "shot_ids": shot_ids, "prompt": prompt,
                "duration": request_duration, "source_duration": round(source_duration, 3),
                "video_route": route, "resolution": resolution, "aspect_ratio": "9:16",
                "seed": seed, "voiceover_line": voice, "caption": caption,
                "onscreen_copy": copy, "end_state": end_state,
                "chain_group": f"G{group_index:02d}", "chain_index": chunk_index,
                "chain_total": len(chunks),
            }
            segment["audio_route"] = selected_audio
            if route == "keyframe" and selected_audio == "native" and not first_last_audio.get("usable"):
                segment["audio_route"] = "tts"
                segment["audio_route_reason"] = first_last_audio.get("reason") or "first-last 原声不可用"
            if route == "quality":
                segment["images"] = [str(scene)] + refs[:8]
            else:
                segment.update(first_frame="chain:prev", last_frame="chain:end")
            segment["estimated_cny"] = _estimate(route, resolution, request_duration)
            segments.append(segment)
    validate_durations(segments)
    estimates = [row["estimated_cny"] for row in segments]
    return {
        "id": brief_id, "title": f"高质量复刻 {shots_data.get('candidate_id') or ''}".strip(),
        "summary": shots_data.get("summary", ""), "duration": sum(x["duration"] for x in segments),
        "audio_mode": shots_data["audio_mode"],
        "source_duration": round(sum(_duration(x) for x in shots), 3),
        "aspect_ratio": "9:16", "resolution": "960p", "segments": segments,
        "voiceover_lines": [x["voiceover_line"] for x in segments],
        "must_keep": [shot.get("action", "") for shot in shots if shot.get("keep") == "keep"],
        "replication_reference": shots_data.get("source_video_url") or shots_data.get("reference_url"),
        "shot_list": "shots.json", "script": "script.json", "scene": str(scene),
        "seed": seed, "estimated_cny": round(sum(estimates), 4) if all(x is not None for x in estimates) else None,
        "takes": [brief_id],
    }


def build(project, product_refs, brief_id="hq-replica", scene_path=None, seed=None):
    project = Path(project)
    shots = read_json(project / "shots.json")
    if not shots:
        raise FileNotFoundError(f"缺少 shots.json：{project / 'shots.json'}")
    script_path = project / "script.json"
    script = read_json(script_path)
    if not script:
        raise FileNotFoundError(f"缺少 script.json；请先运行 script_writer.py：{script_path}")
    plan = make_plan(shots, script, product_refs, brief_id=brief_id,
                     scene_path=scene_path or project / "scene.png", seed=seed,
                     first_last_audio=first_last_audio_evidence())
    destination = project / "briefs" / f"{brief_id}.json"
    write_json(destination, plan)
    return destination, plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="按连续性把 shots.json 与 script.json 转成生成计划")
    parser.add_argument("--project", required=True)
    parser.add_argument("--refs", required=True, help="真实产品图，逗号分隔")
    parser.add_argument("--id", default="hq-replica")
    parser.add_argument("--scene", help="共用定场图；默认 <运行目录>/scene.png")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    path, plan = build(args.project, [x.strip() for x in args.refs.split(",") if x.strip()],
                       args.id, args.scene, args.seed)
    print(f"生成计划 → {path}")
    print(json.dumps({"segments": len(plan["segments"]), "duration": plan["duration"],
                      "seed": plan["seed"], "estimated_cny": plan["estimated_cny"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
