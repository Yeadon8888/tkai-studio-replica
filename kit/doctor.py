"""体检：使用前、换模型后、升级后都跑一遍。默认不联网、不花钱。

  python3 doctor.py            本地检查：Python、依赖、ffmpeg、.env、密钥是否存在、目录可写
  python3 doctor.py --online   另外查询供应方的模型列表（/v1/models，免费），确认 .env 里的模型名真的存在

只报告"有没有"，不打印任何密钥内容。退出码：0 = 全部通过；1 = 有阻塞项。
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import settings  # noqa: E402


def local_checks():
    rows = []  # (级别, 名称, 是否通过, 说明)
    rows.append(("block", "Python ≥ 3.9", sys.version_info >= (3, 9), sys.version.split()[0]))
    try:
        import yaml  # noqa: F401
        rows.append(("block", "依赖 pyyaml", True, ""))
    except ImportError:
        rows.append(("block", "依赖 pyyaml", False, "pip install -r kit/requirements.txt"))
    for tool in ("ffmpeg", "ffprobe"):
        rows.append(("block", tool, shutil.which(tool) is not None, shutil.which(tool) or "未安装"))
    cfg = settings.load(force=True)
    rows.append(("warn", ".env 文件", bool(cfg["_env_file"]), cfg["_env_file"] or "未找到，正在使用默认值；cp .env.example .env"))
    rows.append(("block", "配置版本", str(cfg["CONFIG_VERSION"]) == str(settings.CONFIG_VERSION),
                 f".env={cfg['CONFIG_VERSION']} 代码={settings.CONFIG_VERSION}"))
    for k in settings.SECRET_KEYS:
        rows.append(("block", f"密钥 {k}", bool(os.environ.get(k)), "已找到" if os.environ.get(k) else "未找到：检查 CREDENTIALS_FILES"))
    home = settings.studio_home()
    rows.append(("block", "数据目录可写", os.access(home, os.W_OK), str(home)))
    try:
        w = settings.tier_weights()
        rows.append(("block", "TIER_WEIGHTS 可解析", bool(w) and all(v >= 0 for v in w.values()), str(w)))
    except ValueError as e:
        rows.append(("block", "TIER_WEIGHTS 可解析", False, str(e)))
    rows.append(("block", "视频单价 > 0", settings.video_price() > 0, f"{settings.video_price()} 元/秒"))
    return rows


def online_checks():
    rows = []
    base = settings.get("TOKENSFACTORY_BASE_URL").rstrip("/")
    key = os.environ.get("TOKENSFACTORY_API_KEY", "")
    try:
        req = urllib.request.Request(base + "/v1/models", headers={"Authorization": f"Bearer {key}",
                                                                    "User-Agent": settings.get("HTTP_USER_AGENT")})
        with urllib.request.urlopen(req, timeout=30) as r:
            ids = {m.get("id") for m in json.loads(r.read().decode("utf-8")).get("data", [])}
    except Exception as e:  # 联网检查失败只报告，不抛出
        return [("block", "模型列表 /v1/models", False, f"{type(e).__name__}")]
    rows.append(("block", "模型列表 /v1/models", True, f"{len(ids)} 个模型"))
    wanted = {"video": settings.model("video"), **{r: settings.model(r) for r in settings.TEXT_ROLES}}
    for role, mid in wanted.items():
        rows.append(("block", f"模型 {role} = {mid}", mid in ids, "存在" if mid in ids else "供应方没有这个模型名"))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="studio-replica 体检")
    ap.add_argument("--online", action="store_true", help="查询模型列表（免费），确认模型名存在")
    a = ap.parse_args(argv)
    rows = local_checks() + (online_checks() if a.online else [])
    print(f"studio-replica {settings.skill_version()}")
    bad = 0
    for level, name, ok, note in rows:
        mark = "通过" if ok else ("阻塞" if level == "block" else "提醒")
        if not ok and level == "block":
            bad += 1
        print(f"[{mark}] {name}  {note}")
    print("结果：", "全部通过" if not bad else f"{bad} 项阻塞")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
