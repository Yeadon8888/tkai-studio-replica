#!/usr/bin/env python3
"""闭环：拆解（调研）→ 简报 → 生成 → 评审 → 闸门 → takes.json → 看板。

子命令（每一步都可单独运行，都记账）：
  breakdown  --batch <调研批次> --ids C003[,C..]           Gemini 七维拆解（公开播放链接）→ breakdown.json
  gen        --project <项目目录> --brief <brief.json>       生成并保存远程地址，落盘后默认自动评审
  review     --project <项目目录> --take <T01> --refs a.jpg,b.jpg   三专家整条视频评审 → reviews/
  assemble   --project <项目目录>                            汇总 reviews/ 与 briefs 为 takes.json（闸门判定在 production.py）
  board      --project <项目目录> [--research <批次>] --out <html>

预算与失败规则：
- 生成任务提交后立即落盘 task_id；未完成前不重复提交；失败不自动重提。
- 每次 Gemini / 生成调用都写入 studio/ledger/costs.jsonl。
- 超出调用上限会直接拒绝（由调用方传入 --cap；流程见 references/architecture.md）。
"""
import argparse
import base64
import hashlib
import json
import re
import os
import shutil
import subprocess
import sys
import time
import datetime as dt
import urllib.error
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
import common  # noqa: E402
from common import CST, _data_uri, _now, _read, _write  # noqa: E402
import dashboard  # noqa: E402
import gate  # noqa: E402
import ledger  # noqa: E402
import production  # noqa: E402
import research  # noqa: E402
import tt_research as tt  # noqa: E402

import settings  # noqa: E402  模型、上限、路径都从 .env 读，见 settings.py

GEN_MODEL = settings.model("video")
GEMINI = settings.model("text")   # 兼容旧引用；各环节实际用 settings.model(role)
CST = dt.timezone(dt.timedelta(hours=8))

BREAKDOWN_PROMPT = """你在拆解一条 TikTok 对标视频，用于判断它能否作为产品视频的复刻参考。
只依据画面和声音，看不清写 0，不要编造。每个证据必须带时间码（格式 00:00–00:03）。

七个维度，每项 0–2 分（0=没有，1=有但弱，2=清楚有效）：
- 钩子：前 3 秒是否明确抓人
- 人：出镜的人是谁、表现是否真实
- 货：产品是否清楚出现、细节是否可见
- 场：场景是否服务于产品使用
- 证明：是否有展示、对比、测试等证明
- 行动：是否有明确的行动号召（CTA）
- 可复制：动作、镜头、道具是否容易用 AI 视频复刻

只输出一个 JSON 对象，不要其他文字：
{"dims": {"钩子": 0, "人": 0, "货": 0, "场": 0, "证明": 0, "行动": 0, "可复制": 0},
 "evidence": [{"dim": "钩子", "text": "...", "t": "00:00–00:03"}],
 "judgement": {"怎么读": "...", "学到什么": "...", "我们的判断": "...", "对我们": "...", "边界": "..."},
 "style": {"rendering": "真人实拍 | 2D 动画 | 3D 动画 | 微观/科普动画 | 混合", "description": "画面风格的具体描述：材质、光线、镜头感、色调、是否有线条或体素等，足够让另一个模型做出同样的风格"},
 "has_person": true 或 false,
 "risk": "AI 复刻风险一句话"}"""

class VideoNotIngested(RuntimeError):
    """请求里带了视频，但模型实际没有读到（prompt token 只有文字和图片的量）。
    2026-11-13 实测：Release 附件链接（有跳转）会被网关静默丢弃，模型照样编出结论。"""


def video_ingested(parts, usage):
    """按用量判断视频是否被读到：去掉文字 token 和每张图约 1100 token 后，至少还要有 VIDEO_MIN_TOKENS。
    用量里没有明细时返回 None（无法判断）。"""
    if not any(p.get("type") == "video_url" for p in parts):
        return True
    details = (usage or {}).get("prompt_tokens_details") or {}
    if "text_tokens" not in details or "prompt_tokens" not in (usage or {}):
        return None
    n_img = sum(1 for p in parts if p.get("type") == "image_url")
    rest = usage["prompt_tokens"] - details["text_tokens"] - 1100 * n_img
    return rest >= settings.get_int("VIDEO_MIN_TOKENS")


