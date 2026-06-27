#!/bin/bash
# syncer 守护进程：轮询 API 同步状态，检测到 pending 且防抖过期后执行完整同步链路
# 完整链路：coscli sync → POST /api/reindex → docker compose run --rm web
set -euo pipefail

PROJECT_DIR="/project"
ENV_FILE="$PROJECT_DIR/.env"
POLL_INTERVAL="${SYNC_WATCHER_POLL_INTERVAL:-10}"

# ---- 从 .env 读取配置（与 sync.sh / rebuild.sh 一致的解析方式）----

read_env_value() {
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
target = sys.argv[2]
for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() != target:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    print(value)
    break
PY
}

API_BASE="${API_BASE:-$(read_env_value API_BASE)}"
API_BASE="${API_BASE:-http://api:8000}"
API_BASE="${API_BASE%/}"

ADMIN_TOKEN="${ADMIN_TOKEN:-$(read_env_value ADMIN_TOKEN)}"
REINDEX_TOKEN="${REINDEX_TOKEN:-$(read_env_value REINDEX_TOKEN)}"

COS_SECRET_ID="${COS_SECRET_ID:-$(read_env_value COS_SECRET_ID)}"
COS_SECRET_KEY="${COS_SECRET_KEY:-$(read_env_value COS_SECRET_KEY)}"
COS_REGION="${COS_REGION:-$(read_env_value COS_REGION)}"
COS_ENDPOINT="${COS_ENDPOINT:-$(read_env_value COS_ENDPOINT)}"
COS_BUCKET="${COS_BUCKET:-$(read_env_value COS_BUCKET)}"
NOTES_COS_PREFIX="${NOTES_COS_PREFIX:-$(read_env_value NOTES_COS_PREFIX)}"
COS_SYNC_SOURCE="${COS_SYNC_SOURCE:-$(read_env_value COS_SYNC_SOURCE)}"
COSCLI_CONFIG_PATH="${COSCLI_CONFIG_PATH:-$(read_env_value COSCLI_CONFIG_PATH)}"
COS_ENDPOINT="${COS_ENDPOINT#https://}"
COS_ENDPOINT="${COS_ENDPOINT#http://}"
COS_ENDPOINT="${COS_ENDPOINT%/}"

# 拼接 COS_SYNC_SOURCE
if [ -z "$COS_SYNC_SOURCE" ] && [ -n "$COS_BUCKET" ] && [ -n "$NOTES_COS_PREFIX" ]; then
  NOTES_COS_PREFIX="${NOTES_COS_PREFIX#/}"
  NOTES_COS_PREFIX="${NOTES_COS_PREFIX%/}"
  COS_SYNC_SOURCE="cos://${COS_BUCKET}/${NOTES_COS_PREFIX}/"
fi

# ---- 生成 coscli 配置 ----

sync_config_path="$COSCLI_CONFIG_PATH"
temp_config_path=""

cleanup_temp_config() {
  if [ -n "$temp_config_path" ] && [ -f "$temp_config_path" ]; then
    rm -f "$temp_config_path"
  fi
}
trap cleanup_temp_config EXIT

if [ -z "$sync_config_path" ]; then
  if [ -z "$COS_SECRET_ID" ] || [ -z "$COS_SECRET_KEY" ] || \
     [ -z "$COS_REGION" ] || [ -z "$COS_ENDPOINT" ] || [ -z "$COS_BUCKET" ]; then
    echo "[syncer] 缺少 COS 配置，无法执行同步" >&2
  else
    temp_config_path="$(mktemp --suffix=.yaml)"
    COSCLI_TEMP_CONFIG_PATH="$temp_config_path" \
    COS_SECRET_ID="$COS_SECRET_ID" \
    COS_SECRET_KEY="$COS_SECRET_KEY" \
    COS_REGION="$COS_REGION" \
    COS_ENDPOINT="$COS_ENDPOINT" \
    COS_BUCKET="$COS_BUCKET" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

def quote(name: str) -> str:
    return json.dumps(os.environ[name], ensure_ascii=False)

