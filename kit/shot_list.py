#!/usr/bin/env python3
"""整条对标视频 → 连续镜头表 ``shots.json``。"""
import argparse
import json
import shutil
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402
import loop  # noqa: E402
import settings  # noqa: E402
import tt_research as tt  # noqa: E402


PROMPT = """你是短视频导演。完整观看并听完给出的对标视频，先判断整条叙事结构，再按真实切镜输出镜头表，最后判断哪些内容保留、哪些重做。
不要为了控制时长拆镜；只有画面实际切镜才新建镜头。起止时间必须首尾相接、覆盖整条视频，不得有空隙或重叠。
只依据画面和声音，看不清或听不清就写空字符串，不要编造。
只输出一个 JSON 对象：
{"duration": 18.2, "summary": "为什么整条视频抓人", "structure": [
 {"beat":"钩子|证明|行动或其他叙事节拍","start":0.0,"end":4.2}], "shots": [
 {"id":"S01","start":0.0,"end":4.2,"scale":"景别","camera":"机位与运动",
  "action":"动作","product_role":"subject|supporting|none","hand_action":true,
  "onscreen_text":"原画面文字","voiceover":"口播原文","music_sfx":"音乐与音效",
  "rhythm":"节奏点","hook_reason":"为什么抓人","join":"cut|continuous|match_cut",
  "keep":"keep|redo","redo_reason":"redo 时说明不适配原因，否则空字符串",
  "end_state":"结束瞬间手、产品、道具的位置和状态"}]}
第一个镜头的 join 必须是 cut。continuous 只用于同一连续镜头被时间边界分段、画面本身没有切镜；match_cut 是动作匹配切。
"""

REQUIRED = ("id", "start", "end", "scale", "camera", "action", "onscreen_text",
            "voiceover", "music_sfx", "rhythm", "hook_reason", "join", "keep",
            "redo_reason", "end_state")
JOINS = ("cut", "continuous", "match_cut")
KEEP = ("keep", "redo")


def research_batch(project, shots=None, explicit=None):
    """从命令参数、镜头表、运行元数据或 BOARD_RESEARCH 解析调研批次。"""
    project = Path(project)
    shots = shots or {}
    meta = read_json(project / "project.json", {}) or {}
    values = [explicit, shots.get("research_batch"), meta.get("research_batch"),
              meta.get("research_dir"), settings.get("BOARD_RESEARCH")]
    for value in (item for item in values if item):
        raw = Path(value).expanduser()
        if raw.is_absolute():
            choices = [raw]
        else:
            choices = [settings.workspace_root() / raw, settings.studio_home() / raw,
                       Path(settings.get("RESEARCH_ROOT")) / raw]
            research_root = Path(settings.get("RESEARCH_ROOT"))
            if not research_root.is_absolute():
                research_root = settings.workspace_root() / research_root
            choices.append(research_root / raw)
        batch = next((path.resolve() for path in choices if path.is_dir()), None)
        if batch:
            return batch
    return None


def _candidate(batch, candidate_id):
    rows = read_json(batch / "candidates.json", []) or []
    return next((row for row in rows
                 if str(row.get("candidate_id") or row.get("id") or "") == str(candidate_id or "")), {})


def copy_benchmark_media(project, shots=None, batch=None):
    """把调研封面和已有视频复制到运行目录；缺失时返回原因而不中断流程。"""
    project, shots = Path(project), shots or {}
    batch = research_batch(project, shots, batch)
    result = {"batch": str(batch) if batch else ""}
    if not batch:
        reason = "无法从 shots.json、project.json 或 BOARD_RESEARCH 确定调研批次"
        result.update(cover={"status": "missing", "reason": reason},
                      video={"status": "missing", "reason": reason})
        return result

    candidate_id = shots.get("candidate_id")
    candidate = _candidate(batch, candidate_id)
    aweme_id = str(shots.get("aweme_id") or candidate.get("aweme_id") or "")
    names = [name for name in (aweme_id, str(candidate_id or "")) if name]
    cover_sources = [batch / "covers" / f"{name}{suffix}"
                     for name in names for suffix in (".jpg", ".jpeg")]
    local_file = candidate.get("local_file")
    video_sources = []
    if local_file:
        local_path = Path(local_file).expanduser()
        video_sources.append(local_path if local_path.is_absolute() else batch / local_path)
    video_sources.extend(root / f"{name}.mp4"
                         for root in (batch / "videos", batch / "media") for name in names)

    output = project / "benchmark"
    for kind, sources, filename in (("cover", cover_sources, "cover.jpg"),
                                    ("video", video_sources, "video.mp4")):
        target = output / filename
        source = next((path for path in sources if path.is_file()), None)
        if not source:
            if target.is_file():
                result[kind] = {"status": "existing", "path": str(target.relative_to(project))}
            else:
                result[kind] = {"status": "missing", "reason": f"调研批次没有可用的对标{'封面' if kind == 'cover' else '视频'}"}
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            result[kind] = {"status": "copied", "source": str(source),
                            "path": str(target.relative_to(project))}
        except OSError as exc:
            result[kind] = {"status": "missing", "reason": f"复制失败：{exc}"}
    return result


def _validate_timeline(rows, duration, label, name_key):
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label} 必须是非空列表")
    cursor = 0.0
    normalized = []
    for index, raw in enumerate(rows, 1):
        if not isinstance(raw, dict) or name_key not in raw:
            raise ValueError(f"第 {index} 个{label}缺少 {name_key}")
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 个{label}时间不是数字") from exc
        if end <= start or abs(start - cursor) > 0.08:
            raise ValueError(f"第 {index} 个{label}时间轴不连续")
        normalized.append(dict(raw, start=round(cursor, 3), end=round(end, 3)))
        cursor = end
    if abs(cursor - duration) > 0.08:
        raise ValueError(f"{label}结尾 {cursor:g} 与视频时长 {duration:g} 不一致")
    normalized[-1]["end"] = round(duration, 3)
    return normalized


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
    structure = _validate_timeline(data.get("structure"), duration, "structure", "beat")
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
        if row["join"] not in JOINS:
            raise ValueError(f"第 {index} 个镜头 join 非法：{row['join']}")
        if index == 1 and row["join"] != "cut":
            raise ValueError("第一个镜头 join 必须是 cut")
        if row["keep"] not in KEEP:
            raise ValueError(f"第 {index} 个镜头 keep 非法：{row['keep']}")
        if row["keep"] == "redo" and not str(row["redo_reason"]).strip():
            raise ValueError(f"第 {index} 个镜头 redo 时必须填写 redo_reason")
        if not str(row["end_state"]).strip():
            raise ValueError(f"第 {index} 个镜头 end_state 不能为空")
        row["product_role"] = row.get("product_role") if row.get("product_role") in ("subject", "supporting", "none") else "none"
        row["hand_action"] = bool(row.get("hand_action"))
        shots.append(row)
        cursor = end
    if abs(cursor - duration) > tolerance:
        raise ValueError(f"镜头表结尾 {cursor:g} 与视频时长 {duration:g} 不一致")
    shots[-1]["end"] = round(duration, 3)
    return {**data, "duration": round(duration, 3), "structure": structure, "shots": shots}


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
    result.update(research_batch=str(batch.resolve()), reference_author=candidate.get("author") or "",
                  reference_title=candidate.get("title") or candidate.get("desc") or "")
    result["benchmark_media"] = copy_benchmark_media(project, result, batch)
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
