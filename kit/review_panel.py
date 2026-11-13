"""三专家评审面板：机器检查 → 三位专家打分 → 合并 → 闸门判定。

流程（见 references/architecture.md）
1. 机器检查（计算型，阻塞级）：时长、分辨率、宽高比、文件大小。不过则不交给专家，直接 repair（回到 generate）。
2. 三位专家（推断型，见 config/experts.yaml）各自打分，只负责自己的维度。
3. gate.merge_judges：逐项取较低值；否决项取并集。
4. gate.evaluate：加权总分 ≥ 8.0、任一维度 ≥ 6.5、无否决，才 pass。

输出：reviews/<take>.json（完整记录）与 reviews/<take>.decision.json（判定结果，供看板和人工决定使用）。
"""
import argparse
import copy
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

KIT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KIT)

import yaml  # noqa: E402

import gate  # noqa: E402
import media_host  # noqa: E402
import settings  # noqa: E402
from common import _read, _write, parse_json, read_anchor  # noqa: E402
import loop  # noqa: E402  (提供 gemini()、_frames()、_data_uri())

EXPERTS_PATH = os.path.join(KIT, "config", "experts.yaml")


def brief_duration(brief):
    default = settings.get_float("VIDEO_DURATION")
    segments = brief.get("segments") or []
    if segments:
        return sum(float(s.get("duration", default)) for s in segments)
    return float(brief.get("duration", default))


def brief_text(brief):
    if brief.get("summary"):
        return brief["summary"]
    if brief.get("segments"):
        return "\n".join(f"第 {i} 段：{s.get('prompt', '')}" for i, s in enumerate(brief["segments"], 1))
    return brief.get("prompt", "")


def load_experts(path=EXPERTS_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _probe(video):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                   "-of", "json", str(video)])
    return json.loads(out.decode("utf-8"))


def machine_checks(video, brief):
    """计算型检查（阻塞级）。返回 {"blocking": [...], "info": [...], "facts": {...}}。"""
    blocking, info, facts = [], [], {}
    if not Path(video).exists():
        return {"blocking": ["没有成片文件"], "info": [], "facts": {}}
    pr = _probe(video)
    v = next((s for s in pr.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        blocking.append("没有视频流")
    else:
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        dur = float(pr.get("format", {}).get("duration", 0))
        facts.update(width=w, height=h, duration=round(dur, 2), codec=v.get("codec_name"))
        resolution = str(brief.get("resolution", "768p"))
        base_resolution = settings.base_resolution(resolution)
        if base_resolution.endswith("p") and base_resolution[:-1].isdigit():
            want = int(base_resolution[:-1])
            if min(w, h) != want:
                blocking.append(f"短边 {min(w, h)} 与分辨率 {want}p 不符")
        else:
            match = re.match(r"(\d+)\*(\d+)px", resolution)
            if match and {w, h} != {int(match.group(1)), int(match.group(2))}:
                blocking.append(f"画面 {w}×{h} 与分辨率 {resolution} 不符")
        ar = brief.get("aspect_ratio", "9:16")
        a, b = (int(x) for x in ar.split(":"))
        if w and abs((h / w) - (b / a)) > 0.03 and abs((w / h) - (b / a)) > 0.03:
            blocking.append(f"画面 {w}×{h} 与宽高比 {ar} 不符")
        if brief.get("video_route") != "motion" or brief.get("duration") is not None:
            want_dur = brief_duration(brief)
            if abs(dur - want_dur) > 1.0:
                blocking.append(f"时长 {dur:.1f}s 与简报 {want_dur:g}s 相差超过 1 秒")
    size = Path(video).stat().st_size
    facts["bytes"] = size
    if size < 50_000:
        blocking.append(f"文件只有 {size} 字节，疑似空视频")
    return {"blocking": blocking, "info": info, "facts": facts}


def _seam_events(video, joins):
    """用 ffmpeg 检查拼接点附近的黑帧与异常大场景跳变；返回可复核的事件。"""
    if not joins:
        return []
    cmd = ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(video), "-vf",
           "blackdetect=d=0.04:pic_th=0.98,select='gt(scene,0.85)',metadata=print", "-an", "-f", "null", "-"]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    events = []
    for match in re.finditer(r"black_start:([0-9.]+).*?black_end:([0-9.]+)", result.stderr):
        start, end = float(match.group(1)), float(match.group(2))
        if any(start - 0.15 <= point <= end + 0.15 for point in joins):
            events.append({"kind": "black", "at": round((start + end) / 2, 3)})
    for match in re.finditer(r"pts_time:([0-9.]+)[\s\S]{0,240}?lavfi\.scene_score=([0-9.]+)", result.stderr):
        at, score = float(match.group(1)), float(match.group(2))
        if any(abs(at - point) <= 0.15 for point in joins):
            events.append({"kind": "jump", "at": round(at, 3), "score": round(score, 3)})
    return events


def concat_machine_checks(video, brief):
    """父成片只做本地检查：基础媒体属性、音轨和各拼接点。"""
    result = machine_checks(video, brief)
    if not Path(video).exists():
        return result
    probe = _probe(video)
    has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))
    result["facts"]["has_audio"] = has_audio
    if not has_audio:
        result["blocking"].append("拼接成片没有音轨")
    durations = [float(s.get("duration", settings.get_float("VIDEO_DURATION"))) for s in brief.get("segments") or []]
    elapsed, joins = 0.0, []
    for duration in durations[:-1]:
        elapsed += duration
        joins.append(elapsed)
    events = _seam_events(video, joins)
    result["facts"]["joins"] = joins
    result["facts"]["seam_events"] = events
    for event in events:
        label = "黑帧" if event["kind"] == "black" else "疑似跳帧"
        result["blocking"].append(f"拼接处 {event['at']:.2f}s 检出{label}")
    return result