content = f"""cos:
  base:
    secretid: {quote('COS_SECRET_ID')}
    secretkey: {quote('COS_SECRET_KEY')}
    sessiontoken: \"\"
    protocol: https
  buckets:
    - name: {quote('COS_BUCKET')}
      alias: \"default\"
      region: {quote('COS_REGION')}
      endpoint: {quote('COS_ENDPOINT')}
      ofs: false
"""

Path(os.environ["COSCLI_TEMP_CONFIG_PATH"]).write_text(content, encoding="utf-8")
PY
    sync_config_path="$temp_config_path"
  fi
fi

# ---- 辅助函数 ----

json_value() {
  # 从 JSON 字符串中提取指定 key 的值
  echo "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$2', $3))"
}

now_epoch() {
  python3 -c "import time; print(time.time())"
}

# ---- 主循环 ----

echo "[syncer] 启动，轮询间隔: ${POLL_INTERVAL}s，API: $API_BASE"

while true; do
  # 1. 轮询同步状态
  response=$(curl -sf -H "X-Admin-Token: $ADMIN_TOKEN" "$API_BASE/admin/api/sync-status" 2>/dev/null) || {
    sleep "$POLL_INTERVAL"
    continue
  }

  pending=$(json_value "$response" "pending" "False")
  syncing=$(json_value "$response" "syncing" "False")
  debounce_until=$(json_value "$response" "debounce_until" "0")
  now=$(now_epoch)

  # 2. 判断是否需要触发同步
  should_sync=false
  if [ "$pending" = "True" ] && [ "$syncing" = "False" ]; then
    # 比较 now >= debounce_until
    if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)" "$now" "$debounce_until" 2>/dev/null; then
      should_sync=true
    fi
  fi

  if [ "$should_sync" = "true" ]; then
    echo "[syncer] 检测到待同步任务，防抖窗口已过期，开始执行"

    # 3. 标记 syncing=true
    curl -sf -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"syncing": true}' "$API_BASE/admin/api/sync-status" >/dev/null 2>&1 || true

    sync_status="success"

    # 4. 执行 COS 同步
    if [ -n "$COS_SYNC_SOURCE" ] && [ -n "$sync_config_path" ]; then
      echo "[syncer] 1/3 执行 COS 同步: $COS_SYNC_SOURCE → /data/notes"
      mkdir -p /data/notes /data/logs/cos-sync-backup
      if coscli sync "$COS_SYNC_SOURCE" /data/notes -r --delete \
          --backup-dir /data/logs/cos-sync-backup --force -c "$sync_config_path"; then
        echo "[syncer] COS 同步完成"
      else
        echo "[syncer] COS 同步失败" >&2
        sync_status="sync_failed"
      fi
    else
      echo "[syncer] 1/3 跳过 COS 同步（未配置）"
    fi

    # 5. 触发 reindex
    echo "[syncer] 2/3 触发索引重建"
    if curl -sf -X POST -H "X-Reindex-Token: $REINDEX_TOKEN" \
        "$API_BASE/api/reindex" >/dev/null 2>&1; then
      echo "[syncer] reindex 完成"
    else
      echo "[syncer] reindex 失败" >&2
      [ "$sync_status" = "success" ] && sync_status="reindex_failed"
    fi

    # 6. 触发 Quartz 重建
    echo "[syncer] 3/3 触发 Quartz 构建"
    if cd "$PROJECT_DIR" && docker compose run --rm -T web; then
      echo "[syncer] Quartz 构建完成"
    else
      echo "[syncer] Quartz 构建失败" >&2
      [ "$sync_status" = "success" ] && sync_status="build_failed"
    fi

    # 7. 清除 pending，更新状态
    last_sync_at=$(python3 -c "from datetime import datetime; print(datetime.now().isoformat())")
    curl -sf -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"pending\": false, \"syncing\": false, \"last_sync_at\": \"$last_sync_at\", \"last_sync_status\": \"$sync_status\"}" \
      "$API_BASE/admin/api/sync-status" >/dev/null 2>&1 || true

    echo "[syncer] 同步流程结束: $sync_status"
  fi

  sleep "$POLL_INTERVAL"
done