def gemini(parts, project, ref, max_tokens=1200, temperature=0.1, role="text"):
    """文本/理解模型调用。role 决定用哪个模型（.env 的 MODEL_<ROLE>，留空则用 MODEL_TEXT）。"""
    model = settings.model(role)
    cap = settings.get_int("LIMIT_TEXT_CALLS")
    used = ledger.count_text_calls(project)
    if used >= cap:
        raise SystemExit(f"文本模型调用已达上限 {cap}（运行 {project}），停止。")
    base, key = tt.load_tf_env()
    body = {"model": model, "messages": [{"role": "user", "content": parts}],
            "max_tokens": max_tokens, "temperature": temperature}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": settings.get("HTTP_USER_AGENT")}
    try:
        js = common.http_json("POST", base + "/v1/chat/completions", headers, body, timeout=240)
    except urllib.error.HTTPError as e:
        ledger.append(settings.get("TEXT_PROVIDER"), model, e.code, project=project, ref=ref, units=1, kind="text")
        raise
    text = js["choices"][0]["message"]["content"] or ""
    usage = js.get("usage") or {}
    seen = video_ingested(parts, usage)
    status = "ok" if seen is not False else "video_not_ingested"
    ledger.append(settings.get("TEXT_PROVIDER"), model, status, project=project, ref=ref, units=1, usage=usage, kind="text")
    if seen is False:
        raise VideoNotIngested("模型没有读到视频（按 token 判断），结果作废；检查视频地址是否需要跳转或已失效")
    return text


# ---------------------------------------------------------------- 1. 拆解（调研）

def cmd_breakdown(a):
    batch = Path(a.batch)
    cands = {c["candidate_id"]: c for c in _read(batch / "candidates.json")}
    out_path = batch / "breakdown.json"
    done = {r["aweme_id"]: r for r in _read(out_path)} if out_path.exists() else {}
    ids = [i.strip() for i in a.ids.split(",") if i.strip()]
    if len(ids) > a.cap:
        sys.exit(f"拆解 {len(ids)} 条超过上限 {a.cap}")
    for cid in ids:
        c = cands.get(cid)
        if not c:
            print(f"{cid} 不在候选清单中，跳过")
            continue
        if c["aweme_id"] in done:
            print(f"{cid} 已拆解，跳过（不重复计费）")
            continue
        url = next((u for u in c.get("play_urls") or [] if tt.media_reachable(u)), "")
        if not url:
            print(f"{cid} 视频链接不可用（已过期），需重新检索后再拆解；未计费")
            continue
        text = gemini([{"type": "text", "text": BREAKDOWN_PROMPT},
                       {"type": "video_url", "video_url": url}],
                      project=batch.name, ref=cid, max_tokens=4000, role="breakdown")
        obj = tt.parse_json_obj(text)
        if obj is None:
            print(f"{cid} 模型输出无法解析，已记账，原文前 200 字：{text[:200]}")
            continue
        obj["aweme_id"] = c["aweme_id"]
        obj["candidate_id"] = cid
        obj["breakdown_at"] = _now()
        done[c["aweme_id"]] = obj
        print(f"{cid} 拆解完成：" + " ".join(f"{k}{v}" for k, v in obj.get("dims", {}).items()))
    _write(out_path, list(done.values()))
    print(f"拆解结果 → {out_path}")


# ---------------------------------------------------------------- 2. 生成

def _find_url(js):
    if isinstance(js, dict):
        for k in ("video_url", "url"):
            v = js.get(k)
            if isinstance(v, str) and v.startswith("https://"):
                return v
        for v in js.values():
            r = _find_url(v)
            if r:
                return r
    elif isinstance(js, list):
        for v in js:
            r = _find_url(v)
            if r:
                return r
    return None


def _api(method, path, body=None):
    base, key = tt.load_tf_env()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": settings.get("HTTP_USER_AGENT")}
    return common.http_json(method, base + path, headers, body, timeout=120)


