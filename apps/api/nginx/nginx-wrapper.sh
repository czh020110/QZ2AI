#!/bin/sh
# nginx 启动脚本：等待 Quartz 构建产物写入 public 卷后再启动 nginx
set -e

POLL_DIR="/usr/share/nginx/html"
MAX_WAIT=60
WAITED=0

while [ "$WAITED" -lt "$MAX_WAIT" ]; do
    if [ -f "$POLL_DIR/index.html" ] || find "$POLL_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | grep -q .; then
        echo "[nginx] 构建产物已就绪，启动 nginx"
        exec nginx -g "daemon off;"
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

echo "[nginx] 超时未检测到构建产物，启动空站"
exec nginx -g "daemon off;"
