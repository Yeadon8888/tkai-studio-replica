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
    import tempfile
    digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    name = f"{digest}-{int(time.time())}{p.suffix.lower()}"
    tag = settings.get("MEDIA_RELEASE_TAG") if "MEDIA_RELEASE_TAG" in settings.load() else "temp-clips"
    _ensure_release(repo, tag)
    # 用 Release 附件而不是 contents API：大文件经 contents API 会间歇返回 401，
    # raw.githubusercontent 被模型侧大量拉取时会返回 429（2026-11-13 实测）。
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / name
        shutil.copy2(p, staged)
        r = subprocess.run(["gh", "release", "upload", tag, str(staged), "-R", repo, "--clobber"],
                           capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("gh release upload 失败：" + r.stderr.strip()[:300])
    url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
    return {"path": name, "url": url, "sha": digest, "repo": repo, "tag": tag, "bytes": size,
            "host": "release_asset", "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def _ensure_release(repo, tag):
    r = subprocess.run(["gh", "release", "view", tag, "-R", repo], capture_output=True, text=True)
    if r.returncode == 0:
        return
    c = subprocess.run(["gh", "release", "create", tag, "-R", repo, "--title", "temp clips",
                        "--notes", "Temporary clips for model review; assets are deleted after use"],
                       capture_output=True, text=True)
    if c.returncode != 0:
        raise RuntimeError("gh release create 失败：" + c.stderr.strip()[:300])


def delete(info):
    """删除临时文件并核对。返回 True 表示已确认不可访问。兼容旧记录（contents API 上传的文件）。"""
    import urllib.error
    import urllib.request
    if info.get("host") == "release_asset":
        r = subprocess.run(["gh", "release", "delete-asset", info["tag"], info["path"], "-R", info["repo"], "-y"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("gh release delete-asset 失败：" + r.stderr.strip()[:300])
    else:
        _gh([f"repos/{info['repo']}/contents/{info['path']}", "-X", "DELETE"],
            {"message": "remove temp clip", "sha": info["sha"], "branch": "main"})
    try:
        urllib.request.urlopen(urllib.request.Request(info["url"], method="HEAD"), timeout=30)
        return False
    except urllib.error.HTTPError as e:
        return e.code == 404


def log(info, extra):
    d = settings.studio_home() / "media"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "media_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({**info, **extra}, ensure_ascii=False) + "\n")
