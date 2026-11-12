"""手动对标：用户直接给视频链接或本地视频文件，不必先跑调研。

  add-url    --product <品> --urls <链接,链接> [--relation same|similar|different]
  add-file   --product <品> --files <路径,路径> [--relation same|similar|different]
  breakdown  --product <品> [--ids M001,M002]      拆解（链接：模型直接看视频；文件：本地抽帧后看图）
  tiers      --product <品>                         列出每条对标的置信度与理由
  plan       --product <品> --n 10 (--pick M001,M002 | --tiers H,M | --all)   生成前选择复刻范围

设计
- 手动对标存成一个普通的调研批次（<RESEARCH_ROOT>/manual-<品>/），后面的拆解、定档、看板都走同一套代码。
- relation 是用户对"对标产品与我方产品关系"的说明：same 同款/同类 → 可到 H；similar 同类不同形态 → 最高 L；
  different 不同品类、只借展示方式 → 类比，最高 L。记在 verify.json 里，source 标为 user。
- 本地文件不上传到任何公开地址。拆解时只在本机抽帧，把帧图交给文本模型；听不到声音，记录里会注明。
- plan 只决定"做哪些、各几条"，不花钱。真正出简报与生成仍走 fission / own_script / draft / refine_loop。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import settings  # noqa: E402

RELATIONS = ("same", "similar", "different")


def batch_dir(pid):
    return settings.workspace_root() / settings.get("RESEARCH_ROOT") / f"manual-{pid}"


def _read(p, default):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_id(cands):
    nums = [int(c["candidate_id"][1:]) for c in cands if re.fullmatch(r"M\d+", c.get("candidate_id", ""))]
    return f"M{(max(nums) if nums else 0) + 1:03d}"


def verify_row(cand, relation):
    """用户说明的产品关系 → 与模型验证同结构的一行，供 replication.classify 使用。"""
    if relation not in RELATIONS:
        raise SystemExit(f"relation 必须是 {RELATIONS}")
    verdict = "A" if relation == "same" else "Reject"
    return {"aweme_id": cand["aweme_id"], "candidate_id": cand["candidate_id"], "verdict": verdict,
            "product_relation": relation, "reason": "用户手动提供的对标，产品关系由用户说明", "source": "user"}


def register(pid, cand, relation):
    """把一条手动对标写进批次（candidates.json + verify.json）。同一视频重复添加会被跳过。"""
    b = batch_dir(pid)
    cands = _read(b / "candidates.json", [])
    if any(c["aweme_id"] == cand["aweme_id"] for c in cands):
        return next(c for c in cands if c["aweme_id"] == cand["aweme_id"]), False
    cand = dict(cand, candidate_id=_next_id(cands), source="manual", sources=["manual"], hard_pass=True,
                final_pass=True, topic_pass=True, topic_level="analogy" if relation == "different" else "strong",
                relation=relation)
    cands.append(cand)
    _write(b / "candidates.json", cands)
    ver = [v for v in _read(b / "verify.json", []) if v["aweme_id"] != cand["aweme_id"]]
    ver.append(verify_row(cand, relation))
    _write(b / "verify.json", ver)
    return cand, True


def file_candidate(path):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"找不到文件：{p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"aweme_id": "file-" + h.hexdigest()[:12], "url": "", "local_file": str(p), "desc": p.name,
            "author": "", "play_urls": [], "cover_url": "", "plays": None, "likes": None, "comments": None,
            "shares": None, "saves": None, "author_followers": None, "duration_s": None}


def fetch_url_candidate(url, client):
    """用 TikHub 按分享链接取一条视频（1 次请求）。返回与调研相同结构的候选。"""
    import tt_research as tt
    status, js = client.call("GET", "/api/v1/tiktok/app/v3/fetch_one_video_by_share_url", params={"share_url": url})
    if status != 200:
        raise SystemExit(f"取视频失败（HTTP {status}）：{url}")
    data = js.get("data") or {}
    aweme = data.get("aweme_detail") or (data.get("aweme_details") or [None])[0] or (tt.extract_awemes(js) or [None])[0]
    if not aweme:
        raise SystemExit(f"返回里没有视频数据：{url}")
    c = tt.normalize(aweme, "manual")
    c["url"] = c.get("url") or url
    return c


# ---------------------------------------------------------------- 拆解

FRAMES_NOTE = ("\n\n注意：下面不是视频，是按时间顺序的抽帧（时间见列表）。你听不到声音。"
               "口播、音效相关的内容无法判断时写 0，并在 risk 里写明\"只看了抽帧，未听声音\"。\n抽帧时间：")


def breakdown_file(cand, pid, n=12):
    """本地文件：抽帧 → 文本模型看图拆解。返回与视频拆解同结构的记录。"""
    import loop
    import review_panel
    video = Path(cand["local_file"])
    frames = loop._frames(video, batch_dir(pid) / "frames" / cand["candidate_id"], n=n)
    import subprocess
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "default=nw=1:nk=1", str(video)]).decode().strip())
    parts = [{"type": "text", "text": loop.BREAKDOWN_PROMPT + FRAMES_NOTE + review_panel.frame_times(len(frames), dur)}]
    parts += [{"type": "image_url", "image_url": {"url": loop._data_uri(p)}} for p in frames]
    text = loop.gemini(parts, project=batch_dir(pid).name, ref=cand["candidate_id"], max_tokens=4000, role="breakdown")
    obj = loop.tt.parse_json_obj(text)
    if obj is None:
        return None
    obj.update(aweme_id=cand["aweme_id"], candidate_id=cand["candidate_id"], breakdown_at=loop._now(),
               from_frames=True, duration_s=round(dur, 1))
    return obj


def cmd_breakdown(pid, ids=None):
    import loop
    b = batch_dir(pid)
    cands = _read(b / "candidates.json", [])
    done = {r["aweme_id"]: r for r in _read(b / "breakdown.json", [])}
    todo = [c for c in cands if (not ids or c["candidate_id"] in ids) and c["aweme_id"] not in done]
    url_ids = [c["candidate_id"] for c in todo if not c.get("local_file")]
    for c in [c for c in todo if c.get("local_file")]:
        obj = breakdown_file(c, pid)
        if obj is None:
            print(f"{c['candidate_id']} 模型输出无法解析，已记账")
            continue
        done[c["aweme_id"]] = obj
        _write(b / "breakdown.json", list(done.values()))
        print(f"{c['candidate_id']} 拆解完成（抽帧）：" + " ".join(f"{k}{v}" for k, v in obj.get("dims", {}).items()))
    for i in range(0, len(url_ids), 3):  # loop.cmd_breakdown 每次最多 3 条
        loop.cmd_breakdown(argparse.Namespace(batch=str(b), ids=",".join(url_ids[i:i + 3]), cap=3))


# ---------------------------------------------------------------- 定档与计划

def cases(pid):
    import research
    return research.build(batch_dir(pid))["cases"]


def make_plan(case_list, n, pick=None, tiers=None, use_all=False, max_n=None, price=None, duration=None):
    """生成前的选择：pick = 指定对标；tiers = 指定档位；use_all = 全部。返回计划 dict，不花钱。
    按档位权重分配总条数，再在同档的对标之间平均分；X 档走自写脚本，不依赖对标。"""
    import fission
    max_n = max_n or fission.MAX_N
    price = settings.video_price() if price is None else price
    duration = settings.get_float("VIDEO_DURATION") if duration is None else duration
    if sum(bool(x) for x in (pick, tiers, use_all)) != 1:
        raise SystemExit("--pick、--tiers、--all 必须三选一")
    chosen = list(case_list)
    if pick:
        missing = [p for p in pick if p not in [c["id"] for c in case_list]]
        if missing:
            raise SystemExit(f"没有这些对标：{missing}")
        chosen = [c for c in case_list if c["id"] in pick]
    elif tiers:
        chosen = [c for c in case_list if c.get("tier") in tiers]
    by_tier = {}
    for c in chosen:
        by_tier.setdefault(c.get("tier") or "X", []).append(c)
    weights = {t: w for t, w in fission.TIER_WEIGHTS.items() if (tiers is None or t in tiers)}
    if pick:  # 点名时只在被点名的档位里分配
        weights = {t: w for t, w in weights.items() if t in by_tier}
    available = {t: len(by_tier.get(t, [])) for t in ("H", "M", "L")}
    if "X" in weights and not use_all and not (tiers and "X" in tiers) and not (pick and "X" in by_tier):
        weights.pop("X")
    if not weights:
        raise SystemExit("选择范围内没有可用的档位")
    alloc, gap = fission.allocate(n, available, weights)
    items = []
    for t in ("H", "M", "L"):
        group, total = by_tier.get(t, []), alloc.get(t, 0)
        for i, c in enumerate(group):
            cnt = min(max_n, total // len(group) + (1 if i < total % len(group) else 0))
            if cnt:
                items.append({"id": c["id"], "tier": t, "count": cnt, "route": "replicate", "title": (c.get("title") or "")[:40]})
    if alloc.get("X"):
        src = [c["id"] for c in by_tier.get("X", [])]
        items.append({"id": "X", "tier": "X", "count": alloc["X"], "route": "own_script",
                      "title": "自写脚本（不参考对标）", "unbroken_or_x_benchmarks": src})
    total = sum(i["count"] for i in items)
    return {"n_requested": n, "n_planned": total, "gap": n - total,
            "mode": "pick" if pick else ("tiers" if tiers else "all"),
            "allocation": {k: v for k, v in alloc.items() if v}, "items": items,
            "estimate_cny": round(total * duration * price, 2),
            "note": "没有拆解的对标会显示为 X。X 只表示缺数据或不满足复刻条件，先拆解再定档。"}


def quote_rounds(n, duration=None, price=None, rounds=(1, 2, 3), pass_rate=0.0):
    """多轮报价。rounds 表示每条最多生成几轮（首次 + 修订）。
    最坏情况 = 每条都跑满这么多轮；期望 = 每轮后通过的比例 pass_rate 不再修订。
    返回每个轮数的 {最坏元, 期望元, 单条成品期望元}。不花钱。"""
    duration = settings.get_float("VIDEO_DURATION") if duration is None else duration
    price = settings.video_price() if price is None else price
    per = duration * price
    out = {}
    for r in rounds:
        worst = n * r * per
        # 期望：第 k 轮只有未通过的继续；通过率按固定 pass_rate 估算
        exp, alive = 0.0, float(n)
        for _ in range(r):
            exp += alive * per
            alive *= (1 - pass_rate)
        good = n * (1 - (1 - pass_rate) ** r)
        out[r] = {"worst_cny": round(worst, 2), "expected_cny": round(exp, 2),
                  "expected_good": round(good, 2),
                  "per_good_cny": round(exp / good, 2) if good else None}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="手动对标：链接或本地文件 → 拆解 → 定档 → 生成计划")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add-url", "add-file"):
        p = sub.add_parser(name)
        p.add_argument("--product", required=True)
        p.add_argument("--urls" if name == "add-url" else "--files", required=True, help="逗号分隔")
        p.add_argument("--relation", default="same", choices=RELATIONS)
    b = sub.add_parser("breakdown")
    b.add_argument("--product", required=True)
    b.add_argument("--ids")
    t = sub.add_parser("tiers")
    t.add_argument("--product", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--product", required=True)
    pl.add_argument("--n", type=int, required=True)
    pl.add_argument("--pick", help="指定对标，逗号分隔")
    pl.add_argument("--tiers", help="指定档位，逗号分隔，如 H,M")
    pl.add_argument("--all", action="store_true")
    pl.add_argument("--pass-rate", type=float, default=0.3, help="估算的单轮通过率，默认 0.3（只用于期望值）")
    a = ap.parse_args(argv)
    pid = a.product
    if a.cmd == "add-file":
        for f in [x.strip() for x in a.files.split(",") if x.strip()]:
            c, new = register(pid, file_candidate(f), a.relation)
            print(f"{c['candidate_id']} {'已添加' if new else '已存在'}：{Path(f).name}（{a.relation}）")
    elif a.cmd == "add-url":
        import tt_research as tt
        client = tt.Client(tt.load_key(), batch_dir(pid), budget_calls=settings.get_int("LIMIT_TIKHUB_CALLS"))
        for u in [x.strip() for x in a.urls.split(",") if x.strip()]:
            c, new = register(pid, fetch_url_candidate(u, client), a.relation)
            print(f"{c['candidate_id']} {'已添加' if new else '已存在'}：{u}（{a.relation}）")
    elif a.cmd == "breakdown":
        cmd_breakdown(pid, [x.strip() for x in a.ids.split(",")] if a.ids else None)
    elif a.cmd == "tiers":
        for c in cases(pid):
            print(f"{c['id']}  {c.get('tier')}  {'；'.join(c.get('tier_reasons') or [])}  | {(c.get('title') or '')[:40]}")
    elif a.cmd == "plan":
        plan = make_plan(cases(pid), a.n, pick=[x.strip() for x in a.pick.split(",")] if a.pick else None,
                         tiers=[x.strip() for x in a.tiers.split(",")] if a.tiers else None, use_all=a.all)
        import products
        plan["quote_by_rounds"] = quote_rounds(a.n, pass_rate=a.pass_rate)
        plan["questions_for_creator"] = [
            "每条视频最多做几轮（首次 + 修订）？",
            "这一个品的预算上限是多少？（默认 5 元）",
            "想要几条成品？多出的条数会在报价里列出费用。",
        ]
        _write(products.product_dir(pid) / "plan.json", plan)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
