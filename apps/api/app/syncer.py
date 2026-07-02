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


def _github_sync(settings) -> tuple[bool, bool]:
    """执行 GitHub 只读同步，返回 (成功, 有变更)"""
    if not settings.github_repo_url:
        logger.info("未配置 GitHub 仓库 URL，跳过")
        return True, False

    notes_dir = Path(settings.notes_dir)
    git_dir = notes_dir / ".git"

    # 代理通过 git -c http.proxy 传入，作用域仅单条命令，不污染全局环境
    git_env = os.environ.copy()
    git_base = ["git"]
    if settings.git_proxy:
        git_base += ["-c", f"http.proxy={settings.git_proxy}", "-c", f"https.proxy={settings.git_proxy}"]
        logger.info("git 使用代理: %s", settings.git_proxy)

    # 构造仓库 URL：可选加速镜像前缀 + token 认证。
    # GitHub 已禁用 https://TOKEN@github.com 旧格式，必须用 x-access-token 用户名，
    # 否则 git 会把 TOKEN 当用户名并交互式要求输入密码，容器内无 tty 导致克隆失败。
    repo_url = settings.github_repo_url
    if settings.git_accelerator and repo_url.startswith("https://github.com/"):
        repo_url = settings.git_accelerator.rstrip("/") + "/" + repo_url
        logger.info("使用加速镜像: %s", settings.git_accelerator)
    if settings.github_token and "github.com" in repo_url:
        if repo_url.startswith("https://github.com/"):
            repo_url = repo_url.replace("https://github.com/", f"https://x-access-token:{settings.github_token}@github.com/")
            logger.info("使用 GitHub Token 认证")

    try:
        # 检查是否已经是 git 仓库
        if not git_dir.exists():
            # 首次克隆：git clone 要求目标目录为空或不存在。
            # 从 COS 等其他来源切换到 GitHub 时，notes_dir 可能已残留旧笔记，
            # 需先清空目录（保留目录本身）再克隆，否则 clone 会报 "destination path already exists"
            if notes_dir.exists():
                import shutil
                for item in notes_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                logger.info("首次克隆前清空残留目录: %s", notes_dir)
            notes_dir.mkdir(parents=True, exist_ok=True)
            logger.info("首次克隆 GitHub 仓库: %s → %s", settings.github_repo_url, notes_dir)
            cmd = git_base + [
                "clone", "--depth", "1",
                "--branch", settings.github_branch,
                repo_url, str(notes_dir)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=git_env)
            if result.returncode != 0:
                logger.error("GitHub 克隆失败: %s", result.stderr[:500])
                return False, False
            logger.info("GitHub 克隆完成")
            return True, True  # 首次克隆视为有变更

        # 已存在仓库，执行拉取
        logger.info("拉取 GitHub 仓库更新: %s", settings.github_repo_url)

        # 丢弃本地变更（只读模式）
        reset_cmd = git_base + ["-C", str(notes_dir), "reset", "--hard", "HEAD"]
        subprocess.run(reset_cmd, capture_output=True, text=True, timeout=30, env=git_env)

        # 清理未跟踪文件
        clean_cmd = git_base + ["-C", str(notes_dir), "clean", "-fd"]
        subprocess.run(clean_cmd, capture_output=True, text=True, timeout=30, env=git_env)

        # 更新 remote URL（以防 token 变更）
        if settings.github_token:
            set_url_cmd = git_base + ["-C", str(notes_dir), "remote", "set-url", "origin", repo_url]
            subprocess.run(set_url_cmd, capture_output=True, text=True, timeout=10, env=git_env)
        # 记录拉取前的 commit
        old_commit_cmd = git_base + ["-C", str(notes_dir), "rev-parse", "HEAD"]
        old_result = subprocess.run(old_commit_cmd, capture_output=True, text=True, timeout=10, env=git_env)
        old_commit = old_result.stdout.strip() if old_result.returncode == 0 else ""

        # 拉取最新代码
        pull_cmd = git_base + ["-C", str(notes_dir), "pull", "--rebase", "origin", settings.github_branch]
        result = subprocess.run(pull_cmd, capture_output=True, text=True, timeout=600, env=git_env)
        if result.returncode != 0:
            logger.error("GitHub 拉取失败: %s", result.stderr[:500])
            return False, False

        # 记录拉取后的 commit
        new_commit_cmd = git_base + ["-C", str(notes_dir), "rev-parse", "HEAD"]
        new_result = subprocess.run(new_commit_cmd, capture_output=True, text=True, timeout=10, env=git_env)
        new_commit = new_result.stdout.strip() if new_result.returncode == 0 else ""

        changed = (old_commit != new_commit)
        logger.info("GitHub 拉取完成，有变更: %s (旧: %s, 新: %s)", changed, old_commit[:8], new_commit[:8])

        # 如果配置了子目录，需要处理文件移动
        if changed and settings.notes_github_prefix:
            subdir = notes_dir / settings.notes_github_prefix.strip("/")
            if subdir.exists() and subdir != notes_dir:
                logger.info("处理子目录: %s", settings.notes_github_prefix)
                # 临时移动文件（保留 .git）
                import tempfile
                import shutil
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_path = Path(tmpdir) / "notes"
                    shutil.move(str(subdir), str(tmp_path))
                    # 清理 notes_dir 除了 .git 外的所有文件
                    for item in notes_dir.iterdir():
                        if item.name != ".git":
                            if item.is_dir():
                                shutil.rmtree(item)
                            else:
                                item.unlink()
                    # 移动回来
                    for item in tmp_path.iterdir():
                        shutil.move(str(item), str(notes_dir / item.name))

        return True, changed
    except Exception as e:
        logger.error("GitHub 同步异常: %s", e)
        return False, False


