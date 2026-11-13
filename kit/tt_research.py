#!/usr/bin/env python3
"""TikTok 对标视频调研脚本（TikHub 数据源）。

子命令：
  trends    热门标签榜单（Creative Center）
  search    关键词视频搜索
  hashtag   话题下的视频
  account   垂直达人/卖家账号的作品（需 sec_uid）
  filter    去重、主题门控、硬筛、爆款类型、自动分，输出候选清单与 creators.json
  intake    校验产品任务卡 product_card.json（缺项即继续追问）
  verify    TokensFactory gemini-3.8-flash 验证入选视频（只传公开视频链接，不下载视频）
  report    生成可视化 HTML 报告（report.html，单文件：封面 + 跳转链接 + 验证结论）

原则：
  - 脚本负责采集、确定性规则和可视化；产品匹配的最终判断、复刻风险由 AI 按 references/rules.md 完成。
  - 硬筛阈值（时长、30 天、播放、点赞）写死，不提供放宽开关。
  - 计费请求全部写入 calls.jsonl；每次运行有调用上限；失败不自动重试。
  - API Key 只从环境变量或 env 文件读取，不打印。
仅依赖标准库。本脚本不下载视频文件，只保存封面图地址与视频跳转链接。
"""
import argparse
import base64
import csv
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as studio_ledger  # noqa: E402  统一账本

import settings  # noqa: E402  读 .env，并把密钥放进进程环境

settings.load()
BASE = settings.get("TIKHUB_BASE_URL")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 硬筛阈值（手册 6.1，禁止在代码外放宽）
MAX_DURATION_S = 40
OPTIMAL_DURATION_S = (10, 15)
MAX_AGE_DAYS = 30
MIN_PLAYS = 1500      # 必须大于
MIN_LIKES = 100       # 必须大于

# 爆款类型阈值（可在 config.json 覆盖，不影响硬筛）
OUTLIER_DEFAULTS = {
    "absolute_plays": 1_000_000,   # 绝对爆款
    "low_follower_max": 50_000,    # 低粉：粉丝不超过此数
    "low_follower_ratio": 5.0,     # 低粉爆款：播放 / 粉丝 ≥ 此倍数
    "high_plays": 100_000,         # 高播放
}

HASHTAG_REGIONS = {"US", "GB", "FR", "JP", "VN", "SG"}

# 密钥文件位置在 .env 的 CREDENTIALS_FILES 里配置；这里只保留一个通用的兜底位置。
DEFAULT_ENV_FILES = ["~/.config/tikhub/credentials.env"]
CST = dt.timezone(dt.timedelta(hours=8))


# ---------------------------------------------------------------- 基础设施

def load_key(explicit_env_file=None):
    if os.environ.get("TIKHUB_API_KEY"):
        return os.environ["TIKHUB_API_KEY"].strip()
    candidates = [explicit_env_file] if explicit_env_file else DEFAULT_ENV_FILES
    for f in candidates:
        if not f:
            continue
        p = Path(os.path.expanduser(f))
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("TIKHUB_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    sys.exit("找不到 TIKHUB_API_KEY：请设置环境变量，或用 --env-file 指定包含该变量的文件。")


class Client:
    def __init__(self, key, out_dir, budget_calls, dry_run=False):
        self.key = key
        self.out = Path(out_dir)
        (self.out / "raw").mkdir(parents=True, exist_ok=True)
        import limits  # 用户约定：单次查询不超过 100 次
        self.budget = min(int(budget_calls), limits.TIKHUB_MAX_CALLS)
        self.used = 0
        self.dry_run = dry_run
        self.log = self.out / "calls.jsonl"

    def _record(self, endpoint, params, status, n_items):
        row = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "endpoint": endpoint, "params": params, "status": status, "items": n_items}
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        studio_ledger.append("tikhub", endpoint, status, project=self.out.name, units=1)

    def call(self, method, path, params=None, body=None):
        if self.used >= self.budget:
            sys.exit(f"已达到本次运行调用上限 {self.budget}，停止。确需更多请显式提高 --budget-calls。")
        self.used += 1
        url = BASE + path
        data = None
        headers = {"Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": UA}
        if method == "GET" and params:
            url += "?" + urllib.parse.urlencode(params)
        if method == "POST":
            data = json.dumps(body or {}).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                js = json.loads(r.read().decode("utf-8", "replace"))
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
            js = {"error_text": e.read().decode("utf-8", "replace")[:300]}
        except urllib.error.URLError as e:
            status = "network_error"
            js = {"error_text": str(e.reason)[:300]}
        n = None
        if status == 200 and isinstance(js, dict):
            n = len(extract_awemes(js)) or len((js.get("data") or {}).get("items") or [])
        self._record(path, params or body, status, n)
        return status, js


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def count_calls(out):
    log = Path(out) / "calls.jsonl"
    return sum(1 for _ in log.open(encoding="utf-8")) if log.exists() else 0


# ---------------------------------------------------------------- 解析与规则

def extract_awemes(js):
    """兼容 search（search_item_list[].aweme_info）与 hashtag / account（aweme_list[]）。"""
    data = js.get("data") or {}
    out = []
    for it in data.get("search_item_list") or []:
        if isinstance(it, dict) and isinstance(it.get("aweme_info"), dict):
            out.append(it["aweme_info"])
    for a in data.get("aweme_list") or []:
        if isinstance(a, dict):
            out.append(a.get("aweme_info") if isinstance(a.get("aweme_info"), dict) else a)
    return [a for a in out if a.get("aweme_id")]


def page_state(js):
    data = js.get("data") or {}
    return bool(data.get("has_more")), data.get("cursor")