def _generate_single(a):
    project = Path(a.project)
    brief = _read(a.brief)
    tid = brief["id"]
    takes_dir = project / "takes"
    jobs_dir = project / "jobs"
    takes_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_path = jobs_dir / f"{tid}.json"
    # 2026-11-12 修复：两个进程同时运行会各自提交一次（TSP-S2 实际重复计费）。
    # 先用排他方式占位，占位成功才允许提交；占位失败说明已有进程在提交或已提交过。
    claim = jobs_dir / f"{tid}.claim"
    try:
        with claim.open("x", encoding="utf-8") as _c:
            _c.write(_now())
        claimed = True
    except FileExistsError:
        claimed = False
    existing = job_path.exists() and _read(job_path).get("status") not in ("failed",)
    if existing or (not claimed and not (job_path.exists() and _read(job_path).get("status") == "failed")):
        job = _read(job_path) if job_path.exists() else {"task_id": "?", "status": "claiming"}
        print(f"{tid} 已有提交占位或任务 {job.get('task_id')}（状态 {job.get('status')}），不重复提交")
        if not job_path.exists() or not job.get("task_id") or job.get("task_id") == "?":
            return  # 只有占位、没有任务号：不能轮询，交给人（占位文件保留，避免重复提交）
    else:
        if a.cap < 1:
            sys.exit("预算上限为 0，拒绝提交")
        try:
            images = [_data_uri(p) for p in brief["images"]]
        except OSError as e:
            claim.unlink(missing_ok=True)  # 还没有提交，释放占位
            sys.exit(f"参考图读取失败（未提交，占位已释放）：{e}")
        duration = brief.get("duration", settings.get_float("VIDEO_DURATION"))
        body = {"model": GEN_MODEL, "prompt": brief["prompt"], "images": images,
                "duration": duration, "resolution": brief.get("resolution", "768p"),
                "aspect_ratio": brief.get("aspect_ratio", "9:16")}
        try:
            js = _api("POST", "/v1/videos", body)
        except urllib.error.HTTPError as e:
            ledger.append("tokensfactory", GEN_MODEL, e.code, project=project.name, ref=tid, units=1)
            sys.exit(f"提交失败 HTTP {e.code}，不重试")
        task_id = js.get("id") or js.get("task_id") or (js.get("data") or {}).get("id")
        if not task_id:
            sys.exit(f"提交返回里没有任务号：{str(js)[:200]}")
        job = {"id": tid, "task_id": task_id, "model": GEN_MODEL, "status": "submitted",
               "submitted_at": _now(),
               "request": {k: (v if k != "images" else [f"sha256:{hashlib.sha256(x.encode()).hexdigest()[:12]} len={len(x)}" for x in v])
                           for k, v in body.items() if k != "prompt"} | {"prompt_chars": len(brief["prompt"])}}
        _write(job_path, job)
        ledger.append("tokensfactory", GEN_MODEL, "submitted", project=project.name, ref=tid, units=duration)
        print(f"{tid} 已提交，任务号 {task_id}（已落盘）")
    # 轮询：网络抖动只记录并继续，不丢任务（2026-11-12 实测 SSL EOF 曾让进程退出，任务停在 in_progress）
    transient = 0
    for i in range(a.max_polls):
        time.sleep(a.interval)
        try:
            js = _api("GET", f"/v1/videos/{job['task_id']}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            transient += 1
            print(f"{tid} 轮询第 {i+1} 次网络错误（已忽略，任务仍在）：{type(e).__name__}")
            if transient >= 30:
                job["last_error"] = str(e)[:200]
                _write(job_path, job)
                print(f"{tid} 连续网络错误过多，停止本次等待；任务号 {job['task_id']} 已保存，可稍后重跑")
                return
            continue
        transient = 0
        status = str(js.get("status") or (js.get("data") or {}).get("status") or "").lower()
        job["status"] = status or job.get("status")
        job["last_poll"] = _now()
        _write(job_path, job)
        print(f"{tid} 轮询 {i+1}: {job['status']}")
        if status in ("completed", "succeeded", "success"):
            url = _find_url(js)
            if not url:
                sys.exit(f"完成但没有找到视频地址：{str(js)[:300]}")
            out = takes_dir / f"{tid}.mp4"
            req = urllib.request.Request(url, headers={"User-Agent": "TokensFactory-Media/1.1"})
            with urllib.request.urlopen(req, timeout=300) as r, out.open("wb") as f:
                f.write(r.read())
            job.update(status="saved", video=str(out), saved_at=_now(),
                       remote_url=url, remote_url_at=_now())
            _write(job_path, job)
            # 终态记账：金额已在 submitted 行计过，这里 units=0，避免重复计费
            ledger.append("tokensfactory", GEN_MODEL, "saved", project=project.name, ref=tid, units=0)
            print(f"{tid} 已保存 → {out}")
            if getattr(a, "no_review", False):
                job.update(review_skipped=True,
                           review_skip_reason=getattr(a, "review_skip_reason", "命令行指定 --no-review"))
                _write(job_path, job)
            else:
                import review_panel  # 延迟导入，避免循环导入
                review_panel.review_take(project, tid, brief.get("images") or [])
                job.update(review_skipped=False, reviewed_at=_now())
                _write(job_path, job)
            return
        if status in ("failed", "error", "cancelled"):
            job["error"] = str(js)[:300]
            _write(job_path, job)
            ledger.append("tokensfactory", GEN_MODEL, "failed", project=project.name, ref=tid, units=0)
            print(f"{tid} 生成失败：{job['error']}（不自动重提）")
            return
    print(f"{tid} 轮询超时，任务仍在：{job['task_id']}，先查原任务，不重提")


def _segment_brief(brief, segment, segment_id):
    """把父简报的一段展开成现有单段生成逻辑可读的简报。"""
    child = {k: v for k, v in brief.items() if k not in ("id", "prompt", "duration", "images", "segments")}
    child.update({"id": segment_id, "prompt": segment["prompt"],
                  "duration": segment.get("duration", settings.get_float("VIDEO_DURATION")),
                  "images": segment.get("images", brief.get("images", []))})
    return child


def _brief_images(brief):
    images = list(brief.get("images") or [])
    for segment in brief.get("segments") or []:
        images.extend(segment.get("images") or [])
    return list(dict.fromkeys(images))


def brief_duration(brief):
    default = settings.get_float("VIDEO_DURATION")
    segments = brief.get("segments") or []
    if segments:
        return sum(float(s.get("duration", default)) for s in segments)
    return float(brief.get("duration", default))


def _probe_for_concat(video):
    raw = subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                   "-of", "json", str(video)])
    data = json.loads(raw.decode("utf-8"))
    video_stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    duration = float(data.get("format", {}).get("duration", 0))
    return video_stream, has_audio, duration