def _offset_timecode(value, offset):
    if not value or not offset:
        return value
    def replace(match):
        seconds = int(match.group(1) or 0) * 60 + float(match.group(2)) + offset
        minute = int(seconds // 60)
        remain = seconds % 60
        return f"{minute:02d}:{remain:04.1f}" if not remain.is_integer() else f"{minute:02d}:{int(remain):02d}"
    return re.sub(r"(?:(\d+):)?(\d+(?:\.\d+)?)", replace, str(value))


def aggregate_segment_reviews(project, take, segment_ids, brief):
    """汇总逐段评审；任一段不通过则父成片不通过，问题时间码平移到整条视频。"""
    project = Path(project)
    machine = concat_machine_checks(project / "takes" / f"{take}.mp4", brief)
    offset, segment_rows, experts, decisions = 0.0, [], [], []
    durations = [float(s.get("duration", settings.get_float("VIDEO_DURATION"))) for s in brief.get("segments") or []]
    for index, sid in enumerate(segment_ids):
        rec_path = project / "reviews" / f"{sid}.json"
        rec = _read(rec_path) if rec_path.exists() else {}
        job_path = project / "jobs" / f"{sid}.json"
        job = _read(job_path) if job_path.exists() else {}
        decision = rec.get("decision") or {"decision": None, "reason": "该段没有评审记录"}
        decisions.append(decision)
        shifted = []
        for expert in rec.get("experts") or []:
            item = copy.deepcopy(expert)
            item.setdefault("judge", {})["segment"] = sid
            for issue in item.get("issues") or []:
                issue["segment"] = sid
                issue["at"] = _offset_timecode(issue.get("at"), offset)
            shifted.append(item)
        experts.extend(shifted)
        segment_rows.append({"id": sid, "offset": offset, "duration": durations[index],
                             "mode": rec.get("mode"), "video_fallback": rec.get("video_fallback"),
                             "model": job.get("model"), "resolution": job.get("resolution"),
                             "video_route": job.get("video_route"),
                             "decision": decision, "experts": shifted})
        offset += durations[index]

    statuses = [d.get("decision") for d in decisions]
    vetoes = sorted({v for d in decisions for v in (d.get("vetoes") or [])})
    lows = sorted({v for d in decisions for v in (d.get("low_dimensions") or [])})
    uncited = [dict(item, segment=segment_ids[index])
               for index, row in enumerate(decisions) for item in (row.get("uncited_low") or [])]
    weights = [d.get("weighted") for d in decisions if isinstance(d.get("weighted"), (int, float))]
    if machine["blocking"]:
        decision = {"attempt": brief.get("attempt", 1), "decision": "repair", "stage": "glitches",
                    "retry_from": "generate", "reason": "父成片机器检查未通过：" + "；".join(machine["blocking"]),
                    "weighted": min(weights) if weights else None, "low_dimensions": lows, "vetoes": vetoes}
    elif any(value == "escalate" for value in statuses):
        failed = [segment_ids[i] for i, value in enumerate(statuses) if value == "escalate"]
        reasons = [decisions[i].get("reason") for i, value in enumerate(statuses)
                   if value == "escalate" and decisions[i].get("reason")]
        decision = {"attempt": brief.get("attempt", 1), "decision": "escalate", "stage": "segments",
                    "reason": "以下分段需人工处理：" + "、".join(failed) +
                              (("；" + "；".join(reasons)) if reasons else ""),
                    "weighted": min(weights) if weights else None, "low_dimensions": lows,
                    "vetoes": vetoes, "uncited_low": uncited}
    elif statuses and all(value == "pass" for value in statuses):
        decision = {"attempt": brief.get("attempt", 1), "decision": "pass", "stage": "pass",
                    "reason": "所有分段均通过，父成片机器检查通过", "weighted": min(weights) if weights else None,
                    "low_dimensions": lows, "vetoes": vetoes}
    elif any(value == "repair" for value in statuses):
        failed = [segment_ids[i] for i, value in enumerate(statuses) if value != "pass"]
        decision = {"attempt": brief.get("attempt", 1), "decision": "repair", "stage": "segments",
                    "retry_from": "generate", "reason": "以下分段未通过：" + "、".join(failed),
                    "weighted": min(weights) if weights else None, "low_dimensions": lows, "vetoes": vetoes}
    else:
        decision = {"attempt": brief.get("attempt", 1), "decision": None,
                    "reason": "至少一段评审未完成或需人工处理", "weighted": min(weights) if weights else None,
                    "low_dimensions": lows, "vetoes": vetoes}
    parent_job_path = project / "jobs" / f"{take}.json"
    parent_job = _read(parent_job_path) if parent_job_path.exists() else {}
    record = {"take": take, "attempt": brief.get("attempt", 1), "mode": "segments", "machine": machine,
              "generation": {"model": parent_job.get("model"), "resolution": parent_job.get("resolution"),
                             "video_route": parent_job.get("video_route")},
              "segments": segment_rows, "experts": experts, "decision": decision, "versions": settings.stamp()}
    _write(project / "reviews" / f"{take}.json", record)
    _write(project / "reviews" / f"{take}.decision.json", decision)
    return decision


def frame_times(n, duration):
    """抽帧时间表：与 loop._frames 的 fps=n/dur 一致，第 k 帧在 (k-1)*dur/n 秒。给专家写时间码用。"""
    step = duration / n
    out = []
    for k in range(n):
        t = k * step
        out.append(f"f{k + 1:02d}={int(t // 60):02d}:{t % 60:04.1f}")
    return ", ".join(out)


NO_MUST_KEEP = "（简报没有列出必须项：只按简报摘要判断，无法判断的写'看不清'，不要自己假设动作顺序）"
AUDIO_NONE = "简报没有口播或台词：确认没有意外人声；没有则 audio_text 给 10 分，不列 issues。"


def audio_rule(brief, audio_available=True):
    voiceover = brief.get("voiceover")
    lines = brief.get("voiceover_lines") or []
    if not voiceover and not lines:
        return AUDIO_NONE
    if not audio_available:
        return ("当前因 MEDIA_UPLOAD=off 回退抽帧，无法取得声音：audio_text 的各项写看不清，"
                "给 7 分，并写一条 cause=audio_sync 的 issue 说明需人工听取；不要猜语言或台词。")
    expected = voiceover if isinstance(voiceover, str) else json.dumps(voiceover, ensure_ascii=False)
    line_text = "；".join(str(x) for x in lines)
    return ("简报有口播或台词，必须真正听完整条视频：检查是否有人声、语言是否与简报一致、"
            "台词是否与简报一致、字幕是否与口播一致。"
            f"简报口播要求：{expected or '（见台词）'}；台词：{line_text or '（未逐句列出）'}。"
            "不一致时写 issue，cause=audio_sync；关键词读错可用 cause=keyword_misspoken。")


def must_keep_text(brief):
    """必须项来自简报本身：must_keep（列表）优先；自写脚本用各镜头动作；都没有则明说没有。"""
    items = brief.get("must_keep")
    if not items and isinstance(brief.get("script"), dict):
        items = [f"{s.get('start')}–{s.get('end')}s：{s.get('action_zh') or s.get('scene_zh') or ''}"
                 for s in brief["script"].get("shots", [])]
    if not items:
        return NO_MUST_KEEP
    return "\n      ".join(f"{i + 1}. {x}" for i, x in enumerate(items))


def _expert_prompt(exp, brief, anchor, timecode_text="", audio_available=True):
    shared = exp.get("shared_rules", "")
    if not audio_available:
        shared = (shared.replace("产品参考图和整条视频", "产品参考图和抽帧")
                        .replace("完整观看视频并真正听声音。", "当前是抽帧回退，只按给定帧判断，声音不可用。"))
    focus = (exp["focus"].strip()
             .replace("{must_keep}", must_keep_text(brief))
             .replace("{duration}", f"{brief_duration(brief):g}")
             .replace("{audio_rule}", audio_rule(brief, audio_available))
             .replace("{shot_list}", json.dumps(brief.get("segments") or brief.get("shot") or [], ensure_ascii=False)[:4000]))
    return (exp["prompt"]
            .replace("{brief}", brief_text(brief)[:800])
            .replace("{anchor}", anchor or "（简报未提供锚点）")
            .replace("{frame_times}", timecode_text or "按整条视频实际时间填写，格式 00:05 或 00:05–00:07")
            .replace("{timecode_rule}", timecode_text or "按整条视频实际时间填写，格式 00:05 或 00:05–00:07")
            .replace("{shared_rules}", shared.strip())
            .replace("{focus}", focus))


def _normalize_checks(obj):
    """确保存档的 checks 叶子只有三种状态；模型附加说明移入 notes。"""
    allowed = ("通过", "失败", "看不清")
    extras = []

    def walk(value, path=""):
        if isinstance(value, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in value.items()}
        text = str(value).strip()
        status = next((x for x in allowed if text.startswith(x)), None)
        if not status:
            # 模型写了说明而不是状态：不作废整位专家，按"看不清"记录，原文移到 notes，便于人看
            extras.append(f"{path}: {text}")
            return "看不清"
        if text != status:
            extras.append(f"{path}: {text}")
        return status

    obj["checks"] = walk(obj.get("checks") or {})
    if extras:
        old = str(obj.get("notes") or "").strip()
        obj["notes"] = "；".join(x for x in (old, "检查项原说明：" + "；".join(extras)) if x)
    return obj


def run_expert(exp, project_name, take, brief, anchor, refs, video_url=None, frames=None, reference_url=None,
               evidence_request=None):
    frames = frames or []
    reference_url = reference_url or brief.get("replication_reference")
    ft = frame_times(len(frames), brief_duration(brief)) if frames else ""
    prompt = _expert_prompt(exp, brief, anchor, ft, audio_available=bool(video_url))
    is_replication = exp.get("id") == "R"
    suffix = "\n前面是产品参考图，接着是对标视频，最后是整条成片。请完整对照观看并按成片实际时间写时间码。" \
        if is_replication and reference_url and video_url else (
            "\n前面是产品参考图，最后是整条成片。请完整观看并按实际时间写时间码。" if video_url
            else "\n前面是产品参考图，后面是成片抽帧（按时间顺序）；声音不可用。")
    if evidence_request:
        missing = "；".join(f"{item['dim']} {item['score']} 分" for item in evidence_request)
        suffix += ("\n\n【补证据，只追问这一次】你刚才给了以下低分但没有写对应问题：" + missing +
                   "。请重新检查这些维度并输出完整 JSON：每个低于 6.5 分的维度必须写出至少一条带实际时间码的具体问题；"
                   "如果复看后找不到问题，就改成与你实际判断一致的分数。不要沿用无证据低分。")
    parts = [{"type": "text", "text": prompt + suffix}]
    parts += [{"type": "image_url", "image_url": {"url": loop._data_uri(p)}} for p in refs]
    if is_replication and reference_url:
        parts.append({"type": "video_url", "video_url": reference_url})
    if video_url:
        parts.append({"type": "video_url", "video_url": video_url})
    else:
        parts += [{"type": "image_url", "image_url": {"url": loop._data_uri(p)}} for p in frames]
    text = loop.gemini(parts, project=project_name, ref=f"{take}:{exp['id']}",
                       max_tokens=settings.get_int("REVIEW_MAX_TOKENS"),
                       temperature=exp.get("temperature", 0.1), role="review")
    obj = parse_json(text, require=("scores",))
    if not isinstance(obj, dict) or not isinstance(obj.get("scores"), dict):
        raise ValueError("expert output truncated or missing scores")
    try:
        _normalize_checks(obj)
    except ValueError:
        return None
    obj["judge"] = {"id": exp["id"], "name": exp["name"], "temperature": exp.get("temperature", 0.1),
                    "mode": "video" if video_url else "frames", "frames": len(frames), "frame_times": ft}
    return obj


def _retry_evidence_once(exp, obj, project, take, brief, anchor, refs, video_url, frames, reference_url):
    """一位专家至多补问一次。失败时保留原结果，让闸门把无证据低分交给人。"""
    _, uncited = gate.normalize_review(obj)
    if not uncited:
        return obj
    retry_log = [{"dimension": item["dim"], "original_score": item["score"]} for item in uncited]
    try:
        revised = run_expert(exp, project.name, take, brief, anchor, refs, video_url=video_url, frames=frames,
                             reference_url=reference_url, evidence_request=uncited)
        if not isinstance(revised, dict):
            raise ValueError("补问结果无效")
    except Exception as exc:
        for row in retry_log:
            row.update(new_score=None, evidence_added=False, error=type(exc).__name__)
        obj["evidence_retry"] = retry_log
        return obj
    for row in retry_log:
        dim = row["dimension"]
        row.update(new_score=(revised.get("scores") or {}).get(dim),
                   evidence_added=gate._cited(revised, dim))
    revised["evidence_retry"] = retry_log
    return revised


def _reachable(url):
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    for method in ("HEAD", "GET"):
        headers = {"User-Agent": settings.get("HTTP_USER_AGENT")}
        if method == "GET":
            headers["Range"] = "bytes=0-0"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=20) as r:
                return r.status in (200, 206)
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405):
                continue
            return False
        except (urllib.error.URLError, OSError):
            return False
    return False


