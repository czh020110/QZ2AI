#!/usr/bin/env bash
# rebuild.sh —— 内容变成索引与静态站：触发增量索引 + Quartz 构建。
# 职责边界：sync.sh 拿内容后串接本脚本，或独立定时。
#
# 单写入者硬约束：嵌入式 Chroma 底层 SQLite，多进程同时写会损坏数据。
# 因此索引重建必须经 API 进程内的 /api/reindex 触发，
# 禁止本脚本另起独立 python 进程直接写 data/chroma。
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
REINDEX_TOKEN="${REINDEX_TOKEN:-}"

# TODO(阶段三): 无变化早退判断（对比内容 hash / mtime），避免空跑全量构建

# 1) 增量索引：调用 API 进程内的 /api/reindex（遵守单写入者约束）
echo "[rebuild] 占位：触发 $API_BASE/api/reindex（阶段三实现真实索引）"
# curl -fsS -X POST "$API_BASE/api/reindex" -H "X-Reindex-Token: $REINDEX_TOKEN"

# 2) Quartz 构建：重跑 web 容器把 data/notes 构建到 public 卷
echo "[rebuild] 占位：触发 Quartz 构建（docker compose run --rm web）"
# docker compose run --rm web

echo "[rebuild] 重建逻辑未实现，直接退出"
exit 0
