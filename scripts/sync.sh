#!/usr/bin/env bash
# sync.sh —— 从腾讯云 COS 拉取最新 Markdown 到 data/notes（增量/差异同步）。
# 职责边界：只负责"拿内容"，不碰索引与构建（交给 rebuild.sh），便于单独排障。
# 调度：cron / systemd timer 定时执行。阶段五接入真实 COS。
set -euo pipefail

NOTES_DIR="${NOTES_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data/notes}"

# TODO(阶段五): coscli/coscmd sync COS_BUCKET → NOTES_DIR（增量）
# 早退占位：无变化时不应触发下游全量构建
echo "[sync] 占位：阶段五接入 COS 增量同步 → $NOTES_DIR"
echo "[sync] 同步逻辑未实现，直接退出"
exit 0