def concat_segments(videos, output):
    """统一画面、帧率和音轨后拼接；ffmpeg 失败时抛错，不留下半成品。"""
    videos = [Path(v) for v in videos]
    if not videos:
        raise ValueError("没有可拼接的视频段")
    probes = [_probe_for_concat(v) for v in videos]
    first = probes[0][0]
    width, height = int(first["width"]), int(first["height"])
    frame_rate = first.get("avg_frame_rate") or first.get("r_frame_rate") or "24/1"
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for video in videos:
        cmd += ["-i", str(video)]
    silent_inputs = {}
    for i, (_, has_audio, duration) in enumerate(probes):
        if not has_audio:
            silent_inputs[i] = len(videos) + len(silent_inputs)
            cmd += ["-f", "lavfi", "-t", f"{duration:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
    filters = []
    concat_inputs = []
    for i, (_, has_audio, _) in enumerate(probes):
        filters.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={frame_rate},"
                       f"format=yuv420p[v{i}]")
        audio_input = i if has_audio else silent_inputs[i]
        filters.append(f"[{audio_input}:a]aresample=48000,aformat=sample_fmts=fltp:"
                       f"sample_rates=48000:channel_layouts=stereo[a{i}]")
        concat_inputs.append(f"[v{i}][a{i}]")
    filters.append("".join(concat_inputs) + f"concat=n={len(videos)}:v=1:a=1[v][a]")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + ".joining.mp4")
    try:
        subprocess.run(cmd + ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                              "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(temp)],
                       check=True)
        temp.replace(output)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def cmd_gen(a):
    project = Path(a.project)
    brief = _read(a.brief)
    segments = brief.get("segments") or []
    if not segments:
        return _generate_single(a)
    tid = brief["id"]
    if len(segments) > a.cap:
        sys.exit(f"分段数 {len(segments)} 超过本次生成上限 {a.cap}")
    if not all(isinstance(s, dict) and s.get("prompt") for s in segments):
        sys.exit("segments 每一段都必须包含非空 prompt")
    (project / "briefs").mkdir(parents=True, exist_ok=True)
    (project / "takes").mkdir(parents=True, exist_ok=True)
    (project / "jobs").mkdir(parents=True, exist_ok=True)
    segment_videos = []
    segment_ids = []
    for index, segment in enumerate(segments, 1):
        sid = f"{tid}-s{index}"
        segment_ids.append(sid)
        out = project / "takes" / f"{sid}.mp4"
        reuse_from = segment.get("reuse_from")
        if reuse_from and not out.exists():
            source = project / "takes" / f"{reuse_from}.mp4"
            if not source.exists():
                print(f"{sid} 要复用的段不存在：{source}，停止且不拼接")
                return
            shutil.copy2(source, out)
            _write(project / "jobs" / f"{sid}.json",
                   {"id": sid, "status": "reused", "reuse_from": reuse_from,
                    "video": str(out), "saved_at": _now()})
            print(f"{sid} 已复用 {reuse_from}")
        elif not out.exists():
            child_path = project / "briefs" / f"{sid}.json"
            _write(child_path, _segment_brief(brief, segment, sid))
            child_args = argparse.Namespace(**vars(a))
            child_args.brief = str(child_path)
            child_args.no_review = True
            child_args.review_skip_reason = f"分段 {sid}，等待父成片 {tid} 拼接后统一评审"
            _generate_single(child_args)
        if not out.exists():
            print(f"{sid} 未生成成功，停止且不拼接；已成功的视频段保留")
            return
        segment_videos.append(out)
    final = project / "takes" / f"{tid}.mp4"
    final.unlink(missing_ok=True)
    try:
        concat_segments(segment_videos, final)
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        sys.exit(f"{tid} 分段拼接失败：{type(e).__name__}，未生成成片")
    parent_job = {"id": tid, "status": "saved", "video": str(final), "segments": segment_ids,
                  "duration": brief_duration(brief), "saved_at": _now()}
    _write(project / "jobs" / f"{tid}.json", parent_job)
    print(f"{tid} 已拼接 {len(segment_videos)} 段 → {final}")
    if getattr(a, "no_review", False):
        parent_job.update(review_skipped=True,
                          review_skip_reason=getattr(a, "review_skip_reason", "命令行指定 --no-review"))
    else:
        import review_panel
        review_panel.review_take(project, tid, _brief_images(brief))
        parent_job.update(review_skipped=False, reviewed_at=_now())
    _write(project / "jobs" / f"{tid}.json", parent_job)


