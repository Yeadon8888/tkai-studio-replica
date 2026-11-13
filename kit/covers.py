"""封面本地化：TikTok 封面地址带签名、会过期（x-expires），看板一段时间后就只剩空白。

做法：拿到封面地址的那一刻就把图片存到调研批次的 covers/ 目录，看板内嵌图片（data URI），不再依赖临时地址。
- 链接候选：封面地址未过期 → 直接下载；已过期 → 用分享链接向 TikHub 刷新一次（1 次请求）再下载。
- 本地视频候选：用 ffmpeg 截第 1 秒的画面当封面。
- 只下载封面图片（几十 KB），不下载视频。

命令：
  python3 covers.py backfill --batch <调研批次目录> [--refresh] [--only-shown]
    给已有批次补封面。--refresh 允许对已过期的封面调用 TikHub 刷新（每条 1 次）；不加则只下载未过期的。
"""
import argparse
import base64
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import settings  # noqa: E402

MAX_BYTES = 400_000
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def cover_path(batch, cand):
    return Path(batch) / "covers" / f"{cand['aweme_id']}.jpg"


def expired(url, now=None):
    """地址里带 x-expires 且已过期时返回 True；看不出有效期时返回 False（交给下载结果判断）。"""
    m = re.search(r"x-expires=(\d+)", url or "")
    return bool(m) and int(m.group(1)) < (now or time.time())


def download(url, dest):
    """下载封面图片。成功返回 True。只接受图片，超过 MAX_BYTES 不保存。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.tiktok.com/"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            data = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError):
        return False
    if not ctype.startswith("image/") or not data or len(data) > MAX_BYTES:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".src")
    tmp.write_bytes(data)
    # 统一转成宽 360 的 JPG（webp/heic 浏览器兼容不一）；没有 ffmpeg 时原样保存
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-vf", "scale=360:-2", str(dest)],
                       capture_output=True)
    if r.returncode != 0:
        tmp.replace(dest)
    else:
        tmp.unlink(missing_ok=True)
    return dest.exists() and dest.stat().st_size > 0


def frame_cover(video, dest):
    """本地视频：截第 1 秒（视频不足 1 秒时截第一帧）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for ss in ("1", "0"):
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", ss, "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=360:-2", str(dest)], capture_output=True)
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return True
    return False


def refresh_cover_url(cand, batch):
    """用分享链接向 TikHub 重新取一次，返回新的封面地址（顺便更新播放地址）。失败返回空串。"""
    if not cand.get("url"):
        return ""
    import intake
    import tt_research as tt
    try:
        client = tt.Client(tt.load_key(), batch, budget_calls=1)
        fresh = intake.fetch_url_candidate(cand["url"], client)
    except (SystemExit, Exception):  # noqa: BLE001  刷新失败不影响其它候选
        return ""
    cand["play_urls"] = fresh.get("play_urls") or cand.get("play_urls") or []
    return fresh.get("cover_url") or ""


def cache(batch, cand, refresh=False):
    """给一条候选保存封面。返回状态：cached / exists / downloaded / refreshed / frame / expired / failed / none。
    成功时在候选上写 cover_file（相对批次目录）。"""
    dest = cover_path(batch, cand)
    if dest.exists() and dest.stat().st_size > 0:
        cand["cover_file"] = dest.relative_to(Path(batch)).as_posix()
        return "exists"
    if cand.get("local_file"):
        ok = Path(cand["local_file"]).exists() and frame_cover(cand["local_file"], dest)
        status = "frame" if ok else "failed"
    else:
        url = cand.get("cover_url") or ""
        status = "none"
        if url and not expired(url) and download(url, dest):
            status = "downloaded"
        elif refresh:
            new = refresh_cover_url(cand, batch)
            if new:
                cand["cover_url"] = new
                status = "refreshed" if download(new, dest) else "failed"
            else:
                status = "failed"
        elif url:
            status = "expired"
    if dest.exists() and dest.stat().st_size > 0:
        cand["cover_file"] = dest.relative_to(Path(batch)).as_posix()
        cand["cover_cached_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return status


def shown(cand):
    """看板会展示的候选（与 research.build 的筛选一致），只给它们补封面，省请求。"""
    return cand.get("source") == "manual" or (cand.get("hard_pass") and cand.get("topic_level") in ("strong", "analogy"))


def cache_batch(batch, refresh=False, only_shown=True, cands=None):
    """给整个批次补封面并写回 candidates.json。返回各状态的计数。"""
    batch = Path(batch)
    path = batch / "candidates.json"
    own = cands is None
    if own:
        cands = json.loads(path.read_text(encoding="utf-8"))
    counts = {}
    for c in cands:
        if only_shown and not shown(c):
            continue
        s = cache(batch, c, refresh=refresh)
        counts[s] = counts.get(s, 0) + 1
    if own:
        path.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts


def data_uri(batch, cand):
    """看板内嵌用：本地封面转成 data URI；没有本地封面返回空串。"""
    rel = cand.get("cover_file")
    if not rel:
        return ""
    p = Path(batch) / rel
    if not p.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def main(argv=None):
    ap = argparse.ArgumentParser(description="封面本地化")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--batch", required=True)
    b.add_argument("--refresh", action="store_true", help="封面已过期时调用 TikHub 刷新（每条 1 次请求）")
    b.add_argument("--all", action="store_true", help="给所有候选补（默认只补看板会展示的）")
    a = ap.parse_args(argv)
    settings.load()
    counts = cache_batch(a.batch, refresh=a.refresh, only_shown=not a.all)
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
