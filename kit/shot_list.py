#!/usr/bin/env python3
"""整条对标视频 → 连续镜头表 ``shots.json``。"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402
import loop  # noqa: E402
import settings  # noqa: E402
import tt_research as tt  # noqa: E402
import audio_transcript  # noqa: E402


PROMPT = """你是短视频导演。完整观看并听完给出的对标视频，先判断整条叙事结构，再按真实切镜输出镜头表，最后判断哪些内容保留、哪些重做。
不要为了控制时长拆镜；只有画面实际切镜才新建镜头。起止时间必须首尾相接、覆盖整条视频，不得有空隙或重叠。
只依据画面和声音，看不清或听不清就写空字符串，不要编造。
只输出一个 JSON 对象：
{"duration": 18.2, "summary": "为什么整条视频抓人", "audio_mode":"on_camera_speech|voiceover|special_voice|music_only|ambient",
 "must_keep":["必须保留的复刻机制，用可执行画面语言表达；包括对标中的强透视、X 光或溃散效果"], "structure": [
 {"beat":"钩子|证明|行动或其他叙事节拍","start":0.0,"end":4.2}], "shots": [
 {"id":"S01","start":0.0,"end":4.2,"scale":"景别","camera":"机位与运动",
  "action":"动作","product_role":"subject|supporting|none","hand_action":true,"speaker_on_screen":true,
  "onscreen_text":"原画面文字","voiceover":"口播原文","music_sfx":"音乐与音效",
  "cta":{"present":true,"kind":"direct|reverse|none","evidence":"台词或画面文字原文"},
  "rhythm":"节奏点","hook_reason":"为什么抓人","join":"cut|continuous|match_cut",
  "keep":"keep|redo","redo_reason":"redo 时说明不适配原因，否则空字符串",
  "end_state":"结束瞬间手、产品、道具的位置和状态"}]}
第一个镜头的 join 必须是 cut。continuous 只用于同一连续镜头被时间边界分段、画面本身没有切镜；match_cut 是动作匹配切。
"""


def breakdown_prompt(target_duration=None, transcript=None, replication_notes=""):
    context = ""
    if transcript:
        context += ("\n\n以下是只由对标音轨转写的原始台词与时间码，它是 voiceover "
                    "和口播节拍的权威依据，不得根据画面改写：\n" +
                    json.dumps(transcript, ensure_ascii=False))
    if replication_notes:
        context += "\n\n本次用户确认的复刻要点（只作为拆解/脚本模型输入）：\n" + str(replication_notes)
    if target_duration is None:
        return PROMPT + context
    value = float(target_duration)
    if value <= 0:
        raise ValueError("目标时长必须大于 0 秒")
    return PROMPT + f"""

本次成片目标时长是 {value:g} 秒，与对标原时长不同。先完整理解对标，再压缩为精确 {value:g} 秒的镜头表：
- 保留原叙事节拍顺序、钩子、证明和行动逻辑，删除重复或次要内容。
- duration 必须精确为 {value:g}，structure 和 shots 的时间轴都从 0 连续覆盖到 {value:g}。
- 镜头仍以对标中实际切镜为依据；被删除的内容不要伪造新切镜。
 - 另输出 beat_mapping，每项含 beat、source_start、source_end、target_start、target_end，明确对标原始节拍到目标 {value:g} 秒的映射。
