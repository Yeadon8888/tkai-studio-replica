"""发布打包：从开发目录导出一份干净的安装包（不含任何个人数据、密钥或运行产物），并生成 zip。

用法（在 studio/ 目录下）：
  python3 kit/package.py                      输出到 dist/studio-replica-<版本>.zip
  python3 kit/package.py --out <目录>          指定输出目录

会排除：.env、.git、运行目录（projects/ ledger/ media/ research/ audit/ takes/）、
  products/ 下除 _example 以外的品、methods/review_log、开发历史文档、旧版配置备份、缓存。
导出后会在导出目录里跑一次测试，失败就不生成 zip。
"""
import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parent

KEEP_TOP = ["SKILL.md", "README.md", "VERSION", "CHANGELOG.md", ".env.example", ".gitignore", "setup.sh",
            "evals", "references", "schemas", "methods", "kit"]
EXCLUDE_NAMES = {".env", ".env.bak", ".cache", "__pycache__", ".DS_Store", "skills", "dist"}
EXCLUDE_PATHS = {"methods/review_log", "kit/config/experts.v1.yaml"}
EXCLUDE_SUFFIX = {".mp4", ".mov", ".webm", ".jpg", ".png", ".log", ".pyc"}


def _skip(rel):
    parts = rel.split("/")
    if any(p in EXCLUDE_NAMES for p in parts):
        return True
    if any(rel == x or rel.startswith(x + "/") for x in EXCLUDE_PATHS):
        return True
    if Path(rel).suffix.lower() in EXCLUDE_SUFFIX:
        return True
    return False


def export(dest):
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for top in KEEP_TOP:
        src = ROOT / top
        if not src.exists():
            continue
        items = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file()]
        for f in items:
            rel = f.relative_to(ROOT).as_posix()
            if _skip(rel):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    # 产品目录：只保留示例与空队列
    (dest / "products").mkdir(exist_ok=True)
    if (ROOT / "products/_example").exists():
        shutil.copytree(ROOT / "products/_example", dest / "products/_example", dirs_exist_ok=True)
    (dest / "products/_queue.json").write_text(json.dumps({"tasks": {}}, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    # 安装包里不应有开发期的个人配置
    env_example = dest / ".env.example"
    if env_example.exists():
        env_example.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def check_clean(dest):
    """扫描导出目录：个人路径、个人账号、密钥样式字符串。发现即失败。"""
    bad = []
    import re
    patterns = ["/" + "Users" + r"/\w+", r"yeadon", r"Yeadon8888", r"tkAI视频增长中台", r"MyProject", r"gho_[A-Za-z0-9]{20,}",
                r"sk-[A-Za-z0-9]{20,}"]
    for f in dest.rglob("*"):
        if not f.is_file() or f.suffix.lower() in (".zip", ".pyc") or f.name == "package.py":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for p in patterns:
            if re.search(p, text, flags=re.IGNORECASE):
                bad.append(f"{f.relative_to(dest)}: 匹配 {p}")
    return bad


def run_tests(dest):
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "kit/tests"], cwd=dest,
                       capture_output=True, text=True)
    tail = (r.stderr or "").strip().splitlines()[-1:] or [""]
    return r.returncode == 0, tail[0]


def make_zip(src, zip_path):
    name = src.name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f"{name}/{f.relative_to(src).as_posix()}")
    return zip_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出干净的安装包")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args(argv)
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tree = out / "studio-replica"
    export(tree)
    bad = check_clean(tree)
    if bad:
        print("导出目录含个人信息，已停止：")
        for b in bad:
            print("  -", b)
        return 1
    ok, msg = run_tests(tree)
    print("导出目录测试：", "通过" if ok else f"失败 {msg}")
    if not ok:
        return 1
    zp = make_zip(tree, out / f"studio-replica-{version}.zip")
    print("安装包：", zp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
