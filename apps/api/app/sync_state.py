"""同步状态 I/O：对 /data/sync-pending.json 的读写抽象（跨进程共享，抗 syncer 线程与管理 API 并发）。

admin.py 同步 webhook 入口写入 pending 标记；syncer.py 后台线程读取并执行同步。
两者必须共用同一套文件锁，并基于 defaults 填充缺失字段（兼容手工编辑的 JSON）。
"""
import fcntl
import json
from pathlib import Path

SYNC_PENDING_PATH = Path("/data/sync-pending.json")

_SYNC_STATE_DEFAULTS = {
    "pending": False,
    "syncing": False,
    "building": False,
    "content_visibility_pending": False,
    "manual_trigger": False,
    "debounce_until": 0,
    "triggered_at": "",
    "event_count": 0,
    "last_sync_at": "",
    "last_sync_status": "",
    "last_sync_detail": "",
}


def read_sync_state() -> dict:
    # 加共享锁读取，避免与 write_sync_state 的 truncate+rewrite 窗口碰撞读到半截 JSON
    try:
        with open(SYNC_PENDING_PATH, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                raw = json.loads(f.read())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_SYNC_STATE_DEFAULTS)
    # 用 defaults 填充，兼容旧文件缺少新字段（如 manual_trigger）
    full = dict(_SYNC_STATE_DEFAULTS)
    full.update(raw)
    return full


def write_sync_state(state: dict) -> None:
    with open(SYNC_PENDING_PATH, "a+") as f:
        f.seek(0)
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            json.dump(state, f, ensure_ascii=False)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