def normalize(aweme, source):
    stats = aweme.get("statistics") or {}
    author = aweme.get("author") or {}
    video = aweme.get("video") or {}
    ct = int(aweme.get("create_time") or 0)
    duration_s = round((video.get("duration") or 0) / 1000, 1)
    age_days = round((time.time() - ct) / 86400, 1) if ct else None
    tags = [t.get("hashtag_name") for t in (aweme.get("text_extra") or []) if t.get("hashtag_name")]
    aweme_id = str(aweme.get("aweme_id"))
    uid = author.get("unique_id") or ""
    url = aweme.get("share_url") or (f"https://www.tiktok.com/@{uid}/video/{aweme_id}" if uid else "")
    play_urls = ((video.get("play_addr") or {}).get("url_list")) or []
    return {
        "aweme_id": aweme_id,
        "url": url.split("?")[0] if url else "",
        "author": uid,
        "author_nickname": author.get("nickname", ""),
        "author_sec_uid": author.get("sec_uid", ""),
        "author_followers": author.get("follower_count") or 0,
        "desc": (aweme.get("desc") or "").strip(),
        "create_time": ct,
        "published_at_cst": dt.datetime.fromtimestamp(ct, CST).strftime("%Y-%m-%d %H:%M") if ct else "",
        "age_days": age_days,
        "duration_s": duration_s,
        "plays": stats.get("play_count") or 0,
        "likes": stats.get("digg_count") or 0,
        "comments": stats.get("comment_count") or 0,
        "shares": stats.get("share_count") or 0,
        "saves": stats.get("collect_count") or 0,
        "hashtags": tags,
        "music": (aweme.get("music") or {}).get("title", ""),
        "is_ads": bool(aweme.get("is_ads")),
        "play_urls": play_urls[:3],
        "cover_url": ((video.get("cover") or {}).get("url_list") or [""])[0],
        "source": source,
    }


def check_hard(c):
    reasons = []
    if not c["duration_s"] or c["duration_s"] > MAX_DURATION_S:
        reasons.append(f"时长>{MAX_DURATION_S}s或缺失")
    if c["age_days"] is None or c["age_days"] > MAX_AGE_DAYS:
        reasons.append(f"发布超过{MAX_AGE_DAYS}天或缺失")
    if not c["plays"] or c["plays"] <= MIN_PLAYS:
        reasons.append(f"播放量≤{MIN_PLAYS}")
    if not c["likes"] or c["likes"] <= MIN_LIKES:
        reasons.append(f"点赞≤{MIN_LIKES}")
    return (not reasons), reasons


def dup_key(c):
    text = re.sub(r"#\S+", "", c["desc"].lower())
    text = re.sub(r"[^a-z0-9一-鿿]+", "", text)
    if len(text) < 15:
        return None
    return (text[:60], round(c["duration_s"]))


def norm_text(s):
    return re.sub(r"[^a-z0-9一-鿿]+", "", (s or "").lower())


def load_config(cfg_path):
    """返回 (主题门控, 爆款阈值)。config.json 缺失时主题门控为 None。"""
    outlier = dict(OUTLIER_DEFAULTS)
    if not cfg_path or not Path(cfg_path).is_file():
        return None, outlier
    cfg = read_json(cfg_path)
    outlier.update(cfg.get("outlier", {}))

    def terms(key):
        return [norm_text(t) for t in cfg.get(key, []) if norm_text(t)]
    topic = {"strong": terms("strong_terms") + terms("core_terms"),
             "weak": terms("weak_terms"),
             "analogy": terms("analogy_terms"),   # 类比词：同类展示语法的其他品类，只用于低置信复刻
             "exclude": terms("exclude_terms")}
    return topic, outlier


def check_topic(c, topic):
    """主题门控：只看文案和话题标签。写入 c['topic_level'] 并返回 (是否入选, 说明列表)。"""
    if topic is None:
        c["topic_level"] = "strong"
        return True, ["未配置主题词"]
    text = norm_text(c["desc"] + " " + " ".join(c["hashtags"]))
    bad = [t for t in topic["exclude"] if t in text]
    if bad:
        c["topic_level"] = "none"
        return False, ["排除词:" + "/".join(sorted(set(bad)))]
    strong = [t for t in topic["strong"] if t in text]
    if strong:
        c["topic_level"] = "strong"
        return True, sorted(set(strong))
    weak = [t for t in topic["weak"] if t in text]
    if weak:
        c["topic_level"] = "weak"
        return False, ["仅弱相关词，待人工复核:" + "/".join(sorted(set(weak)))]
    analogy = [t for t in topic.get("analogy", []) if t in text]
    if analogy:
        c["topic_level"] = "analogy"
        return False, ["类比品类（只用于低置信复刻）:" + "/".join(sorted(set(analogy)))]
    c["topic_level"] = "none"
    return False, ["文案/标签无核心词"]


def viral_type(c, o):
    followers = c["author_followers"]
    ratio = c["plays"] / followers if followers else 0
    c["play_per_follower"] = round(ratio, 2) if followers else None
    c["like_rate"] = round(c["likes"] / c["plays"], 4) if c["plays"] else 0
    if c["plays"] >= o["absolute_plays"]:
        return "绝对爆款"
    if followers and followers <= o["low_follower_max"] and ratio >= o["low_follower_ratio"]:
        return "低粉爆款"
    if c["plays"] >= o["high_plays"]:
        return "高播放"
    return "常规"


def auto_score(c):
    """自动可算部分，满分 60：产品匹配 30 + 数据与新鲜度 20 + 来源 10。复刻 25 与内容 15 由 AI 补充。"""
    level = c.get("topic_level")
    match = {"strong": 30, "weak": 10}.get(level, 0)
    data = 0
    if c["age_days"] is not None:
        data += 8 if c["age_days"] <= 14 else (5 if c["age_days"] <= 30 else 0)
    if OPTIMAL_DURATION_S[0] <= c["duration_s"] <= OPTIMAL_DURATION_S[1]:
        data += 6
    elif c["duration_s"] and c["duration_s"] <= MAX_DURATION_S:
        data += 3
    if c["plays"] >= 100_000:
        data += 4
    elif c["plays"] >= 10_000:
        data += 2
    if c.get("viral_type") == "低粉爆款":
        data += 4
    data = min(data, 20)
    source = 10 if str(c["source"]).startswith("account:") else 5
    return match + data + source


# ---------------------------------------------------------------- 采集子命令