# ---------------------------------------------------------------- 3. 评审

def _frames(video, out_dir, n=8):
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "default=nw=1:nk=1", str(video)]).decode().strip())
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f*.jpg"):
        old.unlink()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
                    f"fps={n / dur:.4f},scale=560:-2", "-frames:v", str(n), str(out_dir / "f%02d.jpg")], check=True)
    return sorted(out_dir.glob("f*.jpg"))


def cmd_review(a):
    """评审入口统一走三专家面板（review_panel）。旧的双视角实现已删除（见 studio-audit LEGACY-REVIEW）。"""
    import review_panel  # 延迟导入，避免与 review_panel 的 import loop 互相循环
    refs = [p for p in str(a.refs).split(",") if p.strip()]
    res = review_panel.review_take(a.project, a.take, refs)
    print(review_panel._summary(a.take, res))


# ---------------------------------------------------------------- 4. 汇总 与 看板

def series_root(brief_id, parents):
    """提示词系列的根：沿 retry_of / parent 往上找；没有记录时去掉 -r2、-a3 这类轮次后缀。"""
    seen, cur = set(), brief_id
    while parents.get(cur) and cur not in seen:
        seen.add(cur)
        cur = parents[cur]
    return re.sub(r"-(?:r|a)\d+$", "", cur)


def take_costs(project_name):
    """从账本汇总每条成片的花费（同一条被重复提交时会累加，能看出重复付费）。"""
    out = {}
    if not ledger.LEDGER_FILE.exists():
        return out
    for line in ledger.LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("project") != project_name or not r.get("ref") or r.get("estimate_cny") is None:
            continue
        if r.get("status") == "submitted":
            c = out.setdefault(r["ref"], {"cny": 0.0, "submits": 0})
            c["cny"] = round(c["cny"] + float(r["estimate_cny"]), 4)
            c["submits"] += 1
    return out


