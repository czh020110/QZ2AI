#!/usr/bin/env bash
# deploy.sh —— 服务器发布：git pull + docker compose 重建/重启相关服务。
# 职责边界：手动/发布时执行。版本回滚 = git checkout 指定版本 + 重跑本脚本。
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PROJECT_DIR"

# TODO(阶段五): 校验 .env 存在、git pull、docker compose build/up -d、健康检查探活
echo "[deploy] 占位：git pull → docker compose up -d --build → 探活 /api/health"
# git pull
# docker compose up -d --build
# curl -fsS http://localhost/api/health

echo "[deploy] 发布逻辑未实现，直接退出"
exit 0
