"""本地视频 → 临时公网地址 → 给理解模型整条拆解或评审 → 删除。

为什么：Gemini 读视频只接受公网 HTTP(S) 地址。抽帧只是绕开限制，会丢细节，不作为默认做法。

规则（写进 skill 方法论）：
- 只在用户已经说明可以上传这个视频时使用（默认 MEDIA_UPLOAD=github_temp 视为已授权上传"对标视频"这一类）。
- 上传到用户自己 GitHub 账号下的专用公开仓库（MEDIA_HOST_REPO，必须在 .env 里设置），
  不使用任何网页上的访问码，也不使用其他人的图床。
- 拆解或评审完成后立即删除该文件，并把删除结果记入 media_log.jsonl。删除失败要告诉用户。
- 只上传视频，不上传封面、不上传运行日志。
- MEDIA_UPLOAD=off 时拒绝上传，本地视频只能用抽帧（会注明"细节不完整"）。
"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import settings  # noqa: E402


def _gh(args, payload=None):
    """调用 gh api。payload 用文件传入，避免命令行过长。"""
    cmd = ["gh", "api"] + args
    tmp = None
    if payload is not None:
        tmp = settings.studio_home() / ".cache" / f"gh-{os.getpid()}-{time.time_ns()}.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        cmd += ["--input", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()
    if r.returncode != 0:
        raise RuntimeError("gh api 失败：" + r.stderr.strip()[:300])
    return json.loads(r.stdout or "{}")


def upload(local, repo=None):
    """上传本地视频，返回 {path, url, sha, repo}。只接受 .mp4/.mov/.webm，大小不超过 90MB（GitHub 限制）。"""
    if settings.get("MEDIA_UPLOAD") != "github_temp":
        raise SystemExit("MEDIA_UPLOAD 不是 github_temp：当前不允许上传本地视频。改用抽帧（会注明细节不完整）或修改 .env。")
    p = Path(os.path.expanduser(str(local))).resolve()
    if p.suffix.lower() not in (".mp4", ".mov", ".webm"):
        raise SystemExit(f"不支持的视频格式：{p.suffix}")
    size = p.stat().st_size
    if size > 90 * 1024 * 1024:
        raise SystemExit("文件超过 90MB，不上传。")
    repo = repo or settings.get("MEDIA_HOST_REPO")
    if not repo:
        raise SystemExit("未设置 MEDIA_HOST_REPO。请在 .env 里填写你自己的公开仓库（格式 用户名/仓库名），例如 MEDIA_HOST_REPO=your-name/studio-media-temp。仓库要先在你的 GitHub 上创建为公开仓库。")
    import hashlib
    import shutil
    digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    name = f"{digest}-{int(time.time())}{p.suffix.lower()}"
    # 用 git 推送到仓库，地址是 raw.githubusercontent 直链（不跳转）。
    # 2026-11-13 实测：Release 附件链接会跳转，模型网关静默丢弃视频并照样编结论；
    # contents API 传 10MB 以上文件间歇 401。git 推送稳定，raw 直链模型能读到。
    repo_dir = _media_repo(repo)
    dest = repo_dir / "clips" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dest)
    _git(repo_dir, ["add", f"clips/{name}"])
    _git(repo_dir, ["commit", "-q", "-m", "temp clip for model review"])
    _push(repo_dir)
    url = f"https://raw.githubusercontent.com/{repo}/main/clips/{name}"
    _wait_until(url, want_ok=True)
    return {"path": f"clips/{name}", "url": url, "sha": digest, "repo": repo, "bytes": size,
            "host": "git_raw", "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def _media_repo(repo):
    """临时仓库的本地浅克隆，放在数据目录的 .cache 下，重复使用。"""
    d = settings.studio_home() / ".cache" / "media-repo"
    if not (d / ".git").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        _run_gh(["gh", "repo", "clone", repo, str(d), "--", "--depth", "1"], "gh repo clone")
        _git(d, ["config", "user.name", "studio-media"])
        _git(d, ["config", "user.email", "studio-media@localhost"])
    else:
        _git(d, ["fetch", "-q", "--depth", "1", "origin", "main"])
        _git(d, ["reset", "-q", "--hard", "origin/main"])
    return d


def _git(repo_dir, args):
    r = subprocess.run(["git", "-C", str(repo_dir)] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} 失败：" + (r.stderr or r.stdout).strip()[:300])
    return r


def _push(repo_dir):
    """推送；网络断流时退避重试。远端有新提交时先变基。"""
    last = ""
    for i in range(4):
        r = subprocess.run(["git", "-C", str(repo_dir), "push", "-q", "origin", "HEAD:main"], capture_output=True, text=True)
        if r.returncode == 0:
            return
        last = (r.stderr or r.stdout).strip()
        if "rejected" in last or "fetch first" in last:
            _git(repo_dir, ["pull", "-q", "--rebase", "origin", "main"])
        time.sleep((5, 15, 30)[min(i, 2)])
    raise RuntimeError("git push 失败：" + last[:300])


def _wait_until(url, want_ok, timeout=90):
    """raw 地址在推送后要几秒才可用；删除后 CDN 可能缓存几分钟，删除的核对用 contents API。"""
    import urllib.error
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20)
            if want_ok:
                return True
        except urllib.error.HTTPError as e:
            if not want_ok and e.code == 404:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(5)
    if want_ok:
        raise RuntimeError("上传后视频地址一直不可访问：" + url)
    return False


def _run_gh(cmd, what, attempts=4):
    """gh 命令经代理时偶尔断流（EOF）。网络类错误按 5、15、30 秒退避重试；其它错误直接报出。"""
    last = ""
    for i in range(attempts):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return r
        last = r.stderr.strip()
        if "already exists" in last:
            return r
        network = any(x in last for x in ("EOF", "timeout", "connection", "Connection", "502", "503", "504"))
        if not network or i == attempts - 1:
            break
        time.sleep((5, 15, 30)[min(i, 2)])
    raise RuntimeError(f"{what} 失败：" + last[:300])


def delete(info):
    """删除临时文件并核对。返回 True 表示仓库里已不存在（以 contents API 为准；raw 的 CDN 缓存会多留几分钟）。
    兼容旧记录：release_asset、contents API 上传的文件。"""
    if info.get("host") == "git_raw":
        repo_dir = _media_repo(info["repo"])
        if (repo_dir / info["path"]).exists():
            _git(repo_dir, ["rm", "-q", info["path"]])
            _git(repo_dir, ["commit", "-q", "-m", "remove temp clip"])
            _push(repo_dir)
    elif info.get("host") == "release_asset":
        _run_gh(["gh", "release", "delete-asset", info["tag"], info["path"], "-R", info["repo"], "-y"],
                "gh release delete-asset")
    else:
        _gh([f"repos/{info['repo']}/contents/{info['path']}", "-X", "DELETE"],
            {"message": "remove temp clip", "sha": info["sha"], "branch": "main"})
    r = subprocess.run(["gh", "api", f"repos/{info['repo']}/contents/{info['path']}"], capture_output=True, text=True)
    return r.returncode != 0 and "404" in (r.stderr + r.stdout)


def log(info, extra):
    d = settings.studio_home() / "media"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "media_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({**info, **extra}, ensure_ascii=False) + "\n")