def cmd_trends(a, client):
    if a.dry_run:
        print(f"[dry-run] trends country={a.country} time_range={a.time_range} pages={a.pages} limit={a.limit}")
        return
    items = []
    for page in range(1, a.pages + 1):
        body = {"time_range": a.time_range, "country_code": a.country, "page": page, "limit": a.limit}
        if a.industry_id:
            body["industry_id"] = a.industry_id
        status, js = client.call("POST", "/api/v1/tiktok/ads/get_trends_hashtag_list", body=body)
        write_json(client.out / "raw" / f"trends_p{page}.json", js)
        if status != 200:
            print(f"trends 第{page}页失败：HTTP {status}", file=sys.stderr)
            break
        data = js.get("data") or {}
        for r in data.get("items") or []:
            items.append({"hashtag_id": str(r.get("hashtagID", "")), "name": r.get("hashtagName", ""),
                          "rank": r.get("rankIndex"), "publish_cnt": r.get("publishCnt"),
                          "views": r.get("vv"), "industries": r.get("industryIDs", []),
                          "country": a.country, "time_range_days": a.time_range})
        if not (data.get("pagination") or {}).get("hasMore"):
            break
    write_json(client.out / "trends.json", items)
    print(f"趋势标签 {len(items)} 个 → {client.out / 'trends.json'}")


def load_keywords(path):
    p = Path(path)
    if p.suffix.lower() == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
        kws = [(k if isinstance(k, str) else k.get("keyword", "")) for k in raw]
    else:
        kws = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return [k for k in kws if k]


def cmd_search(a, client):
    kws = load_keywords(a.keywords)
    if a.dry_run:
        print(f"[dry-run] search keywords={len(kws)} pages={a.pages} count={a.count} "
              f"publish_time={a.publish_time} sort={a.sort} region={a.region} 最多 {len(kws) * a.pages} 次调用")
        return
    collected = []
    for ki, kw in enumerate(kws, 1):
        offset = 0
        for page in range(1, a.pages + 1):
            params = {"keyword": kw, "offset": offset, "count": a.count,
                      "sort_type": 1 if a.sort == "likes" else 0,
                      "publish_time": a.publish_time, "region": a.region}
            status, js = client.call("GET", "/api/v1/tiktok/app/v3/fetch_video_search_result", params=params)
            write_json(client.out / "raw" / f"search_{ki:02d}_p{page}.json", js)
            if status != 200:
                print(f"搜索「{kw}」第{page}页失败：HTTP {status}", file=sys.stderr)
                break
            awemes = extract_awemes(js)
            collected += [{"source": f"keyword:{kw}", "aweme": x} for x in awemes]
            print(f"搜索「{kw}」第{page}页：{len(awemes)} 条")
            more, nxt = page_state(js)
            if not more or nxt is None:
                break
            offset = nxt
    fname = f"items_search_{a.sort}.json"  # 按排序分文件，避免两种排序互相覆盖
    write_json(client.out / fname, collected)
    print(f"搜索合计 {len(collected)} 条（未去重）→ {fname}")


def cmd_hashtag(a, client):
    if a.region not in HASHTAG_REGIONS:
        sys.exit(f"话题视频接口地区仅支持 {sorted(HASHTAG_REGIONS)}，当前 {a.region}。MY、MX 请用 search。")
    targets = []
    if a.from_trends:
        targets = [(t["hashtag_id"], t["name"]) for t in read_json(a.from_trends)[: a.top]]
    targets += [(cid, cid) for cid in (a.ch_id or [])]
    if not targets:
        sys.exit("请用 --ch-id 指定话题 ID，或用 --from-trends 指定 trends.json。")
    if a.dry_run:
        print(f"[dry-run] hashtag targets={len(targets)} pages={a.pages} count={a.count} region={a.region}")
        return
    collected = []
    for cid, name in targets:
        cursor = 0
        for page in range(1, a.pages + 1):
            params = {"ch_id": cid, "cursor": cursor, "count": a.count, "region": a.region}
            status, js = client.call("GET", "/api/v1/tiktok/app/v3/fetch_hashtag_video_list", params=params)
            write_json(client.out / "raw" / f"hashtag_{cid}_p{page}.json", js)
            if status != 200:
                print(f"话题 {name} 第{page}页失败：HTTP {status}", file=sys.stderr)
                break
            awemes = extract_awemes(js)
            collected += [{"source": f"hashtag:{name}", "aweme": x} for x in awemes]
            print(f"话题 #{name}（{cid}）第{page}页：{len(awemes)} 条")
            more, nxt = page_state(js)
            if not more or nxt is None:
                break
            cursor = nxt
    write_json(client.out / "items_hashtag.json", collected)
    print(f"话题合计 {len(collected)} 条（未去重）→ items_hashtag.json")


def cmd_account(a, client):
    targets = []
    if a.from_creators:
        targets = read_json(a.from_creators)[: a.top]
    targets += [{"unique_id": s[:14], "sec_uid": s} for s in (a.sec_uid or [])]
    if not targets:
        sys.exit("请用 --from-creators（filter 生成的 creators.json）或 --sec-uid 指定账号。")
    if a.dry_run:
        print(f"[dry-run] account targets={len(targets)} pages={a.pages} count={a.count}")
        return
    collected = []
    for t in targets:
        cursor = 0
        for page in range(1, a.pages + 1):
            params = {"sec_user_id": t["sec_uid"], "max_cursor": cursor, "count": a.count, "sort_type": 0}
            status, js = client.call("GET", "/api/v1/tiktok/app/v3/fetch_user_post_videos_v3", params=params)
            write_json(client.out / "raw" / f"account_{t['unique_id']}_p{page}.json", js)
            if status != 200:
                print(f"账号 @{t['unique_id']} 第{page}页失败：HTTP {status}", file=sys.stderr)
                break
            awemes = extract_awemes(js)
            collected += [{"source": f"account:{t['unique_id']}", "aweme": x} for x in awemes]
            print(f"账号 @{t['unique_id']} 第{page}页：{len(awemes)} 条")
            data = js.get("data") or {}
            nxt = data.get("max_cursor", data.get("cursor"))
            if not data.get("has_more") or nxt is None:
                break
            cursor = nxt
    write_json(client.out / "items_account.json", collected)
    print(f"账号合计 {len(collected)} 条（未去重）→ items_account.json")


# ---------------------------------------------------------------- 筛选