def _remote_sync(settings) -> tuple[bool, bool]:
    """根据配置的远程类型执行同步，返回 (成功, 有变更)"""
    if settings.remote_type == "github":
        return _github_sync(settings)
    elif settings.remote_type == "cos":
        return _coscli_sync(settings)
    else:
        logger.error("未知的远程类型: %s", settings.remote_type)
        return False, False


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
    logger.info("syncer 线程启动，轮询间隔: %ds", POLL_INTERVAL)

    # 启动时恢复卡死的状态：如果 syncing=true 但进程已重启，说明上次同步中断，重置
    startup_state = _read_state()
    if startup_state.get("syncing"):
        logger.warning("检测到卡死状态（syncing=true），重置为待同步")
        startup_state["syncing"] = False
        startup_state["pending"] = True
        startup_state["debounce_until"] = 0
        _write_state(startup_state)

    while True:
        settings = get_settings()
        try:
            state = _read_state()
            pending = state.get("pending", False)
            syncing = state.get("syncing", False)
            debounce_until = state.get("debounce_until", 0)

            # 定时同步：需同时满足 auto_sync_enabled 开启 且 interval > 0
            if settings.auto_sync_enabled:
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
                # 安全网：同步状态卡死超过 10 分钟自动重置
                if syncing and state.get("syncing_started_at", 0):
                    elapsed = time.time() - state["syncing_started_at"]
                    if elapsed > 600:
                        logger.warning("同步超时（%.0fs），重置卡死状态", elapsed)
                        state["syncing"] = False
                        state["syncing_started_at"] = 0
                        syncing = False
                        _write_state(state)
                if not pending or syncing or time.time() < debounce_until:
                    time.sleep(POLL_INTERVAL)
                    continue

            logger.info("检测到待同步任务，防抖窗口已过期，开始执行")
            state["syncing"] = True
            state["syncing_started_at"] = time.time()
            _write_state(state)

            sync_status = "success"

            # 1. 远程同步（COS 或 GitHub）
            ok, changed = _remote_sync(settings)
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
            state["syncing_started_at"] = 0
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