def _video_source(project, take, video, brief=None):
    job_path = project / "jobs" / f"{take}.json"
    job = _read(job_path) if job_path.exists() else {}
    if _reachable(job.get("remote_url")):
        return "video", job["remote_url"], None
    if job.get("remote_url") and (brief or {}).get("segment_of"):
        return "frames", None, None
    if settings.get("MEDIA_UPLOAD") == "off":
        return "frames", None, None
    info = media_host.upload(video)
    return "video", info["url"], info


def _run_panel(cfg, project, take, brief, anchor, refs_p, video_url, frames, reference_url=None):
    """跑四位专家。返回 (judges, failed, not_ingested)。not_ingested 为 True 表示模型没读到视频，本轮作废。"""
    judges, failed = [], []
    for exp in cfg["experts"]:
        if exp.get("id") == "R" and not brief.get("replication_reference"):
            judges.append({"judge": {"id": "R", "name": exp.get("name"), "mode": "not_applicable"},
                           "scores": {"replication_fidelity": gate.NEUTRAL}, "checks": {},
                           "vetoes": [], "issues": [], "notes": "该简报不是对标复刻，维度不适用"})
            continue
        if exp.get("id") == "R" and not reference_url:
            failed.append({"id": "R", "kind": "reference_unavailable"})
            continue
        obj, last_err = None, None
        for attempt_no in range(1, 3):  # 只对上游瞬时错误（5xx、429、超时断连、输出截断）重试 1 次；评审只读
            try:
                expert_brief = dict(brief)
                if reference_url:
                    expert_brief["replication_reference"] = reference_url
                obj = run_expert(exp, project.name, take, expert_brief, anchor, refs_p,
                                 video_url=video_url, frames=frames)
                last_err = None
                break
            except loop.VideoNotIngested:
                return [], [], True
            except Exception as e:
                last_err = e
                code = getattr(e, "code", None)
                print(f"{take} {exp['id']} 第 {attempt_no} 次调用失败：{type(e).__name__} {code or ''}")
                transient = (isinstance(code, int) and (code >= 500 or code == 429)) or code is None
                if not transient:
                    break
                import time
                time.sleep(45 if code == 429 else 15)
        if last_err is not None:
            failed.append({"id": exp["id"], "kind": "call_error", "error": type(last_err).__name__,
                           "status": getattr(last_err, "code", None)})
        elif obj is None:
            failed.append({"id": exp["id"], "kind": "invalid_output"})
        else:
            obj = _retry_evidence_once(exp, obj, project, take, brief, anchor, refs_p,
                                       video_url, frames, reference_url)
            judges.append(obj)
    return judges, failed, False


