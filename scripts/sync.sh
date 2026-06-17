#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$PROJECT_DIR/.env"
TARGET_DIR="$PROJECT_DIR/data/notes"
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
COS_SYNC_SOURCE="${COS_SYNC_SOURCE:-$(read_env_value COS_SYNC_SOURCE)}"

if ! command -v "$COSCLI_BIN" >/dev/null 2>&1 && [ ! -x "$COSCLI_BIN" ]; then
  echo "[sync] 未找到 coscli 可执行文件: $COSCLI_BIN" >&2
  exit 1
fi

if [ -z "$COSCLI_CONFIG_PATH" ]; then
  echo "[sync] 缺少 COSCLI_CONFIG_PATH" >&2
  exit 1
fi

if [ ! -f "$COSCLI_CONFIG_PATH" ]; then
  echo "[sync] coscli 配置文件不存在: $COSCLI_CONFIG_PATH" >&2
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
    echo "[sync] COS_SYNC_SOURCE 必须以 cos:// 开头" >&2
    exit 1
    ;;
esac

mkdir -p "$TARGET_DIR"

echo "[sync] 开始同步 $COS_SYNC_SOURCE -> $TARGET_DIR"
"$COSCLI_BIN" sync "$COS_SYNC_SOURCE" "$TARGET_DIR" -r --delete --force -c "$COSCLI_CONFIG_PATH"
echo "[sync] 同步完成"
