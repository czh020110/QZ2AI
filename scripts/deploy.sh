#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$PROJECT_DIR/.env"
cd "$PROJECT_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[deploy] 缺少环境文件: $ENV_FILE" >&2
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
LAST_HEALTH_ERROR=""

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
CURL_CONNECT_TIMEOUT_SECONDS="${CURL_CONNECT_TIMEOUT_SECONDS:-$(read_env_value SCRIPT_HTTP_CONNECT_TIMEOUT_SECONDS)}"
CURL_CONNECT_TIMEOUT_SECONDS="${CURL_CONNECT_TIMEOUT_SECONDS:-5}"
HEALTH_MAX_TIME_SECONDS="${HEALTH_MAX_TIME_SECONDS:-$(read_env_value SCRIPT_HEALTH_MAX_TIME_SECONDS)}"
HEALTH_MAX_TIME_SECONDS="${HEALTH_MAX_TIME_SECONDS:-15}"
ROOT_MAX_TIME_SECONDS="${ROOT_MAX_TIME_SECONDS:-$(read_env_value SCRIPT_ROOT_MAX_TIME_SECONDS)}"
ROOT_MAX_TIME_SECONDS="${ROOT_MAX_TIME_SECONDS:-15}"
HEALTH_RETRIES="${HEALTH_RETRIES:-$(read_env_value DEPLOY_HEALTH_RETRIES)}"
HEALTH_RETRIES="${HEALTH_RETRIES:-60}"
HEALTH_INTERVAL_SECONDS="${HEALTH_INTERVAL_SECONDS:-$(read_env_value DEPLOY_HEALTH_INTERVAL_SECONDS)}"
HEALTH_INTERVAL_SECONDS="${HEALTH_INTERVAL_SECONDS:-2}"
BUILD_RETRIES="${BUILD_RETRIES:-$(read_env_value DEPLOY_BUILD_RETRIES)}"
BUILD_RETRIES="${BUILD_RETRIES:-300}"
BUILD_INTERVAL_SECONDS="${BUILD_INTERVAL_SECONDS:-$(read_env_value DEPLOY_BUILD_INTERVAL_SECONDS)}"
BUILD_INTERVAL_SECONDS="${BUILD_INTERVAL_SECONDS:-2}"

for cmd in git docker curl python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[deploy] 缺少命令: $cmd" >&2
    exit 1
  fi
done

health_is_ready() {
  if ! http_request "$HEALTH_MAX_TIME_SECONDS" "$API_BASE/api/health"; then
    LAST_HEALTH_ERROR="$HTTP_RESPONSE_BODY"
    return 1
  fi

  if [ "$HTTP_RESPONSE_CODE" -lt 200 ] || [ "$HTTP_RESPONSE_CODE" -ge 300 ]; then
    LAST_HEALTH_ERROR="HTTP $HTTP_RESPONSE_CODE: $HTTP_RESPONSE_BODY"
    return 1
  fi

  if ! HEALTH_RESPONSE="$HTTP_RESPONSE_BODY" python3 - <<'PY'
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
    LAST_HEALTH_ERROR="$HTTP_RESPONSE_BODY"
    return 1
  fi

  LAST_HEALTH_ERROR=""
  return 0
}

wait_for_health() {
  local attempt
  for ((attempt = 1; attempt <= HEALTH_RETRIES; attempt++)); do
    if health_is_ready; then
      echo "[deploy] API 健康检查通过"
      return 0
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
  done

  if [ -n "$LAST_HEALTH_ERROR" ]; then
    echo "[deploy] API 健康检查超时，最后一次结果: $LAST_HEALTH_ERROR" >&2
  else
    echo "[deploy] API 健康检查超时" >&2
  fi
  return 1
}

wait_for_web_exit() {
  local attempt
  local container_id
  local state

  for ((attempt = 1; attempt <= BUILD_RETRIES; attempt++)); do
    container_id="$(docker compose ps -aq web 2>/dev/null | head -n 1 || true)"
    if [ -n "$container_id" ]; then
      state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$container_id" 2>/dev/null || true)"
      case "$state" in
        "exited 0")
          echo "[deploy] web 构建容器已成功退出"
          return 0
          ;;
        exited*)
          echo "[deploy] web 构建失败: $state" >&2
          return 1
          ;;
      esac
    fi
    sleep "$BUILD_INTERVAL_SECONDS"
  done

  echo "[deploy] 等待 web 构建容器退出超时" >&2
  return 1
}

if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
  echo "[deploy] 工作区存在未提交的已跟踪改动，拒绝执行 git pull" >&2
  git status --short >&2
  exit 1
fi

echo "[deploy] 拉取最新代码"
git pull --ff-only

echo "[deploy] 重新构建并启动 compose 服务"
docker compose up -d --build

wait_for_health
wait_for_web_exit

if ! http_request "$ROOT_MAX_TIME_SECONDS" "$API_BASE/"; then
  echo "[deploy] 首页探活失败: $HTTP_RESPONSE_BODY" >&2
  exit 1
fi

if [ "$HTTP_RESPONSE_CODE" -lt 200 ] || [ "$HTTP_RESPONSE_CODE" -ge 300 ]; then
  echo "[deploy] 首页探活返回 HTTP $HTTP_RESPONSE_CODE: $HTTP_RESPONSE_BODY" >&2
  exit 1
fi

echo "[deploy] 发布完成"