def cmd_assemble(a):
    project = Path(a.project)
    briefs = sorted((project / "briefs").glob("*.json"))
    all_briefs = [_read(bp) for bp in briefs]
    parents = {b["id"]: (b.get("retry_of") or b.get("parent")) for b in all_briefs}
    costs = take_costs(project.name)
    takes_by_brief = []
    for b in all_briefs:
        takes = []
        for t in b.get("takes", [b["id"]]):
            tid = t if isinstance(t, str) else t["id"]
            rp = project / "reviews" / f"{tid}.json"
            vp = project / "takes" / f"{tid}.mp4"
            rec = _read(rp) if rp.exists() else None
            # 三专家记录（review_panel 写入）：取 experts 列表；旧版单评审是 dict 或 list，原样兼容
            if isinstance(rec, dict) and "experts" in rec:
                reviews = rec["experts"]
            elif isinstance(rec, dict):
                reviews = [rec]
            else:
                reviews = rec or []
            cost = costs.get(tid)
            if b.get("segments"):
                segment_costs = [costs.get(f"{tid}-s{i}") for i in range(1, len(b["segments"]) + 1)]
                paid = [c for c in segment_costs if c]
                if paid:
                    cost = {"cny": round(sum(c["cny"] for c in paid), 4),
                            "submits": sum(c["submits"] for c in paid)}
            dur = brief_duration(b) if b.get("segments") else float(b.get("duration", settings.get_float("VIDEO_DURATION")))
            takes.append({"id": tid, "attempt": b.get("attempt", 1), "note": b.get("summary", "")[:120],
                          "video": f"takes/{tid}.mp4" if vp.exists() else None,
                          "series": series_root(b["id"], parents), "duration": dur,
                          # 花费优先取账本；账本没有记录时按 时长 × 单价 估算，并标明是估算
                          "cost_cny": cost["cny"] if cost else (round(dur * settings.video_price(), 2) if vp.exists() else 0.0),
                          "cost_estimated": cost is None, "submits": cost["submits"] if cost else None,
                          "machine_blocking": (rec.get("machine") or {}).get("blocking", []) if isinstance(rec, dict) else [],
                          "prompt": (b.get("prompt") or "")[:600],
                          "reviews": reviews})
        takes_by_brief.append({"id": b["id"], "title": b.get("title", b["id"]), "takes": takes,
                               "note": b.get("summary", "")})
    _write(project / "takes.json", {"briefs": takes_by_brief})
    print(f"takes.json 已生成：{len(takes_by_brief)} 个简报")


ROOT = settings.workspace_root()  # 调研批次等相对路径的基准（数据目录的上一级）
BOARD_RESEARCH = settings.get("BOARD_RESEARCH")  # 所有项目的看板默认用同一调研批次（.env 可改）


def cmd_board(a):
    """统一看板：每个项目都是同一结构（调研 + 生成 + 同一副标题），只差数据。
    调研批次默认 BOARD_RESEARCH，可在 project.json 里写 research_dir 覆盖；输出默认 <项目>/board.html。"""
    project = Path(a.project)
    meta = _read(project / "project.json") if (project / "project.json").exists() else {}
    rel = a.research or meta.get("research_dir") or BOARD_RESEARCH
    data = {"subtitle": "调研 → 生成 → 评审 → 闸门（流程见 references/architecture.md）"}
    if rel:
        data["research"] = research.build(ROOT / rel)
    if (project / "takes.json").exists():
        data["production"] = production.build(project / "takes.json")
    title = a.title or f"生成看板 · {meta.get('id', project.name)}"
    out = Path(a.out) if a.out else project / "board.html"
    out.write_text(dashboard.render(data, title), encoding="utf-8")
    print("看板 →", out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("breakdown")
    b.add_argument("--batch", required=True)
    b.add_argument("--ids", required=True)
    b.add_argument("--cap", type=int, default=3)
    g = sub.add_parser("gen")
    g.add_argument("--project", required=True)
    g.add_argument("--brief", required=True)
    g.add_argument("--cap", type=int, default=3)
    g.add_argument("--interval", type=int, default=20)
    g.add_argument("--max-polls", type=int, default=60)
    g.add_argument("--no-review", action="store_true", help="保存成片后跳过评审，并把原因写入任务记录")
    r = sub.add_parser("review")
    r.add_argument("--project", required=True)
    r.add_argument("--take", required=True)
    r.add_argument("--refs", required=True)
    r.add_argument("--judges", type=int, default=2)
    s = sub.add_parser("assemble")
    s.add_argument("--project", required=True)
    bd = sub.add_parser("board")
    bd.add_argument("--project", required=True)
    bd.add_argument("--research", help="调研批次目录（相对仓库根）；默认 BOARD_RESEARCH 或 project.json 的 research_dir")
    bd.add_argument("--out", help="输出路径；默认 <项目>/board.html")
    bd.add_argument("--title", help="默认 '生成看板 · <项目id>'")
    a = ap.parse_args(argv)
    {"breakdown": cmd_breakdown, "gen": cmd_gen, "review": cmd_review,
     "assemble": cmd_assemble, "board": cmd_board}[a.cmd](a)


if __name__ == "__main__":
    main()
