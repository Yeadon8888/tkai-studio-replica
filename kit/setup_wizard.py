"""一键配置：问几个问题，把配置写进 .env（权限 600），然后运行体检。

用法：
  python3 kit/setup_wizard.py            交互式：按提示输入（密钥输入时不显示）
  python3 kit/setup_wizard.py --from-env 从当前环境变量读取，不提问（适合自动化）
  python3 kit/setup_wizard.py --check    只运行体检，不改配置

不会打印任何密钥。已有的 .env 会先备份为 .env.bak（同目录）。
"""
import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

ASK = [  # (键, 提示, 是否密钥, 默认值)
    ("TOKENSFACTORY_API_KEY", "TokensFactory 密钥（生成视频、Gemini 用）", True, ""),
    ("TIKHUB_API_KEY", "TikHub 密钥（调研搜索用；只用手动对标可留空）", True, ""),
    ("MEDIA_HOST_REPO", "你的公开 GitHub 仓库，用于临时上传本地视频（格式 用户名/仓库名；不用本地视频可留空）", False, ""),
    ("PRODUCT_BUDGET_CNY", "每个品的默认预算（元）", False, "5"),
]


def read_env(path):
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def write_env(values):
    """以 .env.example 为模板，替换已有键的值；不在模板里的键追加到末尾。"""
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines() if EXAMPLE.exists() else []
    seen = set()
    out = []
    for line in lines:
        m = re.match(r"^([A-Z_]+)=", line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    if ENV.exists():
        shutil.copy(ENV, ENV.with_suffix(".env.bak"))
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(ENV, 0o600)


def collect(from_env):
    current = read_env(ENV)
    values = {}
    for key, prompt, secret, default in ASK:
        if from_env:
            v = os.environ.get(key, current.get(key, default))
        else:
            shown = "已设置" if current.get(key) and secret else (current.get(key) or default)
            hint = f"（当前：{shown}，回车保留）" if shown else "（回车跳过）"
            raw = getpass.getpass(f"{prompt} {hint}: ") if secret else input(f"{prompt} {hint}: ")
            v = raw.strip() or current.get(key, default)
        if v:
            values[key] = v
    return values


def run_doctor():
    print("\n运行体检……")
    r = subprocess.run([sys.executable, str(KIT / "doctor.py")], cwd=KIT)
    return r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description="studio-replica 一键配置")
    ap.add_argument("--from-env", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.check:
        return run_doctor()
    if not ENV.exists() and not EXAMPLE.exists():
        sys.exit("找不到 .env.example，请确认安装包完整。")
    values = collect(a.from_env)
    if not values.get("TOKENSFACTORY_API_KEY") and not os.environ.get("TOKENSFACTORY_API_KEY"):
        print("提示：还没有填 TokensFactory 密钥。生成视频和拆解都需要它。")
    write_env(values)
    print(f"已写入 {ENV}（权限 600，不会被提交到仓库）。")
    return run_doctor()


if __name__ == "__main__":
    sys.exit(main())
