#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$PROJECT_DIR/.env"
cd "$PROJECT_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[rebuild] 缺少环境文件: $ENV_FILE" >&2
  exit 1
fi

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

HTTP_RESPONSE_BODY=""
HTTP_RESPONSE_CODE=""

http_request() {
  local max_time="$1"
  shift

  local response
  local curl_status

  set +e
  response="$(curl --silent --show-error --connect-timeout "$CURL_CONNECT_TIMEOUT_SECONDS" --max-time "$max_time" --write-out $'\n%{http_code}' "$@" 2>&1)"
  curl_status=$?
  set -e

  if [ "$curl_status" -ne 0 ]; then
    HTTP_RESPONSE_BODY="$response"
    HTTP_RESPONSE_CODE=""
    return "$curl_status"
  fi

  HTTP_RESPONSE_CODE="${response##*$'\n'}"
  HTTP_RESPONSE_BODY="${response%$'\n'*}"
  return 0
}

API_BASE="${API_BASE:-$(read_env_value API_BASE)}"
API_BASE="${API_BASE:-http://127.0.0.1}"
API_BASE="${API_BASE%/}"
REINDEX_TOKEN="${REINDEX_TOKEN:-$(read_env_value REINDEX_TOKEN)}"
CURL_CONNECT_TIMEOUT_SECONDS="${CURL_CONNECT_TIMEOUT_SECONDS:-$(read_env_value SCRIPT_HTTP_CONNECT_TIMEOUT_SECONDS)}"
CURL_CONNECT_TIMEOUT_SECONDS="${CURL_CONNECT_TIMEOUT_SECONDS:-5}"
HEALTH_MAX_TIME_SECONDS="${HEALTH_MAX_TIME_SECONDS:-$(read_env_value SCRIPT_HEALTH_MAX_TIME_SECONDS)}"
HEALTH_MAX_TIME_SECONDS="${HEALTH_MAX_TIME_SECONDS:-15}"
REINDEX_MAX_TIME_SECONDS="${REINDEX_MAX_TIME_SECONDS:-$(read_env_value SCRIPT_REINDEX_MAX_TIME_SECONDS)}"
REINDEX_MAX_TIME_SECONDS="${REINDEX_MAX_TIME_SECONDS:-1800}"

for cmd in curl docker python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[rebuild] 缺少命令: $cmd" >&2
    exit 1
  fi
done

if [ -z "$REINDEX_TOKEN" ]; then
  echo "[rebuild] 缺少 REINDEX_TOKEN" >&2
  exit 1
fi

if ! http_request "$HEALTH_MAX_TIME_SECONDS" "$API_BASE/api/health"; then
  echo "[rebuild] 请求 $API_BASE/api/health 失败: $HTTP_RESPONSE_BODY" >&2
  exit 1
fi

if [ "$HTTP_RESPONSE_CODE" -lt 200 ] || [ "$HTTP_RESPONSE_CODE" -ge 300 ]; then
  echo "[rebuild] API 健康检查返回 HTTP $HTTP_RESPONSE_CODE: $HTTP_RESPONSE_BODY" >&2
  exit 1
fi

health_response="$HTTP_RESPONSE_BODY"
if ! HEALTH_RESPONSE="$health_response" python3 - <<'PY'
import json
import os
import sys

try:
    data = json.loads(os.environ["HEALTH_RESPONSE"])
except json.JSONDecodeError:
    sys.exit(2)

if data.get("status") != "ok" or not data.get("notes_dir_exists"):
    sys.exit(1)
PY
then
  echo "[rebuild] API 健康检查未通过: $health_response" >&2
  exit 1
fi

if ! http_request "$REINDEX_MAX_TIME_SECONDS" -X POST "$API_BASE/api/reindex" -H "X-Reindex-Token: $REINDEX_TOKEN"; then
  echo "[rebuild] 请求 $API_BASE/api/reindex 失败: $HTTP_RESPONSE_BODY" >&2
  exit 1
fi

if [ "$HTTP_RESPONSE_CODE" -lt 200 ] || [ "$HTTP_RESPONSE_CODE" -ge 300 ]; then
  echo "[rebuild] reindex 返回 HTTP $HTTP_RESPONSE_CODE: $HTTP_RESPONSE_BODY" >&2
  exit 1
fi

reindex_response="$HTTP_RESPONSE_BODY"
stats="$({ REINDEX_RESPONSE="$reindex_response" python3 - <<'PY'
import json
import os

try:
    data = json.loads(os.environ["REINDEX_RESPONSE"])
except json.JSONDecodeError:
    raise SystemExit("invalid_json")
print(int(data.get("processed", 0)), int(data.get("deleted", 0)), int(data.get("skipped", 0)))
PY
} )"
read -r processed deleted skipped <<<"$stats"

echo "[rebuild] reindex 完成: processed=$processed skipped=$skipped deleted=$deleted"

if [ "$processed" -eq 0 ] && [ "$deleted" -eq 0 ]; then
  echo "[rebuild] 内容无变化，跳过 Quartz 构建"
  exit 0
fi

echo "[rebuild] 开始执行 Quartz 构建"
docker compose run --rm -T web
echo "[rebuild] 构建完成"