def cmd_filter(a, client):
    out = client.out
    raw_items = []
    for f in sorted(out.glob("items_*.json")):
        raw_items.extend(read_json(f))
    if not raw_items:
        sys.exit(f"{out} 下没有 items_*.json，请先运行 search / hashtag / account。")

    by_id = {}
    for it in raw_items:
        c = normalize(it["aweme"], it["source"])
        if c["aweme_id"] in by_id:
            by_id[c["aweme_id"]]["sources"].append(c["source"])
            continue
        c["sources"] = [c["source"]]
        by_id[c["aweme_id"]] = c
    cands = list(by_id.values())

    topic, outlier = load_config(a.config or (out / "config.json"))

    for c in cands:
        c["hard_pass"], c["fail_reasons"] = check_hard(c)
        c["optimal_length"] = OPTIMAL_DURATION_S[0] <= c["duration_s"] <= OPTIMAL_DURATION_S[1]
        c["topic_pass"], c["topic_info"] = check_topic(c, topic)
        c["viral_type"] = viral_type(c, outlier) if c["hard_pass"] else "未过硬筛"
        c["auto_score"] = auto_score(c)

    # 搬运/重复：文案相同且时长一致归为一组，保留最早发布
    groups = {}
    for c in cands:
        k = dup_key(c)
        if k:
            groups.setdefault(k, []).append(c)
    dup_groups = 0
    for n, (_, members) in enumerate(sorted(groups.items(), key=lambda kv: kv[0]), 1):
        if len(members) < 2:
            continue
        dup_groups += 1
        members.sort(key=lambda c: c["create_time"] or 0)
        for i, c in enumerate(members):
            c["dup_group"] = f"DUP{n:03d}"
            c["dup_verdict"] = "保留(最早发布)" if i == 0 else "疑似搬运/重复-淘汰"
    for c in cands:
        c.setdefault("dup_group", "")
        c.setdefault("dup_verdict", "非重复")
        c["final_pass"] = bool(c["hard_pass"] and c["topic_pass"] and c["dup_verdict"] != "疑似搬运/重复-淘汰")

    cands.sort(key=lambda c: (not c["final_pass"], -c["auto_score"], -c["likes"]))
    for i, c in enumerate(cands, 1):
        c["candidate_id"] = f"C{i:03d}"

    final = [c for c in cands if c["final_pass"]]
    review = [c for c in cands if c["hard_pass"] and c.get("topic_level") == "weak"
              and c["dup_verdict"] != "疑似搬运/重复-淘汰"]

    creators = {}
    for c in final:
        d = creators.setdefault(c["author"], {"unique_id": c["author"], "sec_uid": c["author_sec_uid"],
                                              "nickname": c["author_nickname"], "followers": c["author_followers"],
                                              "final_videos": 0, "sample_url": c["url"]})
        d["final_videos"] += 1
    creator_list = [d for d in creators.values() if d["sec_uid"]]
    creator_list.sort(key=lambda d: (-d["final_videos"], -(d["followers"] or 0)))
    write_json(out / "creators.json", creator_list)

    # 封面地址刚拿到、还没过期：顺手存到本地（只存看板会展示的候选，不额外请求 TikHub）
    try:
        import covers
        covers.cache_batch(out, refresh=False, only_shown=True, cands=cands)
    except Exception as e:  # noqa: BLE001  封面失败不影响调研结果
        print(f"封面保存失败（不影响候选清单）：{type(e).__name__}")
    write_json(out / "candidates.json", cands)
    cols = ["candidate_id", "aweme_id", "url", "author", "author_followers", "viral_type", "play_per_follower",
            "sources", "desc", "published_at_cst", "age_days", "duration_s", "optimal_length", "plays", "likes",
            "comments", "shares", "saves", "like_rate", "hashtags", "music", "is_ads", "auto_score",
            "final_pass", "hard_pass", "fail_reasons", "topic_pass", "topic_level", "topic_info",
            "dup_group", "dup_verdict"]
    with (out / "candidates.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for c in cands:
            row = dict(c)
            row["sources"] = " | ".join(sorted(set(c["sources"])))
            row["hashtags"] = " ".join("#" + h for h in c["hashtags"])
            row["fail_reasons"] = "；".join(c["fail_reasons"])
            row["topic_info"] = "；".join(c["topic_info"]) if isinstance(c["topic_info"], list) else c["topic_info"]
            w.writerow({k: row.get(k, "") for k in cols})

    reason_count = {}
    for c in cands:
        for r in c["fail_reasons"]:
            reason_count[r] = reason_count.get(r, 0) + 1
    vt = {}
    for c in final:
        vt[c["viral_type"]] = vt.get(c["viral_type"], 0) + 1
    lines = [
        f"# 采集汇总 {out.name}",
        "",
        f"- 原始条目：{len(raw_items)}；去重后视频：{len(cands)}",
        f"- 硬筛通过：{sum(1 for c in cands if c['hard_pass'])}",
        f"- 主题相关（最终入选）：{len(final)}",
        f"- 仅弱相关、待人工复核：{len(review)}",
        f"- 重复/搬运组：{dup_groups}",
        f"- 垂直达人候选账号：{len(creator_list)}（creators.json）",
        f"- 入选视频的爆款类型：" + "、".join(f"{k} {v}" for k, v in vt.items()) if vt else "- 入选视频的爆款类型：无",
        f"- 累计计费请求（calls.jsonl）：{count_calls(out)}",
        "",
        "## 未通过硬筛原因统计（一条可多因）",
    ]
    lines += [f"- {k}：{v}" for k, v in sorted(reason_count.items(), key=lambda kv: -kv[1])] or ["- 无"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n候选清单 → {out / 'candidates.csv'}\n可视化 → 运行 report 子命令生成 report.html")


# ---------------------------------------------------------------- 下载与图片匹配

def esc(s):
    return html.escape(str(s if s is not None else ""))


def vt_class(v):
    return {"绝对爆款": "t-abs", "低粉爆款": "t-low", "高播放": "t-high"}.get(v, "")


def scatter_svg(cands):
    W, H, P = 620, 320, 44
    xs = lambda f: P + (math.log10(f + 1) / 7.0) * (W - P - 36)          # 粉丝 0..10^7
    ys = lambda p: H - P - ((math.log10(p + 1) - 3) / 6.0) * (H - P - 10)  # 播放 10^3..10^9
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="粉丝与播放散点">']
    for p in [1e3, 1e4, 1e5, 1e6, 1e7, 1e8]:
        y = ys(p)
        parts.append(f'<line x1="{P}" x2="{W-10}" y1="{y:.1f}" y2="{y:.1f}" stroke="currentColor" opacity=".12"/>')
        parts.append(f'<text x="4" y="{y+4:.1f}">{int(p):,}</text>')
    for f in [1e2, 1e3, 1e4, 1e5, 1e6, 1e7]:
        x = xs(f)
        parts.append(f'<text x="{x:.1f}" y="{H-P+16}" text-anchor="middle">{int(f):,}</text>')
    parts.append(f'<text x="{P}" y="14">播放量（对数）</text><text x="{W-150}" y="{H-6}">粉丝数（对数）</text>')
    # 低粉爆款参考线：播放 = 5 × 粉丝
    pts = []
    for c in cands:
        if c["plays"] <= 0:
            continue
        col = "var(--green)" if c["final_pass"] else ("var(--amber)" if c.get("topic_level") == "weak" else "var(--grey)")
        r = 5 if c["final_pass"] else 3.5
        tip = f'{c["candidate_id"]} @{c["author"]} 播放{c["plays"]:,} 粉丝{c["author_followers"]:,} {c["viral_type"]}'
        pts.append(f'<circle cx="{xs(c["author_followers"]):.1f}" cy="{ys(c["plays"]):.1f}" r="{r}" fill="{col}" opacity=".85">'
                   f'<title>{esc(tip)}</title></circle>')
    parts += pts
    parts.append("</svg>")
    return "".join(parts)


def funnel_html(stages):
    top = max(v for _, v in stages) or 1
    rows = []
    for label, v in stages:
        rows.append(f'<div class="row"><span>{esc(label)}</span><div class="bar"><i style="width:{v*100/top:.1f}%"></i></div>'
                    f'<b>{v}</b></div>')
    return "".join(rows)


# ---------------------------------------------------------------- 产品任务卡（intake）

CARD_FIELDS = {
    "product_name": "产品名（具体名称）",
    "category": "品类，如 pool water test strips",
    "use_case": "核心使用场景与人群",
    "market": "目标市场，如 US",
    "language": "视频语言，如 en",
    "selling_points": "可合法使用的卖点（列表）",
    "forbidden_claims": "禁止表达（列表；确认无禁忌时写 []）",
    "keywords": "搜索词（列表，至少 5 个）",
    "target_count": "目标入选数量（正整数）",
    "source": "输入来源：keyword 或 image",
    "confirmed_by_user": "用户已确认该任务卡（必须为 true）",
}
EMPTY_OK = {"forbidden_claims"}


def cmd_intake(a, client):
    path = Path(a.card) if a.card else client.out / "product_card.json"
    if not path.exists():
        sys.exit(f"缺少 {path}。agent 需先向用户追问，再写入任务卡。")
    card = read_json(path)
    bad = set()
    for k in CARD_FIELDS:
        v = card.get(k)
        if v is None or v == "" or (v == [] and k not in EMPTY_OK):
            bad.add(k)
    if isinstance(card.get("keywords"), list) and len(card["keywords"]) < 5:
        bad.add("keywords")
    if card.get("confirmed_by_user") is not True:
        bad.add("confirmed_by_user")
    if bad:
        print("任务卡未齐，agent 应继续追问：")
        for k in CARD_FIELDS:
            if k in bad:
                print(f"  - {k}：{CARD_FIELDS[k]}")
        sys.exit(1)
    print(f"任务卡齐全：{card['product_name']} / {card['market']} / 目标 {card['target_count']} 条 / 关键词 {len(card['keywords'])} 个")


# ---------------------------------------------------------------- AI 验证（TokensFactory，只传公开链接）

TF_DEFAULT_BASE = settings.get("TOKENSFACTORY_BASE_URL")
TF_UA = settings.get("HTTP_USER_AGENT")
VERIFY_MODEL = settings.model("verify")
VERIFY_PROMPT = """你在审核一条 TikTok 视频，判断它能否作为下面产品的对标素材。
只依据视频画面和声音作答。看不清或听不清的写 unclear 或 null，不要编造。

产品任务卡：
{card}

只输出一个 JSON 对象，不要其他文字：
{"product_relation": "same|similar|different|unclear",
"product_shown": true或false,
"product_first_seen_s": 数字或null,
"hook_first_3s": "前3秒钩子，一句话",
"structure": "口播|产品演示|前后对比|场景生活|测评|其他",
"spoken_summary": "口播要点，一两句",
"on_screen_actions": "主要画面动作，一两句",
"replication_risk": "low|mid|high",
"risk_reason": "一句话理由",
"claims_conflict": true或false,
"verdict": "A|B|C|Reject",
"reason": "一句话总结"}"""


def load_tf_env():
    base = os.environ.get("TOKENSFACTORY_BASE_URL", "")
    key = os.environ.get("TOKENSFACTORY_API_KEY", "")
    env = Path("~/.config/tokensfactory/credentials.env").expanduser()
    if (not key or not base) and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            k = k.replace("export ", "").strip()
            v = v.strip().strip('"').strip("'")
            if k == "TOKENSFACTORY_API_KEY" and not key:
                key = v
            if k == "TOKENSFACTORY_BASE_URL" and not base:
                base = v
    if not key:
        sys.exit("未找到 TOKENSFACTORY_API_KEY（~/.config/tokensfactory/credentials.env）。")
    base = re.sub(r"/v1/?$", "", (base or TF_DEFAULT_BASE).rstrip("/"))
    return base, key


def media_reachable(url):
    """只探测链接是否有效（HEAD，失败再取 1 字节），不下载视频。"""
    for method in ("HEAD", "GET"):
        headers = {"User-Agent": UA}
        if method == "GET":
            headers["Range"] = "bytes=0-0"
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status in (200, 206)
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405):
                continue
            return False
        except (urllib.error.URLError, OSError):
            return False
    return False


def parse_json_obj(text):
    """兼容旧调用名；JSON 解析实现统一在 common.parse_json。"""
    from common import parse_json
    value = parse_json(text)
    return value if isinstance(value, dict) else None


def cmd_verify(a):
    out = Path(a.out)
    card_path = out / "product_card.json"
    if not card_path.exists():
        sys.exit("缺少 product_card.json，请先完成 intake。")
    card = read_json(card_path)
    cands = read_json(out / "candidates.json")
    if a.ids:
        want = set(a.ids.split(","))
        pool = [c for c in cands if c["candidate_id"] in want]
    else:
        pool = [c for c in cands if c["final_pass"] or (a.include_weak and c.get("topic_level") == "weak")]
        pool.sort(key=lambda c: -c["auto_score"])
        pool = pool[: a.top]

    res_path = out / "verify.json"
    results = {r["aweme_id"]: r for r in read_json(res_path)} if res_path.exists() else {}
    todo = [c for c in pool if c["aweme_id"] not in results]
    print(f"验证候选 {len(pool)} 条；已有结果跳过 {len(pool) - len(todo)} 条；待调用 {len(todo)} 条（模型 {a.model}）")
    for c in todo:
        print(f"  {c['candidate_id']} @{c['author']} {c['url']}")
    if a.dry_run:
        print("dry-run：未联网、未调用 TokensFactory、未计费。")
        return
    if not todo:
        print("无需新调用。")
        return
    if len(todo) > a.budget_calls:
        sys.exit(f"待调用 {len(todo)} 条超过 --budget-calls {a.budget_calls}。请缩小范围或经用户确认后调高。")

    base, key = load_tf_env()
    log = out / "verify_calls.jsonl"
    prompt = VERIFY_PROMPT.replace("{card}", json.dumps(card, ensure_ascii=False, indent=1))
    for c in todo:
        url = next((u for u in (c.get("play_urls") or []) if media_reachable(u)), "")
        now = dt.datetime.now(CST).isoformat(timespec="seconds")
        rec = {"aweme_id": c["aweme_id"], "candidate_id": c["candidate_id"], "model": a.model, "at": now}
        if not url:
            rec.update(status="skip", reason="视频链接不可用（已过期或被拒），需重新 search 后再验证")
            print(f"{c['candidate_id']} 跳过：链接不可用，未计费")
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            continue
        body = {
            "model": a.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "video_url", "video_url": url},
            ]}],
            "max_tokens": 900,
        }
        req = urllib.request.Request(
            base + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": TF_UA})
        stop = False
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                js = json.loads(r.read().decode("utf-8"))
            text = (js["choices"][0]["message"]["content"] or "")
            obj = parse_json_obj(text)
            rec.update(status="ok", usage=js.get("usage") or {})
            entry = {"aweme_id": c["aweme_id"], "candidate_id": c["candidate_id"], "model": a.model, "verified_at": now}
            if obj is None:
                entry.update(parse_error=True, raw_text=text[:1500])
            else:
                entry.update(obj)
            results[c["aweme_id"]] = entry
            write_json(res_path, list(results.values()))
            print(f"{c['candidate_id']} 完成：{entry.get('verdict', '?')} {entry.get('reason', '')[:40]}")
        except urllib.error.HTTPError as e:
            rec.update(status="http_error", code=e.code)
            print(f"{c['candidate_id']} 失败 HTTP {e.code}，不重试")
            stop = e.code in (401, 403, 429)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            rec.update(status="error", detail=type(e).__name__)
            print(f"{c['candidate_id']} 失败 {type(e).__name__}，不重试")
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        studio_ledger.append("tokensfactory", a.model, rec["status"] if rec["status"] != "ok" else "ok",
                             project=out.name, ref=c["candidate_id"], units=1, usage=rec.get("usage"))
        if stop:
            print("鉴权或额度问题，停止后续调用。")
            break
    print(f"验证结果 → {res_path}")


# ---------------------------------------------------------------- 报告

VERDICT_CLS = {"A": "v-a", "B": "v-b", "C": "v-c", "Reject": "v-r"}
REL_TXT = {"same": "同款", "similar": "同类", "different": "不同", "unclear": "无法判断"}
CSS = """
:root{--bg:#f7f7f8;--fg:#1d1d1f;--muted:#6b6b72;--card:#fff;--line:#e4e4e8;--ok:#1f8a4c;--warn:#b7791f;--gray:#9aa0a6;--accent:#2563eb;--red:#dc2626}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#111214;--fg:#ececef;--muted:#9a9aa3;--card:#1c1d21;--line:#2c2d33;--ok:#4ade80;--warn:#fbbf24;--gray:#6b7280;--accent:#60a5fa;--red:#f87171}}
:root[data-theme="dark"]{--bg:#111214;--fg:#ececef;--muted:#9a9aa3;--card:#1c1d21;--line:#2c2d33;--ok:#4ade80;--warn:#fbbf24;--gray:#6b7280;--accent:#60a5fa;--red:#f87171}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.muted{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.vcard{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.thumb{position:relative;aspect-ratio:9/16;max-height:340px;background:var(--line);display:flex;align-items:center;justify-content:center}
.thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.vbody{padding:12px;display:flex;flex-direction:column;gap:6px;min-width:0}
.vbody h3{margin:0;font-size:15px;overflow-wrap:anywhere}
.desc{margin:0;font-size:13px;overflow-wrap:anywhere}
.row1{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.tag{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}
.t-abs{color:var(--red);border-color:var(--red)}.t-low{color:var(--ok);border-color:var(--ok)}.t-high{color:var(--accent);border-color:var(--accent)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font-size:13px;margin:6px 0}
.kv dt{color:var(--muted)}.kv dd{margin:0;overflow-wrap:anywhere}
.verdict{font-weight:700;padding:0 6px;border-radius:4px;border:1px solid var(--line)}
.v-a{color:var(--ok)}.v-b{color:var(--accent)}.v-c{color:var(--warn)}.v-r{color:var(--gray)}
.btn{align-self:flex-start;margin-top:4px;font-size:13px;color:var(--accent);text-decoration:none;border:1px solid var(--accent);border-radius:8px;padding:4px 10px}
.noimg{color:var(--muted);font-size:13px}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.stat b{font-size:20px;display:block}
ul{margin:4px 0;padding-left:18px}
a{color:var(--accent)}
@media (max-width:600px){main{padding:16px 16px}.grid{grid-template-columns:1fr}}
"""
REPORT_TEMPLATE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title><style>__CSS__</style></head>
<body><main>
<h1>__TITLE__</h1><p class="muted">__META__</p>
<h2>产品任务卡</h2><div class="card">__CARD__</div>
<h2>概览</h2><div class="stats">__STATS__</div>
<h2>入选视频（按自动分排序）</h2><div class="grid">__CARDS__</div>
<h2>筛选漏斗</h2><div class="card">__FUNNEL__</div>
<h2>粉丝 × 播放</h2><div class="card">__SCATTER__<div class="muted">越靠左上，代表低粉高播放，即低粉爆款。</div></div>
<h2>待复核（泛词命中，需人工看）</h2><div class="tablewrap card"><table>__WEAK__</table></div>
<h2>硬筛通过的全部条目</h2><div class="tablewrap card"><table>__HARD__</table></div>
<h2>垂直达人候选</h2><div class="tablewrap card"><table>__CREATORS__</table></div>
<h2>未过硬筛原因</h2><div class="card">__REASONS__</div>
<p class="muted">封面与视频链接来自 TikTok CDN，链接有时效；本报告不保存视频文件。"打开视频"跳转 TikTok。AI 验证结论仅供参考，最终由人工确认。</p>
</main></body></html>"""


def verify_html(v):
    if not v:
        return "<p class='muted'>未验证（运行 verify 后显示）</p>"
    if v.get("parse_error"):
        return f"<p class='muted'>模型输出无法解析：{esc(v.get('raw_text', '')[:200])}</p>"
    first = v.get("product_first_seen_s")
    shown = "是" + (f"，约 {first} 秒首次出现" if first is not None else "") if v.get("product_shown") else "否"
    rows = [
        ("产品关系", esc(REL_TXT.get(v.get("product_relation"), v.get("product_relation") or "")) + "；产品出现：" + esc(shown)),
        ("前3秒钩子", esc(v.get("hook_first_3s", ""))),
        ("结构", esc(v.get("structure", ""))),
        ("口播要点", esc(v.get("spoken_summary", ""))),
        ("画面动作", esc(v.get("on_screen_actions", ""))),
        ("复刻风险", esc(v.get("replication_risk", "")) + "：" + esc(v.get("risk_reason", ""))),
        ("模型结论", f"<span class='verdict {VERDICT_CLS.get(v.get('verdict'), '')}'>{esc(v.get('verdict', ''))}</span> " + esc(v.get("reason", ""))),
    ]
    return "<dl class='kv'>" + "".join(f"<dt>{esc(k)}</dt><dd>{val}</dd>" for k, val in rows) + "</dl>"


def shortlist_card(c, v):
    cover = c.get("cover_url") or ""
    img = ('<span class="noimg">封面（链接可能已过期）</span>'
           + (f'<img src="{esc(cover)}" referrerpolicy="no-referrer" loading="lazy" alt="封面" onerror="this.remove()">' if cover else ''))
    meta = f"{esc(c['published_at_cst'])} · {c['duration_s']}s · 播放 {c['plays']:,} · 赞 {c['likes']:,} · 粉丝 {c['author_followers']:,}"
    return (
        '<article class="vcard">'
        f'<div class="thumb">{img}</div>'
        '<div class="vbody">'
        f'<div class="row1"><span class="tag {vt_class(c.get("viral_type"))}">{esc(c.get("viral_type", ""))}</span>'
        f'<span class="muted">{esc(c["candidate_id"])} · 自动分 {c["auto_score"]}/60</span></div>'
        f'<h3>@{esc(c["author"])}</h3>'
        f'<p class="desc">{esc(c["desc"][:160])}</p>'
        f'<p class="muted">{meta}</p>'
        f'{verify_html(v)}'
        f'<a class="btn" href="{esc(c["url"])}" target="_blank" rel="noopener">打开视频 ↗</a>'
        '</div></article>'
    )


def cmd_report(a, client):
    out = client.out
    cands = read_json(out / "candidates.json")
    creators = read_json(out / "creators.json") if (out / "creators.json").exists() else []
    card = read_json(out / "product_card.json") if (out / "product_card.json").exists() else {}
    vmap = {v["aweme_id"]: v for v in read_json(out / "verify.json")} if (out / "verify.json").exists() else {}
    raw_n = sum(1 for f in out.glob("items_*.json") for _ in read_json(f))
    final = sorted([c for c in cands if c["final_pass"]], key=lambda c: -c["auto_score"])
    weak = [c for c in cands if c["hard_pass"] and c.get("topic_level") == "weak"]
    hard = sorted([c for c in cands if c["hard_pass"]], key=lambda c: -c["plays"])
    product = card.get("product_name") or out.name
    verified_n = sum(1 for c in final if c["aweme_id"] in vmap)

    stages = [("原始条目", raw_n), ("去重后视频", len(cands)), ("硬筛通过", len(hard)),
              ("主题入选", len(final)), ("弱相关待复核", len(weak))]
    reasons = {}
    for c in cands:
        for r in c["fail_reasons"]:
            reasons[r] = reasons.get(r, 0) + 1
    reason_rows = funnel_html(sorted(reasons.items(), key=lambda kv: -kv[1])[:8]) or "<p class='muted'>无</p>"

    stats = [("入选视频", len(final)), ("已 AI 验证", verified_n), ("待复核", len(weak)),
             ("原始条目", raw_n), ("硬筛通过", len(hard))]
    stats_html = "".join(f'<div class="stat"><span class="muted">{esc(k)}</span><b>{v}</b></div>' for k, v in stats)

    def lst(key):
        items = card.get(key) or []
        return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>" if items else "<span class='muted'>无</span>"
    card_html = (
        f"<p><b>{esc(product)}</b> · {esc(card.get('category', ''))} · {esc(card.get('market', ''))} / {esc(card.get('language', ''))}"
        f" · 目标 {esc(card.get('target_count', ''))} 条</p>"
        f"<p class='muted'>使用场景：{esc(card.get('use_case', ''))}</p>"
        f"<p class='muted'>卖点</p>{lst('selling_points')}"
        f"<p class='muted'>禁止表达</p>{lst('forbidden_claims')}"
        f"<p class='muted'>搜索词</p>{lst('keywords')}"
    ) if card else "<p class='muted'>未写入任务卡（product_card.json）。</p>"

    cards_html = "".join(shortlist_card(c, vmap.get(c["aweme_id"])) for c in final) or \
        "<p class='muted'>没有主题入选的视频。</p>"

    weak_rows = "<tr><th>编号</th><th>作者</th><th>播放</th><th>点赞</th><th>时长</th><th>文案</th><th>链接</th></tr>" + "".join(
        f"<tr><td>{esc(c['candidate_id'])}</td><td>@{esc(c['author'])}</td><td>{c['plays']:,}</td><td>{c['likes']:,}</td>"
        f"<td>{c['duration_s']}s</td><td>{esc(c['desc'][:120])}</td><td><a href='{esc(c['url'])}' target='_blank' rel='noopener'>打开</a></td></tr>"
        for c in weak) or "<tr><td class='muted'>无</td></tr>"

    def hard_status(c):
        if c["final_pass"]:
            return "入选"
        if c.get("topic_level") == "weak":
            return "待复核"
        if c.get("dup_verdict") == "疑似搬运/重复-淘汰":
            return "重复淘汰"
        return "主题无关"
    hard_rows = "<tr><th>编号</th><th>类型</th><th>作者</th><th>粉丝</th><th>播放</th><th>点赞</th><th>时长</th><th>发布（北京）</th><th>文案</th><th>状态</th><th>链接</th></tr>" + "".join(
        f"<tr><td>{esc(c['candidate_id'])}</td><td>{esc(c.get('viral_type', ''))}</td><td>@{esc(c['author'])}</td>"
        f"<td>{c['author_followers']:,}</td><td>{c['plays']:,}</td><td>{c['likes']:,}</td><td>{c['duration_s']}s</td>"
        f"<td>{esc(c['published_at_cst'])}</td><td>{esc(c['desc'][:80])}</td><td>{hard_status(c)}</td>"
        f"<td><a href='{esc(c['url'])}' target='_blank' rel='noopener'>打开</a></td></tr>"
        for c in hard) or "<tr><td class='muted'>无</td></tr>"

    cr_rows = "<tr><th>作者</th><th>粉丝</th><th>入选视频数</th><th>示例</th></tr>" + "".join(
        f"<tr><td>{esc(d.get('nickname', ''))}（@{esc(d.get('unique_id', d.get('author', '')))}）</td>"
        f"<td>{(d.get('followers') or 0):,}</td><td>{d.get('final_videos', 0)}</td>"
        f"<td><a href='{esc(d.get('sample_url', ''))}' target='_blank' rel='noopener'>打开</a></td></tr>"
        for d in creators) if creators else "<tr><td class='muted'>无</td></tr>"

    meta = f"批次 {out.name} · 生成于 {dt.datetime.now(CST).strftime('%Y-%m-%d %H:%M')} 北京时间"
    html_out = (REPORT_TEMPLATE
                .replace("__TITLE__", esc(f"TikTok 对标调研：{product}"))
                .replace("__CSS__", CSS)
                .replace("__META__", esc(meta))
                .replace("__CARD__", card_html)
                .replace("__STATS__", stats_html)
                .replace("__CARDS__", cards_html)
                .replace("__FUNNEL__", funnel_html(stages))
                .replace("__SCATTER__", scatter_svg(cands))
                .replace("__WEAK__", weak_rows)
                .replace("__HARD__", hard_rows)
                .replace("__CREATORS__", cr_rows)
                .replace("__REASONS__", reason_rows))
    (out / "report.html").write_text(html_out, encoding="utf-8")
    print(f"报告 → {out / 'report.html'}（入选 {len(final)} 条，已验证 {verified_n} 条）")


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", help="包含 TIKHUB_API_KEY 的 env 文件")
    common.add_argument("--out", required=True, help="批次目录，例如 research/tiktok-benchmark/20261110-US-pet")
    common.add_argument("--budget-calls", type=int, default=10, help="本次运行最多计费请求次数")
    common.add_argument("--dry-run", action="store_true", help="只打印计划调用，不联网、不计费")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("trends", parents=[common], help="热门标签榜单")
    t.add_argument("--country", default="US")
    t.add_argument("--time-range", type=int, default=7, choices=[7, 30, 90])
    t.add_argument("--industry-id", type=int)
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--pages", type=int, default=1)

    s = sub.add_parser("search", parents=[common], help="关键词视频搜索")
    s.add_argument("--keywords", required=True, help="关键词文件：.json 字符串数组或 .txt 每行一个")
    s.add_argument("--region", default="US")
    s.add_argument("--publish-time", type=int, default=30, choices=[0, 1, 7, 30, 90, 180])
    s.add_argument("--sort", default="relevance", choices=["likes", "relevance"],
                   help="relevance 噪声少；likes 爆款多但噪声大，两种都建议跑")
    s.add_argument("--count", type=int, default=20)
    s.add_argument("--pages", type=int, default=1)

    h = sub.add_parser("hashtag", parents=[common], help="话题下的视频")
    h.add_argument("--ch-id", action="append")
    h.add_argument("--from-trends")
    h.add_argument("--top", type=int, default=5)
    h.add_argument("--region", default="US", help=f"仅支持 {sorted(HASHTAG_REGIONS)}")
    h.add_argument("--count", type=int, default=20)
    h.add_argument("--pages", type=int, default=1)

    ac = sub.add_parser("account", parents=[common], help="垂直达人/卖家账号作品")
    ac.add_argument("--from-creators")
    ac.add_argument("--top", type=int, default=5)
    ac.add_argument("--sec-uid", action="append")
    ac.add_argument("--count", type=int, default=20)
    ac.add_argument("--pages", type=int, default=1)

    f = sub.add_parser("filter", parents=[common], help="去重、主题门控、硬筛、爆款类型、自动分")
    f.add_argument("--config", help="主题与阈值配置（默认 <out>/config.json）")

    it = sub.add_parser("intake", parents=[common], help="校验产品任务卡 product_card.json")
    it.add_argument("--card", help="任务卡路径，默认 <out>/product_card.json")

    v = sub.add_parser("verify", parents=[common], help="用 .env 的 MODEL_VERIFY 验证入选视频（公开链接，不下载）")
    v.add_argument("--ids", help="候选编号，逗号分隔，如 C001,C003")
    v.add_argument("--top", type=int, default=5, help="按自动分取前 N 条入选视频")
    v.add_argument("--include-weak", action="store_true", help="同时验证待复核条目")
    v.add_argument("--model", default=VERIFY_MODEL)

    sub.add_parser("report", parents=[common], help="生成 report.html")

    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd in ("filter", "report", "intake"):
        client = Client("", out, 0)
        {"filter": cmd_filter, "report": cmd_report, "intake": cmd_intake}[a.cmd](a, client)
        return
    if a.cmd == "verify":
        cmd_verify(a)
        return
    key = "" if a.dry_run else load_key(a.env_file)
    client = Client(key, out, a.budget_calls, dry_run=a.dry_run)
    {"trends": cmd_trends, "search": cmd_search, "hashtag": cmd_hashtag, "account": cmd_account}[a.cmd](a, client)


if __name__ == "__main__":
    main()
