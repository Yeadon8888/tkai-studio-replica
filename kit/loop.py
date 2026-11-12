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
- 超出 GATES.md 的上限会直接拒绝（由调用方传入 --cap）。
"""
import argparse
import base64
import hashlib
import json
import re
import os
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
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": settings.get("HTTP_USER_AGENT")})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            js = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        ledger.append(settings.get("TEXT_PROVIDER"), model, e.code, project=project, ref=ref, units=1, kind="text")
        raise
    text = js["choices"][0]["message"]["content"] or ""
    ledger.append(settings.get("TEXT_PROVIDER"), model, "ok", project=project, ref=ref, units=1, usage=js.get("usage") or {}, kind="text")
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
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": settings.get("HTTP_USER_AGENT")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def cmd_gen(a):
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
        body = {"model": GEN_MODEL, "prompt": brief["prompt"], "images": images,
                "duration": brief.get("duration", 10), "resolution": brief.get("resolution", "768p"),
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
        ledger.append("tokensfactory", GEN_MODEL, "submitted", project=project.name, ref=tid, units=brief.get("duration", 10))
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
            dur = float(b.get("duration", settings.get_float("VIDEO_DURATION")))
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
    data = {"subtitle": "调研 → 生成 → 评审 → 闸门（规则见 GATES.md）"}
    if rel:
        data["research"] = research.build(ROOT / rel)
    if (project / "takes.json").exists():
        data["production"] = production.build(project / "takes.json")
    title = a.title or f"生成看板 · {meta.get('id', project.name)}"
    out = Path(a.out) if a.out else project / "board.html"
    out.write_text(dashboard.render(data, title), encoding="utf-8")
    print("看板 →", out)


# ---------------------------------------------------------------- 5. 人工勾选 → 制作

def cmd_select(a):
    """读取看板导出的 selection.json，逐条用闸门核对后写入项目。不可进入制作的条目需要 --override 并写明理由。"""
    project = Path(a.project)
    sel = _read(a.file).get("selected", [])
    cases = {c["id"]: c for c in research.build(a.batch)["cases"]}
    ok, rejected = [], []
    for cid in sel:
        c = cases.get(cid)
        if c is None:
            rejected.append({"id": cid, "reason": "不在本批次的硬筛候选中"})
        elif c["eligible"]:
            ok.append(cid)
        elif a.override:
            ok.append(cid)
            rejected.append({"id": cid, "reason": "人工覆盖：" + "；".join(c["block_reasons"]), "overridden": True})
        else:
            rejected.append({"id": cid, "reason": "；".join(c["block_reasons"])})
    _write(project / "selection.json", {"selected": ok, "rejected": rejected,
                                        "override_reason": a.override or None, "selected_at": _now()})
    print(f"通过 {len(ok)} 条：{', '.join(ok) or '无'}")
    for r in rejected:
        print(f"  未进入制作 {r['id']}：{r['reason']}")


def cmd_produce(a):
    """对 selection.json 中的每条：要求已有简报 → 生成（自动评审）→ 汇总。"""
    project = Path(a.project)
    sel = _read(project / "selection.json")["selected"]
    if not sel:
        sys.exit("selection.json 里没有可制作的条目，停止。")
    for cid in sel:
        brief = project / "briefs" / f"{cid}.json"
        if not brief.exists():
            print(f"{cid} 缺少简报 briefs/{cid}.json，跳过")
            continue
        ns = argparse.Namespace(project=str(project), brief=str(brief), cap=a.cap, interval=a.interval,
                                max_polls=a.max_polls, no_review=False)
        cmd_gen(ns)
    cmd_assemble(argparse.Namespace(project=str(project)))


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
    sl = sub.add_parser("select")
    sl.add_argument("--project", required=True)
    sl.add_argument("--batch", required=True)
    sl.add_argument("--file", required=True)
    sl.add_argument("--override", help="不符合闸门的条目仍要制作时，必须写明理由")
    pr = sub.add_parser("produce")
    pr.add_argument("--project", required=True)
    pr.add_argument("--cap", type=int, default=3)
    pr.add_argument("--interval", type=int, default=20)
    pr.add_argument("--max-polls", type=int, default=60)
    a = ap.parse_args(argv)
    {"breakdown": cmd_breakdown, "gen": cmd_gen, "review": cmd_review,
     "assemble": cmd_assemble, "board": cmd_board, "select": cmd_select, "produce": cmd_produce}[a.cmd](a)


if __name__ == "__main__":
    main()
