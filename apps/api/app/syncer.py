import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import get_settings

logger = logging.getLogger("syncer")

SYNC_PENDING_PATH = Path("/data/sync-pending.json")
POLL_INTERVAL = 10


def _read_state() -> dict:
    try:
        return json.loads(SYNC_PENDING_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "pending": False, "syncing": False, "debounce_until": 0,
            "triggered_at": "", "event_count": 0,
            "last_sync_at": "", "last_sync_status": "",
        }


def _write_state(state: dict) -> None:
    import fcntl
    with open(SYNC_PENDING_PATH, "a+") as f:
        f.seek(0)
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            json.dump(state, f, ensure_ascii=False)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _coscli_sync(settings) -> tuple[bool, bool]:
    """执行 coscli sync，返回 (成功, 有变更)"""
    cos_sync_source = ""
    if settings.cos_bucket and settings.notes_cos_prefix:
        prefix = settings.notes_cos_prefix.strip("/")
        cos_sync_source = f"cos://{settings.cos_bucket}/{prefix}/"

    if not cos_sync_source:
        logger.info("未配置 COS 同步源，跳过")
        return True, False

    # 生成临时 coscli 配置
    import tempfile
    config_content = f"""cos:
  base:
    secretid: {json.dumps(settings.cos_secret_id)}
    secretkey: {json.dumps(settings.cos_secret_key)}
    sessiontoken: ""
    protocol: https
  buckets:
    - name: {json.dumps(settings.cos_bucket)}
      alias: "default"
      region: {json.dumps(settings.cos_region)}
      endpoint: {json.dumps(settings.cos_endpoint)}
      ofs: false
"""
    config_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            config_file = f.name

        notes_dir = Path(settings.notes_dir)
        notes_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = Path("/data/logs/cos-sync-backup")
        backup_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "coscli", "sync", cos_sync_source, str(notes_dir),
            "-r", "--delete", "--backup-dir", str(backup_dir),
            "--force", "-c", config_file,
        ]
        logger.info("执行 COS 同步: %s → %s", cos_sync_source, notes_dir)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error("COS 同步失败: %s", result.stderr[:500])
            return False, False
        changed = bool(result.stdout.strip())
        logger.info("COS 同步完成，有变更: %s", changed)
        return True, changed
    except Exception as e:
        logger.error("COS 同步异常: %s", e)
        return False, False
    finally:
        if config_file:
            try:
                os.unlink(config_file)
            except OSError:
                pass


def _trigger_reindex() -> bool:
    """直接调用 reindex()，不需要走 HTTP"""
    try:
        from .indexer import reindex
        result = reindex()
        logger.info("reindex 完成: processed=%d deleted=%d", result.get("processed", 0), result.get("deleted", 0))
        return True
    except Exception as e:
        logger.error("reindex 失败: %s", e)
        return False


def _trigger_web_rebuild() -> bool:
    """执行 docker compose run --rm -T web"""
    try:
        project_dir = os.environ.get("PROJECT_DIR", "/project")
        cmd = ["docker", "compose", "run", "--rm", "-T", "web"]
        logger.info("触发 Quartz 构建")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=project_dir)
        if result.returncode != 0:
            logger.error("Quartz 构建失败: %s", result.stderr[:500])
            return False
        logger.info("Quartz 构建完成")
        return True
    except Exception as e:
        logger.error("Quartz 构建异常: %s", e)
        return False


def _syncer_loop() -> None:
    """syncer 后台线程主循环"""
    settings = get_settings()
    logger.info("syncer 线程启动，轮询间隔: %ds", POLL_INTERVAL)

    while True:
        try:
            state = _read_state()
            pending = state.get("pending", False)
            syncing = state.get("syncing", False)
            debounce_until = state.get("debounce_until", 0)

            # 定时同步：距上次同步超过间隔则自动触发
            interval = settings.sync_interval_seconds
            if interval > 0 and not pending and not syncing:
                last_sync_at = state.get("last_sync_at", "")
                if last_sync_at:
                    from datetime import timezone
                    last_dt = datetime.fromisoformat(last_sync_at)
                    elapsed = (datetime.now() - last_dt).total_seconds()
                else:
                    elapsed = interval  # 从未同步过，立即触发
                if elapsed >= interval:
                    logger.info("定时同步触发，距上次同步 %.0fs", elapsed)
                    state["pending"] = True
                    state["debounce_until"] = 0
                    _write_state(state)
                    pending = True

            if not pending or syncing or time.time() < debounce_until:
                time.sleep(POLL_INTERVAL)
                continue

            logger.info("检测到待同步任务，防抖窗口已过期，开始执行")
            state["syncing"] = True
            _write_state(state)

            sync_status = "success"

            # 1. COS 同步
            ok, changed = _coscli_sync(settings)
            if not ok:
                sync_status = "sync_failed"

            if ok and not changed:
                logger.info("无文件变更，跳过 reindex 和构建")
            else:
                # 2. 索引重建
                if not _trigger_reindex():
                    if sync_status == "success":
                        sync_status = "reindex_failed"

                # 3. Quartz 重建
                if not _trigger_web_rebuild():
                    if sync_status == "success":
                        sync_status = "build_failed"

            # 4. 清除 pending
            state = _read_state()
            state["pending"] = False
            state["syncing"] = False
            state["last_sync_at"] = datetime.now().isoformat()
            state["last_sync_status"] = sync_status
            _write_state(state)

            logger.info("同步流程结束: %s", sync_status)
        except Exception as e:
            logger.error("syncer 循环异常: %s", e)

        time.sleep(POLL_INTERVAL)


def start_syncer_thread() -> None:
    """启动 syncer 后台线程（由 FastAPI startup 事件调用）"""
    t = threading.Thread(target=_syncer_loop, name="syncer", daemon=True)
    t.start()
