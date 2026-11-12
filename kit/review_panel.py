"""三专家评审面板：机器检查 → 三位专家打分 → 合并 → 闸门判定。

流程（ARCHITECTURE-v2 §4）
1. 机器检查（计算型，阻塞级）：时长、分辨率、宽高比、文件大小。不过则不交给专家，直接 repair（回到 generate）。
2. 三位专家（推断型，见 config/experts.yaml）各自打分，只负责自己的维度。
3. gate.merge_judges：逐项取较低值；否决项取并集。
4. gate.evaluate：加权总分 ≥ 8.0、任一维度 ≥ 6.5、无否决，才 pass。

输出：reviews/<take>.json（完整记录）与 reviews/<take>.decision.json（判定结果，供看板和人工决定使用）。
"""
import argparse
import json
import os
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
from common import _read, _write, parse_json  # noqa: E402
import loop  # noqa: E402  (提供 gemini()、_frames()、_data_uri())

EXPERTS_PATH = os.path.join(KIT, "config", "experts.yaml")


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
        want = int(str(brief.get("resolution", "768p")).rstrip("p") or 768)
        if min(w, h) != want:
            blocking.append(f"短边 {min(w, h)} 与分辨率 {want}p 不符")
        ar = brief.get("aspect_ratio", "9:16")
        a, b = (int(x) for x in ar.split(":"))
        if w and abs((h / w) - (b / a)) > 0.03 and abs((w / h) - (b / a)) > 0.03:
            blocking.append(f"画面 {w}×{h} 与宽高比 {ar} 不符")
        want_dur = float(brief.get("duration", 10))
        if abs(dur - want_dur) > 1.0:
            blocking.append(f"时长 {dur:.1f}s 与简报 {want_dur:g}s 相差超过 1 秒")
    size = Path(video).stat().st_size
    facts["bytes"] = size
    if size < 50_000:
        blocking.append(f"文件只有 {size} 字节，疑似空视频")
    return {"blocking": blocking, "info": info, "facts": facts}


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
             .replace("{duration}", f"{float(brief.get('duration', 10)):g}")
             .replace("{audio_rule}", audio_rule(brief, audio_available)))
    return (exp["prompt"]
            .replace("{brief}", (brief.get("summary") or brief.get("prompt", ""))[:800])
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
            raise ValueError(f"检查项 {path} 不是规定状态")
        if text != status:
            extras.append(f"{path}: {text}")
        return status

    obj["checks"] = walk(obj.get("checks") or {})
    if extras:
        old = str(obj.get("notes") or "").strip()
        obj["notes"] = "；".join(x for x in (old, "检查项原说明：" + "；".join(extras)) if x)
    return obj


def run_expert(exp, project_name, take, brief, anchor, refs, video_url=None, frames=None):
    frames = frames or []
    ft = frame_times(len(frames), float(brief.get("duration", 10))) if frames else ""
    prompt = _expert_prompt(exp, brief, anchor, ft, audio_available=bool(video_url))
    suffix = "\n前面是产品参考图，最后是整条成片。请完整观看并按实际时间写时间码。" if video_url \
        else "\n前面是产品参考图，后面是成片抽帧（按时间顺序）；声音不可用。"
    parts = [{"type": "text", "text": prompt + suffix}]
    parts += [{"type": "image_url", "image_url": {"url": loop._data_uri(p)}} for p in refs]
    if video_url:
        parts.append({"type": "video_url", "video_url": video_url})
    else:
        parts += [{"type": "image_url", "image_url": {"url": loop._data_uri(p)}} for p in frames]
    text = loop.gemini(parts, project=project_name, ref=f"{take}:{exp['id']}",
                       max_tokens=settings.get_int("REVIEW_MAX_TOKENS"),
                       temperature=exp.get("temperature", 0.1), role="review")
    obj = parse_json(text)
    if not isinstance(obj, dict):
        return None
    try:
        _normalize_checks(obj)
    except ValueError:
        return None
    obj["judge"] = {"id": exp["id"], "name": exp["name"], "temperature": exp.get("temperature", 0.1),
                    "mode": "video" if video_url else "frames", "frames": len(frames), "frame_times": ft}
    return obj


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


def _video_source(project, take, video):
    job_path = project / "jobs" / f"{take}.json"
    job = _read(job_path) if job_path.exists() else {}
    if _reachable(job.get("remote_url")):
        return "video", job["remote_url"], None
    if settings.get("MEDIA_UPLOAD") == "off":
        return "frames", None, None
    info = media_host.upload(video)
    return "video", info["url"], info


def review_take(project, take, refs, experts_path=EXPERTS_PATH):
    project = Path(project)
    brief = _read(project / "briefs" / f"{take}.json")
    video = project / "takes" / f"{take}.mp4"
    attempt = brief.get("attempt", 1)
    anchor = brief.get("product_anchor") or _anchor_from_refs(project)
    mc = machine_checks(video, brief)
    record = {"take": take, "attempt": attempt, "mode": "video", "machine": mc,
              "experts": [], "versions": settings.stamp()}
    if mc["blocking"]:
        # 计算型阻塞：不花钱调用专家，直接退回生成
        decision = {"attempt": attempt, "decision": "repair", "stage": "glitches", "retry_from": "generate",
                    "reason": "机器检查未通过：" + "；".join(mc["blocking"]),
                    "weighted": None, "low_dimensions": [], "vetoes": []}
        record["decision"] = decision
        _write(project / "reviews" / f"{take}.json", record)
        _write(project / "reviews" / f"{take}.decision.json", decision)
        return decision
    mode, video_url, upload_info = _video_source(project, take, video)
    record["mode"] = mode
    frames = loop._frames(video, project / "reviews" / "frames" / take) if mode == "frames" else []
    cfg = load_experts(experts_path)
    refs_p = [Path(p) for p in refs]
    judges, failed = [], []
    try:
        for exp in cfg["experts"]:
            try:
                obj = run_expert(exp, project.name, take, brief, anchor, refs_p,
                                 video_url=video_url, frames=frames)
            except Exception as e:
                print(f"{take} {exp['id']} 调用失败：{type(e).__name__} {getattr(e, 'code', '')}")
                obj = None
                failed.append({"id": exp["id"], "kind": "call_error", "error": type(e).__name__,
                               "status": getattr(e, "code", None)})
            if obj is None:
                if not any(x["id"] == exp["id"] for x in failed):
                    failed.append({"id": exp["id"], "kind": "invalid_output"})
            else:
                judges.append(obj)
                record["experts"].append(obj)
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
        _write(project / "reviews" / f"{take}.json", record)
        _write(project / "reviews" / f"{take}.decision.json", decision)
        return decision
    merged = gate.merge_judges(judges)
    res = gate.evaluate(merged, attempt)
    record["merged"] = merged
    record["decision"] = res
    _write(project / "reviews" / f"{take}.json", record)
    _write(project / "reviews" / f"{take}.decision.json", res)
    return res


def reevaluate(project, take):
    """用已保存的专家原始打分重新合并判定，不调用任何模型，不花钱。用于规则修正后的回放。"""
    project = Path(project)
    rec_path = project / "reviews" / f"{take}.json"
    rec = _read(rec_path)
    judges = rec.get("experts") or []
    if not judges:
        return rec.get("decision")
    attempt = rec.get("attempt", 1)
    merged = gate.merge_judges(judges)
    res = gate.evaluate(merged, attempt)
    rec["merged"] = merged
    rec["decision"] = res
    rec["reevaluated"] = True
    _write(rec_path, rec)
    _write(project / "reviews" / f"{take}.decision.json", res)
    return res


def _anchor_from_refs(project):
    p = project / "product_anchor.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


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
    a = ap.parse_args(argv)
    refs = [p for p in a.refs.split(",") if p.strip()]
    for t in a.take:
        res = review_take(a.project, t, refs)
        print(_summary(t, res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
