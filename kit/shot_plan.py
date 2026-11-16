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
NO_VOICE = "No dialogue, no speech, no singing, no voice-over; only quiet natural ambient sound."
PRODUCT_LABEL_LOCK = "Keep label text exactly as in the reference photo; do not invent, transcribe or describe small label text."
SPEECH_RATE_WPS = 2.6
SPEECH_TAIL_SECONDS = 0.8


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


def allocate_request_durations(source_durations, target_duration):
    """H3 只收整秒；用最大余数法保持整条目标时长，避免逐段舍入累积偏差。"""
    values = [float(value) for value in source_durations]
    target = int(round(float(target_duration)))
    if not values or target < len(values) or target > 15 * len(values):
        raise ValueError(f"目标时长 {target_duration} 秒无法分配到 {len(values)} 个 1–15 秒单元")
    result = [max(1, min(15, math.floor(value))) for value in values]
    delta = target - sum(result)
    if delta > 0:
        order = sorted(range(len(values)), key=lambda i: (values[i] - math.floor(values[i]), -i), reverse=True)
        while delta:
            changed = False
            for index in order:
                if result[index] < 15 and delta:
                    result[index] += 1
                    delta -= 1
                    changed = True
            if not changed:
                raise ValueError("目标时长超过 H3 单元上限")
    elif delta < 0:
        order = sorted(range(len(values)), key=lambda i: (values[i] - math.floor(values[i]), i))
        while delta:
            changed = False
            for index in order:
                if result[index] > 1 and delta:
                    result[index] -= 1
                    delta += 1
                    changed = True
            if not changed:
                raise ValueError("目标时长低于 H3 单元下限")
    return result


def _script_rows(script_data):
    return {str(row.get("id")): row for row in script_data.get("shots") or []}


