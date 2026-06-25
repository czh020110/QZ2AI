#!/bin/sh
# nginx docker-entrypoint.d 钩子：等待 web 构建产物写入 public 卷。
# docker compose depends_on condition: service_completed_successfully 只保证 web 容器退出码为 0，
# 但卷同步可能存在短暂延迟，此处做最终确认。
set -e

POLL_DIR="/usr/share/nginx/html"
MAX_WAIT=30
WAITED=0

while [ "$WAITED" -lt "$MAX_WAIT" ]; do
    if [ -f "$POLL_DIR/index.html" ] || find "$POLL_DIR" -mindepth 1 -maxdepth 1 | grep -q .; then
        echo "[wait-for-public] 构建产物已就绪"
        exit 0
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

echo "[wait-for-public] 警告：等待 ${MAX_WAIT}s 后仍未检测到构建产物，nginx 将使用空目录启动"