def review_take(project, take, refs, experts_path=EXPERTS_PATH, review_id=None):
    project = Path(project)
    output_take = review_id or take
    if review_id:
        targets = [project / "reviews" / f"{output_take}.json",
                   project / "reviews" / f"{output_take}.decision.json"]
        if any(path.exists() for path in targets):
            raise FileExistsError(f"新评审文件已存在：{output_take}")
    brief = _read(project / "briefs" / f"{take}.json")
    video = project / "takes" / f"{take}.mp4"
    attempt = brief.get("attempt", 1)
    anchor = brief.get("product_anchor") or read_anchor(project)
    job_path = project / "jobs" / f"{take}.json"
    job = _read(job_path) if job_path.exists() else {}
    check_brief = dict(brief)
    if job.get("composed") and job.get("resolution"):
        check_brief["resolution"] = job["resolution"]
    mc = machine_checks(video, check_brief)
    record = {"take": output_take, "attempt": attempt, "mode": "video", "machine": mc,
              "experts": [], "versions": settings.stamp()}
    if output_take != take:
        record["source_take"] = take
    record["generation"] = {"model": job.get("model"), "resolution": job.get("resolution"),
                            "video_route": job.get("video_route")}
    if mc["blocking"]:
        # 计算型阻塞：不花钱调用专家，直接退回生成
        decision = {"attempt": attempt, "decision": "repair", "stage": "glitches", "retry_from": "generate",
                    "reason": "机器检查未通过：" + "；".join(mc["blocking"]),
                    "weighted": None, "low_dimensions": [], "vetoes": []}
        record["decision"] = decision
        _write(project / "reviews" / f"{output_take}.json", record)
        _write(project / "reviews" / f"{output_take}.decision.json", decision)
        return decision
    mode, video_url, upload_info = _video_source(project, take, video, brief)
    record["mode"] = mode
    if mode == "frames" and job.get("remote_url") and brief.get("segment_of"):
        record["video_fallback"] = "分段 remote_url 已失效，已改用抽帧；声音不可用"
    frames = loop._frames(video, project / "reviews" / "frames" / output_take) if mode == "frames" else []
    cfg = load_experts(experts_path)
    refs_p = [Path(p) for p in refs]
    reference_url = brief.get("replication_reference")
    if reference_url and not _reachable(reference_url):
        reference_url = None
    try:
        judges, failed, not_ingested = _run_panel(cfg, project, output_take, brief, anchor, refs_p, video_url, frames,
                                                  reference_url)
        if not_ingested and settings.get("MEDIA_UPLOAD") != "off":
            # 模型没读到视频：作废这一轮，改用抽帧重评，并记下原因（抽帧听不到声音，口播判为需人工听）
            print(f"{take} 模型没有读到视频，改用抽帧重评")
            mode, video_url = "frames", None
            record["mode"] = "frames"
            record["video_fallback"] = "模型没有读到视频（按 token 判断），已改用抽帧"
            frames = loop._frames(video, project / "reviews" / "frames" / output_take)
            judges, failed, _ = _run_panel(cfg, project, output_take, brief, anchor, refs_p, None, frames,
                                            reference_url)
        record["experts"] = judges
    finally:
        if upload_info:
            deleted = False
            error = ""
            try:
                deleted = media_host.delete(upload_info)
            except Exception as e:
                error = type(e).__name__
            media_host.log(upload_info, {"purpose": "review", "take": take,
                                         "deleted": deleted, "delete_error": error})
            if not deleted:
                print(f"{take} 临时媒体删除未确认，请检查 media_log.jsonl")
    if failed:
        record["expert_failures"] = failed
        decision = {"attempt": attempt, "decision": None,
                    "reason": f"专家评审未完成：{', '.join(x['id'] for x in failed)}，需重跑评审（不重生成视频）"}
        record["decision"] = decision
        _write(project / "reviews" / f"{output_take}.json", record)
        _write(project / "reviews" / f"{output_take}.decision.json", decision)
        return decision
    merged = gate.merge_judges(judges)
    res = gate.evaluate(merged, attempt)
    record["merged"] = merged
    record["decision"] = res
    _write(project / "reviews" / f"{output_take}.json", record)
    _write(project / "reviews" / f"{output_take}.decision.json", res)
    return res


