#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$PROJECT_DIR/.env"
TARGET_DIR="$PROJECT_DIR/data/notes"
BACKUP_DIR="$PROJECT_DIR/data/logs/cos-sync-backup"
TARGET_DIR_REAL="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$TARGET_DIR")"
cd "$PROJECT_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[sync] 缺少环境文件: $ENV_FILE" >&2
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

COSCLI_BIN="${COSCLI_BIN:-$(read_env_value COSCLI_BIN)}"
COSCLI_BIN="${COSCLI_BIN:-coscli}"
COSCLI_CONFIG_PATH="${COSCLI_CONFIG_PATH:-$(read_env_value COSCLI_CONFIG_PATH)}"
COS_SECRET_ID="${COS_SECRET_ID:-$(read_env_value COS_SECRET_ID)}"
COS_SECRET_KEY="${COS_SECRET_KEY:-$(read_env_value COS_SECRET_KEY)}"
COS_REGION="${COS_REGION:-$(read_env_value COS_REGION)}"
COS_ENDPOINT="${COS_ENDPOINT:-$(read_env_value COS_ENDPOINT)}"
COS_BUCKET="${COS_BUCKET:-$(read_env_value COS_BUCKET)}"
NOTES_COS_PREFIX="${NOTES_COS_PREFIX:-$(read_env_value NOTES_COS_PREFIX)}"
COS_SYNC_SOURCE="${COS_SYNC_SOURCE:-$(read_env_value COS_SYNC_SOURCE)}"
COS_ENDPOINT="${COS_ENDPOINT#https://}"
COS_ENDPOINT="${COS_ENDPOINT#http://}"
COS_ENDPOINT="${COS_ENDPOINT%/}"

# 若已有完整 COS_SYNC_SOURCE 则直接使用；否则根据 COS_BUCKET + NOTES_COS_PREFIX 拼接
if [ -z "$COS_SYNC_SOURCE" ] && [ -n "$COS_BUCKET" ] && [ -n "$NOTES_COS_PREFIX" ]; then
  NOTES_COS_PREFIX="${NOTES_COS_PREFIX#/}"
  NOTES_COS_PREFIX="${NOTES_COS_PREFIX%/}"
  COS_SYNC_SOURCE="cos://${COS_BUCKET}/${NOTES_COS_PREFIX}/"
  echo "[sync] 根据 NOTES_COS_PREFIX 拼出同步源: $COS_SYNC_SOURCE"
fi

if ! command -v "$COSCLI_BIN" >/dev/null 2>&1 && [ ! -x "$COSCLI_BIN" ]; then
  echo "[sync] 未找到 coscli 可执行文件: $COSCLI_BIN" >&2
  exit 1
fi

if [ -z "$COS_SYNC_SOURCE" ]; then
  echo "[sync] 缺少 COS_SYNC_SOURCE" >&2
  exit 1
fi

case "$COS_SYNC_SOURCE" in
  cos://*/)
    ;;
  cos://*)
    echo "[sync] COS_SYNC_SOURCE 需要使用目录前缀并以 / 结尾，例如 cos://bucket/notes/" >&2
    exit 1
    ;;
  *)
    echo "[sync] COS_SYNC_SOURCE 必须以 cos:// 开头；sync.sh 只允许从 COS 拉取到本地，不支持本地上传到远程" >&2
    exit 1
    ;;
esac

sync_config_path="$COSCLI_CONFIG_PATH"
temp_config_path=""

cleanup_temp_config() {
  if [ -n "$temp_config_path" ] && [ -f "$temp_config_path" ]; then
    rm -f "$temp_config_path"
  fi
}

trap cleanup_temp_config EXIT

if [ -n "$sync_config_path" ]; then
  if [ ! -f "$sync_config_path" ]; then
    echo "[sync] coscli 配置文件不存在: $sync_config_path" >&2
    exit 1
  fi
else
  missing_vars=()
  [ -n "$COS_SECRET_ID" ] || missing_vars+=("COS_SECRET_ID")
  [ -n "$COS_SECRET_KEY" ] || missing_vars+=("COS_SECRET_KEY")
  [ -n "$COS_REGION" ] || missing_vars+=("COS_REGION")
  [ -n "$COS_ENDPOINT" ] || missing_vars+=("COS_ENDPOINT")
  [ -n "$COS_BUCKET" ] || missing_vars+=("COS_BUCKET")

  if [ "${#missing_vars[@]}" -gt 0 ]; then
    echo "[sync] 缺少变量: ${missing_vars[*]}" >&2
    exit 1
  fi

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

mkdir -p "$TARGET_DIR" "$BACKUP_DIR"

target_dir_after_create="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$TARGET_DIR")"
if [ "$target_dir_after_create" != "$TARGET_DIR_REAL" ]; then
  echo "[sync] 目标目录解析异常: $TARGET_DIR" >&2
  exit 1
fi

echo "[sync] 当前脚本仅执行 COS -> 本地 单向拉取，远端前缀: $COS_SYNC_SOURCE"
echo "[sync] 如遇同名冲突，将以远端内容覆盖本地: $TARGET_DIR"
echo "[sync] 本地多余文件会按远端镜像规则移入备份目录: $BACKUP_DIR"
"$COSCLI_BIN" sync "$COS_SYNC_SOURCE" "$TARGET_DIR" -r --delete --backup-dir "$BACKUP_DIR" --force -c "$sync_config_path"
echo "[sync] 同步完成"
