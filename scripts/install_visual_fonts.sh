#!/usr/bin/env bash
set -euo pipefail

if ! command -v apt-get >/dev/null 2>&1; then
  echo "当前脚本仅支持 Debian/Ubuntu；请在镜像构建阶段安装 Noto CJK 字体包。" >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk
fc-cache -f
if ! fc-match "Noto Sans CJK SC" | grep -qi "Noto"; then
  echo "Noto Sans CJK SC 未正确安装。" >&2
  exit 1
fi
echo "Noto CJK fonts ready."