def reevaluate(project, take):
    """用已保存的专家原始打分重新合并判定，不调用任何模型，不花钱。用于规则修正后的回放。"""
    project = Path(project)
    rec_path = project / "reviews" / f"{take}.json"
    rec = _read(rec_path)
    judges = rec.get("experts") or []
    if not judges:
        return rec.get("decision")
    if not any("replication_fidelity" in (row.get("scores") or {}) for row in judges):
        judges = list(judges) + [{"scores": {"replication_fidelity": gate.NEUTRAL},
                                  "vetoes": [], "issues": [],
                                  "judge": {"id": "legacy", "name": "R8 前历史兼容"}}]
    attempt = rec.get("attempt", 1)
    merged = gate.merge_judges(judges)
    res = gate.evaluate(merged, attempt)
    rec["merged"] = merged
    rec["decision"] = res
    rec["reevaluated"] = True
    _write(rec_path, rec)
    _write(project / "reviews" / f"{take}.decision.json", res)
    return res


def _summary(take, res):
    if res.get("decision") is None:
        return f"{take}: 未判定（{res.get('reason')}）"
    w = res.get("weighted")
    low = ",".join(res.get("low_dimensions", []) or []) or "-"
    v = ",".join(res.get("vetoes", []) or []) or "-"
    return f"{take}: {res['decision']}  加权={w}  低于下限={low}  否决={v}  {res.get('reason', '')}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="三专家评审面板")
    ap.add_argument("--project", required=True)
    ap.add_argument("--take", required=True, nargs="+")
    ap.add_argument("--refs", required=True, help="产品参考图，逗号分隔")
    ap.add_argument("--review-id", help="只用于单条复评：读取 --take 的成片，写入这个新的评审 ID，不覆盖旧文件")
    a = ap.parse_args(argv)
    if a.review_id and len(a.take) != 1:
        ap.error("--review-id 只能与一个 --take 一起使用")
    refs = [p for p in a.refs.split(",") if p.strip()]
    for t in a.take:
        res = review_take(a.project, t, refs, review_id=a.review_id)
        print(_summary(a.review_id or t, res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
