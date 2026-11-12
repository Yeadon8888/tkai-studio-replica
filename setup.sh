#!/usr/bin/env bash
# studio-replica 一键安装与配置（macOS / Linux）
# 用法：bash setup.sh   然后按提示填写密钥即可。
set -e
cd "$(dirname "$0")"

echo "== 1/4 检查 Python（需要 3.9 以上）"
PY=$(command -v python3 || true)
if [ -z "$PY" ]; then echo "未找到 python3，请先安装：https://www.python.org/downloads/"; exit 1; fi
"$PY" -c 'import sys; assert sys.version_info >= (3, 9), sys.version' || { echo "Python 版本太低"; exit 1; }

echo "== 2/4 安装依赖"
"$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install --quiet -r kit/requirements.txt

echo "== 3/4 检查 ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "未找到 ffmpeg（抽帧和取视频信息要用）。macOS 可运行：brew install ffmpeg"
  echo "Linux 可运行：sudo apt install ffmpeg"
fi

echo "== 4/4 配置"
"$PY" kit/setup_wizard.py
echo ""
echo "完成。之后可以随时运行：python3 kit/doctor.py --online  来检查配置。"