def prepare_product_detail_refs(product_refs, output_dir):
    """从包含盒子和瓶身的真实产品合照生成两个本地特写，不产生远程费用。"""
    from PIL import Image
    candidates = []
    for value in product_refs:
        path = Path(value).expanduser().resolve()
        try:
            with Image.open(path) as image:
                candidates.append((image.width * image.height, path))
        except (OSError, ValueError):
            continue
    if not candidates:
        raise ValueError("真实产品图无法读取，不能生成产品细节特写")
    source = max(candidates)[1]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    crops = (("box-front.jpg", (0.00, 0.02, 0.53, 0.98)),
             ("bottle-label.jpg", (0.49, 0.20, 0.84, 0.96)))
    outputs = []
    with Image.open(source) as image:
        image = image.convert("RGB")
        for name, box in crops:
            out = output_dir / name
            if not out.exists():
                width, height = image.size
                pixels = (round(box[0] * width), round(box[1] * height),
                          round(box[2] * width), round(box[3] * height))
                image.crop(pixels).save(out, quality=95)
            outputs.append(str(out.resolve()))
    return outputs


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
    # 这两种是镜头表的硬路线，不允许全局旧配置把它们改回人声路线。
    if audio_mode == "music_only":
        return "music"
    if audio_mode == "ambient":
        return "ambient"
    override = (override or settings.get("AUDIO_ROUTE_DEFAULT")).strip().lower()
    if override not in ("auto", "native", "tts"):
        raise ValueError("AUDIO_ROUTE_DEFAULT 只能是 auto、native 或 tts")
    if override != "auto":
        return override
    if audio_mode in ("voiceover", "special_voice"):
        return "tts"
    return "native"


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
              first_last_audio=None, character_ref=None, product_detail_refs=()):
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
    if not scene:
        raise FileNotFoundError("缺少共用定场图 scene.png 路径")
    seed = int(seed if seed is not None else secrets.randbelow(2_147_483_646) + 1)
    if seed < 1:
        raise ValueError("连续性计划 seed 必须大于等于 1")
    groups = continuity_groups(shots)
    selected_audio = ("native" if shot_list.all_speech_is_on_camera(shots_data)
                      else audio_route(shots_data["audio_mode"]))
    first_last_audio = first_last_audio or {"usable": False, "reason": "未提供 first-last 原声音轨证据"}
    character_map = {str(row.get("name")): str(row.get("appearance"))
                     for row in script_data.get("characters") or [] if row.get("name") and row.get("appearance")}
    character_image = str(Path(character_ref).expanduser().resolve()) if character_ref else None
    detail_refs = [str(Path(path).expanduser().resolve()) for path in product_detail_refs]
    segments = []
    for group_index, group in enumerate(groups, 1):
        chunks = split_group(group)
        starts_match_cut = group[0].get("join") == "match_cut"
        for chunk_index, chunk in enumerate(chunks, 1):
            script_rows = [rows[atom["shot"]["id"]] for atom in chunk]
            # 出镜口播必须由 H3 每个单元原生生成。first-last 没有可靠原声证据时，
            # 把超过 15 秒或 match-cut 的后续块当作独立 quality 单元硬切，不回退 TTS。
            native_hard_cut = selected_audio == "native"
            chained = (chunk_index > 1 or starts_match_cut) and not native_hard_cut
            route = "keyframe" if chained else "quality"
            resolution = "768p" if chained else "960p"
            source_duration = sum(atom["duration"] for atom in chunk)
            request_duration = max(1, min(15, int(round(source_duration))))
            timed, cursor = [], 0.0
            for atom, row in zip(chunk, script_rows):
                part = f" Part {atom['part']} of {atom['parts']}." if atom["parts"] > 1 else ""
                visual = str(row["visual_prompt"]).strip()
                names = row.get("characters") or []
                descriptions = [character_map[name] for name in names if name in character_map]
                if descriptions:
                    visual += " Character continuity: " + " ".join(descriptions)
                visual += " " + PRODUCT_LABEL_LOCK
                if shots_data["audio_mode"] in ("music_only", "ambient") and not visual.endswith(NO_VOICE):
                    visual = f"{visual.rstrip('.')}. {NO_VOICE}"
                timed.append(f"{cursor:.1f}-{cursor + atom['duration']:.1f}s:{part} {visual}")
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
            if native_hard_cut and (chunk_index > 1 or starts_match_cut):
                segment["join_strategy"] = "hard_cut_for_native_audio"
            if route == "keyframe" and selected_audio == "native" and not first_last_audio.get("usable"):
                segment["audio_route"] = "tts"
                segment["audio_route_reason"] = first_last_audio.get("reason") or "first-last 原声不可用"
            if route == "quality":
                ordered = [str(scene)]
                if character_image:
                    ordered.append(character_image)
                ordered.extend(refs)
                ordered.extend(detail_refs)
                segment["images"] = list(dict.fromkeys(ordered))[:9]
            else:
                segment.update(first_frame="chain:prev", last_frame="chain:end")
            segments.append(segment)
    target_duration = shots_data.get("target_duration", shots_data.get("duration"))
    allocated = allocate_request_durations([row["source_duration"] for row in segments], target_duration)
    for segment, request_duration in zip(segments, allocated):
        segment["duration"] = request_duration
        words = re.findall(r"\b[\w'-]+\b", segment.get("voiceover_line") or "")
        required = len(words) / SPEECH_RATE_WPS + (SPEECH_TAIL_SECONDS if words else 0.0)
        if required > request_duration + 1e-9:
            raise ValueError(f"{segment['shot_id']} 口播至少需要 {required:.2f} 秒（含 {SPEECH_TAIL_SECONDS:g} 秒收尾），"
                             f"单元只有 {request_duration:g} 秒；请让脚本模型缩短台词")
        segment["speech_required_seconds"] = round(required, 3)
        segment["speech_tail_seconds"] = SPEECH_TAIL_SECONDS if words else 0.0
        segment["estimated_cny"] = _estimate(segment["video_route"], segment["resolution"], request_duration)
    validate_durations(segments)
    estimates = [row["estimated_cny"] for row in segments]
    return {
        "id": brief_id, "title": f"高质量复刻 {shots_data.get('candidate_id') or ''}".strip(),
        "summary": shots_data.get("summary", ""), "duration": sum(x["duration"] for x in segments),
        "audio_mode": shots_data["audio_mode"],
        "beats": [str(row.get("beat") or "") for row in shots_data.get("structure") or []],
        "source_duration": round(sum(_duration(x) for x in shots), 3),
        "aspect_ratio": "9:16", "resolution": "960p", "segments": segments,
        "voiceover_lines": [x["voiceover_line"] for x in segments],
        "must_keep": list(dict.fromkeys([*(shots_data.get("must_keep") or []),
                         *(shot.get("action", "") for shot in shots if shot.get("keep") == "keep")])),
        "characters": script_data.get("characters") or [],
        "character_reference": character_image,
        "product_detail_references": detail_refs,
        "replication_reference": shots_data.get("source_video_url") or shots_data.get("reference_url"),
        "replication_reference_file": shots_data.get("source_video_file") or "benchmark/video.mp4",
        "shot_list": "shots.json", "script": "script.json", "scene": str(scene),
        "music": script_data.get("music"),
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
    detail_refs = prepare_product_detail_refs(product_refs, project / "product-details")
    character_ref = project / "characters.png" if script.get("characters") else None
    plan = make_plan(shots, script, product_refs, brief_id=brief_id,
                     scene_path=scene_path or project / "scene.png", seed=seed,
                     first_last_audio=first_last_audio_evidence(), character_ref=character_ref,
                     product_detail_refs=detail_refs)
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