""" + context

REQUIRED = ("id", "start", "end", "scale", "camera", "action", "onscreen_text",
            "voiceover", "music_sfx", "rhythm", "hook_reason", "join", "keep",
            "redo_reason", "end_state")
JOINS = ("cut", "continuous", "match_cut")
KEEP = ("keep", "redo")
AUDIO_MODES = ("on_camera_speech", "voiceover", "special_voice", "music_only", "ambient")


def all_speech_is_on_camera(data):
    """有口播的镜头中，说话者是否全部可见。这比声线是否特殊更能决定原声路线。"""
    voiced = [row for row in data.get("shots") or [] if str(row.get("voiceover") or "").strip()]
    return bool(voiced) and all(bool(row.get("speaker_on_screen")) for row in voiced)


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
    audio_mode = data.get("audio_mode")
    if audio_mode is None:  # 旧运行只读兼容；新模型提示词始终要求该字段。
        audio_mode = "voiceover" if any(str(x.get("voiceover") or "").strip() for x in data["shots"]) else "ambient"
    if audio_mode not in AUDIO_MODES:
        raise ValueError(f"audio_mode 非法：{audio_mode}")
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
        if data.get("cta_schema_version") or "cta" in row:
            cta_text = " ".join(str(row.get(key) or "") for key in ("voiceover", "onscreen_text"))
            from format_invariants import validate_cta
            cta_error = validate_cta(row.get("cta"), cta_text)
            if cta_error:
                raise ValueError(f"第 {index} 个镜头 {cta_error}")
        row["product_role"] = row.get("product_role") if row.get("product_role") in ("subject", "supporting", "none") else "none"
        row["hand_action"] = bool(row.get("hand_action"))
        row["speaker_on_screen"] = bool(row.get("speaker_on_screen"))
        shots.append(row)
        cursor = end
    if abs(cursor - duration) > tolerance:
        raise ValueError(f"镜头表结尾 {cursor:g} 与视频时长 {duration:g} 不一致")
    shots[-1]["end"] = round(duration, 3)
    must_keep = data.get("must_keep") or []
    if not isinstance(must_keep, list) or not all(isinstance(value, str) and value.strip() for value in must_keep):
        raise ValueError("must_keep 必须是非空字符串列表或空列表")
    return {**data, "duration": round(duration, 3), "audio_mode": audio_mode,
            "must_keep": list(dict.fromkeys(value.strip() for value in must_keep)),
            "structure": structure, "shots": shots}


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


def build(project, batch, candidate_id, model_call=loop.gemini, reachable=tt.media_reachable,
          target_duration=None, upload=None, delete=None, media_log=None,
          transcribe_call=audio_transcript.transcribe):
    project, batch = Path(project), Path(batch)
    candidate = _candidate(batch, candidate_id)
    if not candidate:
        raise SystemExit(f"调研批次里没有 {candidate_id}")
    benchmark_media = copy_benchmark_media(project, {"candidate_id": candidate_id,
                                                     "aweme_id": candidate.get("aweme_id")}, batch)
    benchmark_video = project / "benchmark" / "video.mp4"
    transcript = (transcribe_call(benchmark_video, project, ref=candidate_id)
                  if target_duration is not None and benchmark_video.is_file() else None)
    meta = read_json(project / "project.json", {}) or {}
    replication_notes = meta.get("replication_notes") or ""
    uploaded = None
    media_status = {"aweme_id": candidate.get("aweme_id"), "candidate_id": candidate_id,
                    "source": "local" if candidate.get("local_file") else "url"}
    if candidate.get("local_file"):
        media_host = __import__("media_host")
        upload = upload or media_host.upload
        delete = delete or media_host.delete
        media_log = media_log or media_host.log
        uploaded = upload(candidate["local_file"])
        source = uploaded["url"]
        media_status["uploaded_url"] = source
    else:
        candidate, source = candidate_source(batch, candidate_id, reachable)
    try:
        text = model_call([{"type": "text", "text": breakdown_prompt(target_duration, transcript, replication_notes)},
                           {"type": "video_url", "video_url": source}],
                          project=project.name, ref=f"{candidate_id}:shots", max_tokens=5000,
                          temperature=0.1, role="breakdown")
    finally:
        if uploaded is not None:
            try:
                media_status["deleted"] = bool(delete(uploaded))
            except Exception as exc:
                media_status.update(deleted=False, delete_error=str(exc)[:200])
            if media_log:
                media_log(uploaded, media_status)
    if uploaded is not None and media_status.get("deleted") is not True:
        raise RuntimeError("本地对标临时文件未确认删除，已停止写入镜头表")
    parsed = tt.parse_json_obj(text)
    if parsed is None:
        raise ValueError("镜头拆解输出不是 JSON 对象")
    parsed["cta_schema_version"] = 1
    result = validate(parsed)
    if replication_notes:
        # 用户确认的复刻要点是拆解模型的正式输入，也必须进入下游 must_keep，
        # 防止只存在于 prompt、计划与评审却看不到。
        result["must_keep"] = list(dict.fromkeys([*(result.get("must_keep") or []), replication_notes.strip()]))
    if target_duration is not None and abs(result["duration"] - float(target_duration)) > 0.08:
        raise ValueError(f"镜头表时长 {result['duration']:g} 与目标 {float(target_duration):g} 不一致")
    result.update(candidate_id=candidate_id, aweme_id=candidate.get("aweme_id"),
                  reference_url=candidate.get("url") or "",
                  source_video_file="benchmark/video.mp4")
    if uploaded is None:
        result["source_video_url"] = source
    reference_duration = candidate.get("duration_s") or candidate.get("duration")
    if reference_duration is None and candidate.get("local_file"):
        reference_duration = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", candidate["local_file"],
        ], text=True).strip()
    if reference_duration is not None:
        result["reference_duration"] = float(reference_duration)
    if target_duration is not None:
        result["target_duration"] = float(target_duration)
        mapping = result.get("beat_mapping")
        if transcript and (not isinstance(mapping, list) or not mapping):
            raise ValueError("目标时长拆解必须输出 beat_mapping")
        if transcript:
            _validate_timeline([{"beat": row.get("beat"), "start": row.get("target_start"),
                                 "end": row.get("target_end")} for row in mapping],
                               float(target_duration), "beat_mapping", "beat")
    if transcript:
        result["transcript"] = transcript
    result.update(research_batch=str(batch.resolve()), reference_author=candidate.get("author") or "",
                  reference_title=candidate.get("title") or candidate.get("desc") or "")
    if uploaded is not None:
        result.update(source="local_upload_whole_video", media_deleted=True)
    result["benchmark_media"] = benchmark_media
    write_json(project / "shots.json", result)
    return result


def sync_must_keep(project):
    """把已由用户确认并送入拆解模型的复刻要点同步进旧运行的 must_keep。"""
    project = Path(project)
    data = read_json(project / "shots.json") or {}
    meta = read_json(project / "project.json", {}) or {}
    note = str(meta.get("replication_notes") or "").strip()
    if not note:
        raise ValueError("project.json 缺少 replication_notes")
    data["must_keep"] = list(dict.fromkeys([*(data.get("must_keep") or []), note]))
    write_json(project / "shots.json", data)
    return data["must_keep"]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "sync-must-keep":
        parser = argparse.ArgumentParser(description="把旧运行的复刻要点同步到 shots.json.must_keep")
        parser.add_argument("sync_must_keep")
        parser.add_argument("--project", required=True)
        args = parser.parse_args(argv)
        print(json.dumps({"must_keep": sync_must_keep(args.project)}, ensure_ascii=False, indent=2))
        return 0
    parser = argparse.ArgumentParser(description="完整读取对标视频并输出连续镜头表")
    parser.add_argument("--project", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--target-duration", type=float, help="目标成片时长（秒），可与对标不同")
    args = parser.parse_args(argv)
    result = build(args.project, args.batch, args.id, target_duration=args.target_duration)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
