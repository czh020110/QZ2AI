import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .sync_state import read_sync_state, write_sync_state

logger = logging.getLogger("syncer")

POLL_INTERVAL = 10
SYNC_DETAIL_LIMIT = 500


def _clip_sync_detail(detail: str, *secrets: str) -> str:
    text = " ".join(str(detail or "").split())
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text[:SYNC_DETAIL_LIMIT]


def _with_sync_detail(prefix: str, detail: str, *secrets: str) -> str:
    base = _clip_sync_detail(detail, *secrets)
    if not prefix:
        return base
    if not base:
        return prefix
    return _clip_sync_detail(f"{prefix}: {base}", *secrets)


def _resolve_compose_project() -> str:
    """探测 blog 容器所属的 compose project name，让 syncer 触发的 web 构建容器与 blog 共享命名卷。

    blog 容器由宿主机 `docker compose up` 启动，命名卷名为 <project>_public；syncer 在容器内
    `cd /project && docker compose run` 默认用 cwd 目录名作 project，导致 project_public != blog_public，
    构建产物写不到 nginx 托管的卷。从自身容器 label com.docker.compose.project 读取，与 blog 对齐。
    """
    hostname = os.environ.get("HOSTNAME", "")
    if not hostname:
        logger.warning("HOSTNAME 环境变量为空，无法探测 compose project name，卷名可能不匹配")
        return ""
    try:
        result = subprocess.run(
            ["docker", "inspect", hostname,
             "--format", "{{ index .Config.Labels \"com.docker.compose.project\" }}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            name = result.stdout.strip()
            if name:
                return name
    except Exception as e:
        logger.warning("探测 compose project name 失败: %s", e)
    return ""


def _resolve_host_data_dir() -> str:
    """探测宿主机 data 目录路径，供 compose 把 web 容器的 notes bind 到宿主机真实路径。

    syncer 在容器内通过 docker socket 调 `docker compose run web`，compose 在容器内
    解析 ./data/notes 得到容器路径，但真正执行 bind 的宿主机 daemon 端没有这个路径，
    会把 web 容器 bind 到空目录、构建 0 篇文章却 exit 0。
    通过 docker inspect 自身容器拿 /data 的宿主机 source（如 /home/ubuntu/Blog/data），
    注入 HOST_NOTES_DIR 环境变量，compose.yaml 用 ${HOST_NOTES_DIR}/notes 作为绝对路径 bind。
    """
    hostname = os.environ.get("HOSTNAME", "")
    if not hostname:
        logger.warning("HOSTNAME 环境变量为空，无法探测宿主机 data 路径")
        return ""
    try:
        result = subprocess.run(
            ["docker", "inspect", hostname,
             "--format", "{{range .Mounts}}{{.Destination}}={{.Source}}{{println}}{{end}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return ""
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            dst, _, src = line.partition("=")
            if dst.strip() == "/data":
                return src.strip()
    except Exception as e:
        logger.warning("探测宿主机 data 路径失败: %s", e)
    return ""


def _coscli_sync(settings) -> tuple[bool, bool, str]:
    """执行 coscli sync，返回 (成功, 有变更, 失败详情)"""
    cos_sync_source = ""
    if settings.cos_bucket:
        cos_sync_source = f"cos://{settings.cos_bucket}/"

    if not cos_sync_source:
        logger.info("未配置 COS 同步源，跳过")
        return True, False, ""

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

        cmd = [
            "coscli", "sync", cos_sync_source, str(notes_dir),
            "-r", "--delete", "--force", "-c", config_file,
        ]
        logger.info("执行 COS 同步: %s → %s", cos_sync_source, notes_dir)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            detail = _with_sync_detail("COS 同步失败", result.stderr or result.stdout, settings.cos_secret_id, settings.cos_secret_key)
            logger.error("COS 同步失败: %s", detail)
            return False, False, detail
        changed = bool(result.stdout.strip())
        logger.info("COS 同步完成，有变更: %s", changed)
        return True, changed, ""
    except Exception as e:
        detail = _with_sync_detail("COS 同步异常", str(e), settings.cos_secret_id, settings.cos_secret_key)
        logger.error("COS 同步异常: %s", detail)
        return False, False, detail
    finally:
        if config_file:
            try:
                os.unlink(config_file)
            except OSError:
                pass


def _build_repo_url(settings, accelerator: str = "") -> str:
    """构造 GitHub 仓库 URL：可选加速镜像前缀 + token 认证。

    accelerator 为空时走直连；非空时把完整的 GitHub HTTPS URL 包装到镜像前缀后。
    GitHub 已禁用 https://TOKEN@github.com 旧格式，必须用 x-access-token 用户名。
    """
    repo_url = settings.github_repo_url
    if settings.github_token and repo_url.startswith("https://github.com/"):
        repo_url = repo_url.replace("https://github.com/", f"https://x-access-token:{settings.github_token}@github.com/", 1)
    if accelerator and settings.github_repo_url.startswith("https://github.com/"):
        repo_url = accelerator.rstrip("/") + "/" + repo_url
    return repo_url


def _run_pull(git_base, notes_dir, branch, git_env) -> subprocess.CompletedProcess:
    """执行一次 git pull --rebase 从已配置的 remote origin 拉取。"""
    pull_cmd = git_base + ["-C", str(notes_dir), "pull", "--rebase", "origin", branch]
    return subprocess.run(pull_cmd, capture_output=True, text=True, timeout=600, env=git_env)


def _pull_with_fallback(settings, git_base, notes_dir, git_env) -> tuple[bool, str]:
    """拉取 GitHub 仓库更新，失败时按加速源顺序兜底重试。"""
    branch = settings.github_branch
    direct_url = _build_repo_url(settings)
    last_detail = ""

    def set_origin(url: str) -> None:
        set_url_cmd = git_base + ["-C", str(notes_dir), "remote", "set-url", "origin", url]
        subprocess.run(set_url_cmd, capture_output=True, text=True, timeout=10, env=git_env)

    # 每次都同步 remote 到当前候选 URL（无 token 时也要切，否则加速镜像对公开仓库不生效）
    set_origin(direct_url)

    result = _run_pull(git_base, notes_dir, branch, git_env)
    if result.returncode == 0:
        return True, ""
    last_detail = _with_sync_detail("GitHub 直连拉取失败", result.stderr or result.stdout, settings.github_token)
    logger.warning("GitHub 直连拉取失败: %s", _clip_sync_detail(result.stderr or result.stdout, settings.github_token))

    if settings.git_accelerator:
        accel_url = _build_repo_url(settings, settings.git_accelerator)
        logger.warning("重试：使用配置的加速镜像 %s", settings.git_accelerator)
        set_origin(accel_url)
        result = _run_pull(git_base, notes_dir, branch, git_env)
        if result.returncode == 0:
            logger.info("加速镜像 %s 拉取成功", settings.git_accelerator)
            return True, ""
        last_detail = _with_sync_detail(f"加速镜像 {settings.git_accelerator} 拉取失败", result.stderr or result.stdout, settings.github_token)
        logger.warning("加速镜像 %s 拉取失败: %s", settings.git_accelerator, _clip_sync_detail(result.stderr or result.stdout, settings.github_token))

    hardcoded = "https://ghfast.top"
    if hardcoded != settings.git_accelerator:
        hard_url = _build_repo_url(settings, hardcoded)
        logger.warning("重试：使用硬编码加速 %s", hardcoded)
        set_origin(hard_url)
        result = _run_pull(git_base, notes_dir, branch, git_env)
        if result.returncode == 0:
            logger.info("硬编码加速 %s 拉取成功", hardcoded)
            return True, ""
        last_detail = _with_sync_detail(f"硬编码加速 {hardcoded} 拉取失败", result.stderr or result.stdout, settings.github_token)
        logger.error("硬编码加速 %s 拉取失败: %s", hardcoded, _clip_sync_detail(result.stderr or result.stdout, settings.github_token))

    set_origin(direct_url)
    return False, last_detail


def _github_sync(settings) -> tuple[bool, bool, str]:
    """执行 GitHub 只读同步，返回 (成功, 有变更, 失败详情)"""
    if not settings.github_repo_url:
        logger.info("未配置 GitHub 仓库 URL，跳过")
        return True, False, ""

    notes_dir = Path(settings.notes_dir)
    git_dir = notes_dir / ".git"

    git_env = os.environ.copy()
    git_base = ["git", "-c", "safe.directory=*"]
    if settings.git_proxy:
        git_base += ["-c", f"http.proxy={settings.git_proxy}", "-c", f"https.proxy={settings.git_proxy}"]
        logger.info("git 使用代理: %s", settings.git_proxy)

    repo_url = _build_repo_url(settings, settings.git_accelerator)
    if settings.git_accelerator:
        logger.info("使用加速镜像: %s", settings.git_accelerator)
    elif settings.github_token:
        logger.info("使用 GitHub Token 认证")

    try:
        if not git_dir.exists():
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
                detail = _with_sync_detail("GitHub 克隆失败", result.stderr or result.stdout, settings.github_token)
                logger.error("GitHub 克隆失败: %s", detail)
                return False, False, detail
            logger.info("GitHub 克隆完成")
            return True, True, ""

        logger.info("拉取 GitHub 仓库更新: %s", settings.github_repo_url)

        reset_cmd = git_base + ["-C", str(notes_dir), "reset", "--hard", "HEAD"]
        subprocess.run(reset_cmd, capture_output=True, text=True, timeout=30, env=git_env)

        clean_cmd = git_base + ["-C", str(notes_dir), "clean", "-fd"]
        subprocess.run(clean_cmd, capture_output=True, text=True, timeout=30, env=git_env)

        old_commit_cmd = git_base + ["-C", str(notes_dir), "rev-parse", "HEAD"]
        old_result = subprocess.run(old_commit_cmd, capture_output=True, text=True, timeout=10, env=git_env)
        old_commit = old_result.stdout.strip() if old_result.returncode == 0 else ""

        ok, detail = _pull_with_fallback(settings, git_base, notes_dir, git_env)
        if not ok:
            return False, False, detail

        new_commit_cmd = git_base + ["-C", str(notes_dir), "rev-parse", "HEAD"]
        new_result = subprocess.run(new_commit_cmd, capture_output=True, text=True, timeout=10, env=git_env)
        new_commit = new_result.stdout.strip() if new_result.returncode == 0 else ""

        changed = (old_commit != new_commit)
        logger.info("GitHub 拉取完成，有变更: %s (旧: %s, 新: %s)", changed, old_commit[:8], new_commit[:8])

        return True, changed, ""
    except Exception as e:
        detail = _with_sync_detail("GitHub 同步异常", str(e), settings.github_token)
        logger.error("GitHub 同步异常: %s", detail)
        return False, False, detail


def _remote_sync(settings) -> tuple[bool, bool, str]:
    """根据配置的远程类型执行同步，返回 (成功, 有变更, 失败详情)"""
    if settings.remote_type == "github":
        return _github_sync(settings)
    elif settings.remote_type == "cos":
        return _coscli_sync(settings)
    else:
        detail = _with_sync_detail("未知的远程类型", settings.remote_type)
        logger.error("未知的远程类型: %s", settings.remote_type)
        return False, False, detail


def _trigger_reindex() -> tuple[bool, str]:
    """直接调用 reindex()，不需要走 HTTP，返回 (成功, 失败详情)。"""
    try:
        from .indexer import reindex
        result = reindex()
        logger.info("reindex 完成: processed=%d deleted=%d", result.get("processed", 0), result.get("deleted", 0))
        return True, ""
    except Exception as e:
        detail = _with_sync_detail("reindex 失败", str(e))
        logger.error("reindex 失败: %s", detail)
        return False, detail


def _trigger_web_rebuild() -> tuple[bool, str]:
    """执行 docker compose run --rm -T web，返回 (成功, 失败详情)。

    compose 通过 docker socket 在宿主机 daemon 端执行。两个关键点：
    1. notes bind 用 ${HOST_NOTES_DIR}/notes 绝对路径（HOST_NOTES_DIR 由 _resolve_host_data_dir 探测），
       避免相对路径在宿主机端解析到空目录、构建 0 篇却 exit 0。
    2. --project-name 由 _resolve_compose_project 从自身容器 label 探测，与 blog 容器对齐；
       否则容器内 cwd=/project 会让 compose 用 project name=project，命名卷变成 project_public，
       与 blog 容器挂载的 blog_public 不是同一个，构建产物写不到 nginx 托管的卷上。
    """
    try:
        host_data_dir = _resolve_host_data_dir()
        if not host_data_dir:
            detail = "无法探测宿主机 data 路径，跳过 Quartz 构建"
            logger.error(detail)
            return False, detail
        project_dir = os.environ.get("PROJECT_DIR", "/project")
        env = os.environ.copy()
        env["HOST_NOTES_DIR"] = host_data_dir
        project_name = _resolve_compose_project()
        cmd = ["docker", "compose"]
        if project_name:
            cmd += ["--project-name", project_name]
        cmd += ["run", "--rm", "-T", "web"]
        logger.info("触发 Quartz 构建（HOST_NOTES_DIR=%s, project=%s）", host_data_dir, project_name or "默认")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=project_dir, env=env)
        if result.returncode != 0:
            detail = _with_sync_detail("Quartz 构建失败", result.stderr or result.stdout)
            logger.error("Quartz 构建失败: %s", detail)
            return False, detail
        out = (result.stdout or "")
        if "Found 0 input files" in out and "Done processing 0 files" in out:
            detail = _with_sync_detail("Quartz 构建产物为 0 篇，疑似 notes 挂载失效", out)
            logger.error("Quartz 构建产物为 0 篇，疑似 notes 挂载失效: %s", _clip_sync_detail(out))
            return False, detail
        logger.info("Quartz 构建完成")
        return True, ""
    except Exception as e:
        detail = _with_sync_detail("Quartz 构建异常", str(e))
        logger.error("Quartz 构建异常: %s", detail)
        return False, detail


def _run_content_visibility_rebuild_job() -> str:
    """执行一次不拉远程的展示范围重建，返回最终状态。"""
    sync_status = "success"
    sync_detail = ""
    reindex_ok, reindex_detail = _trigger_reindex()
    if not reindex_ok:
        sync_status = "reindex_failed"
        sync_detail = reindex_detail
    rebuild_ok, rebuild_detail = _trigger_web_rebuild()
    if not rebuild_ok and sync_status == "success":
        sync_status = "build_failed"
        sync_detail = rebuild_detail

    state = read_sync_state()
    state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    state["last_sync_status"] = sync_status
    state["last_sync_detail"] = "" if sync_status == "success" else sync_detail
    write_sync_state(state)

    if sync_status in ("reindex_failed", "build_failed"):
        try:
            from .notifier import notify_sync_failure
            notify_sync_failure(get_settings(), sync_status, sync_detail)
        except Exception as ne:
            logger.warning("展示范围重建失败通知发送失败: %s", ne)
    return sync_status



def trigger_rebuild_only() -> None:
    """仅触发 Quartz 重建(不拉笔记、不 reindex),用于博客语言切换等无需同步笔记的场景。

    _syncing_loop 在笔记无变更(changed=False)时跳过 rebuild,语言切换需绕过该判断:
    后台线程直接置 building=true → _trigger_web_rebuild() → building=false。
    不动 pending/syncing,避免与 _syncing_loop 竞态;若已有同步/重建在执行则排队等待。
    """
    def _run():
        # 等待当前同步/重建结束再执行,避免请求被静默丢弃
        while True:
            state = read_sync_state()
            if not state.get("syncing") and not state.get("building"):
                break
            time.sleep(POLL_INTERVAL)
        state = read_sync_state()
        state["building"] = True
        write_sync_state(state)
        try:
            ok, detail = _trigger_web_rebuild()
            if not ok:
                logger.warning("语言切换触发的 Quartz 重建失败: %s", detail)
                fail_state = read_sync_state()
                fail_state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
                fail_state["last_sync_status"] = "build_failed"
                fail_state["last_sync_detail"] = detail
                write_sync_state(fail_state)
        finally:
            state = read_sync_state()
            state["building"] = False
            write_sync_state(state)

    threading.Thread(target=_run, daemon=True).start()


def trigger_content_visibility_rebuild() -> str:
    """展示范围变更后重建索引与 Quartz,不重新拉取远程笔记。"""
    state = read_sync_state()
    if state.get("syncing") or state.get("building"):
        state["content_visibility_pending"] = True
        write_sync_state(state)
        logger.info("已有同步/重建在执行,展示范围重建已排队")
        return "queued"

    state["building"] = True
    state["content_visibility_pending"] = False
    write_sync_state(state)

    def _run():
        try:
            _run_content_visibility_rebuild_job()
        finally:
            state = read_sync_state()
            state["building"] = False
            write_sync_state(state)

    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception as e:
        state = read_sync_state()
        state["building"] = False
        state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
        state["last_sync_status"] = "build_failed"
        state["last_sync_detail"] = _with_sync_detail("展示范围重建线程启动失败", str(e))
        write_sync_state(state)
        raise
    return "started"


def startup_recovery() -> None:
    """syncer 线程启动时恢复卡死状态：同步或构建中断则重置为待同步"""
    state = read_sync_state()
    if state.get("syncing") or state.get("building"):
        logger.warning("检测到卡死状态（syncing/building=true），重置为待同步")
        state["syncing"] = False
        state["building"] = False
        state["pending"] = True
        state["manual_trigger"] = False
        state["debounce_until"] = 0
        state["syncing_started_at"] = 0
        write_sync_state(state)


def _syncing_loop() -> None:
    """syncer 后台线程主循环"""
    logger.info("syncer 线程启动，轮询间隔: %ds", POLL_INTERVAL)
    startup_recovery()

    while True:
        settings = get_settings()
        try:
            state = read_sync_state()
            pending = state.get("pending", False)
            syncing = state.get("syncing", False)
            building = state.get("building", False)
            content_visibility_pending = state.get("content_visibility_pending", False)
            debounce_until = state.get("debounce_until", 0)
            manual_trigger = state.get("manual_trigger", False)

            if content_visibility_pending and not pending and not syncing and not building:
                logger.info("执行排队的内容展示重建任务")
                state["building"] = True
                state["content_visibility_pending"] = False
                write_sync_state(state)
                try:
                    _run_content_visibility_rebuild_job()
                finally:
                    state = read_sync_state()
                    state["building"] = False
                    write_sync_state(state)
                time.sleep(POLL_INTERVAL)
                continue

            # 定时同步：需同时满足 auto_sync_enabled 开启 且 interval > 0
            if settings.auto_sync_enabled:
                interval = settings.sync_interval_seconds
                if interval > 0 and not pending and not syncing:
                    last_sync_at = state.get("last_sync_at", "")
                    if last_sync_at:
                        last_dt = datetime.fromisoformat(last_sync_at)
                        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                    else:
                        elapsed = interval  # 从未同步过，立即触发
                    if elapsed >= interval:
                        logger.info("定时同步触发，距上次同步 %.0fs", elapsed)
                        state["pending"] = True
                        state["debounce_until"] = 0
                        write_sync_state(state)  # 定时同步触发
                        pending = True

            # 手动模式标记的 pending 不自动拾取：保留状态等用户在后台点按钮触发
            if manual_trigger:
                time.sleep(POLL_INTERVAL)
                continue

            if not pending or syncing or time.time() < debounce_until:
                # 安全网：同步状态卡死超过 10 分钟自动重置
                if syncing and state.get("syncing_started_at", 0):
                    elapsed = time.time() - state["syncing_started_at"]
                    if elapsed > 600:
                        logger.warning("同步超时（%.0fs），重置卡死状态", elapsed)
                        state["syncing"] = False
                        state["building"] = False
                        state["syncing_started_at"] = 0
                        syncing = False
                        write_sync_state(state)
                if not pending or syncing or time.time() < debounce_until:
                    time.sleep(POLL_INTERVAL)
                    continue

            logger.info("检测到待同步任务，防抖窗口已过期，开始执行")
            state["syncing"] = True
            state["building"] = False
            state["syncing_started_at"] = time.time()
            write_sync_state(state)

            sync_status = "success"
            sync_detail = ""

            # 1. 远程同步（COS 或 GitHub）
            ok, changed, remote_detail = _remote_sync(settings)
            if not ok:
                sync_status = "sync_failed"
                sync_detail = remote_detail
                logger.info("远程同步失败，跳过 reindex 和 Quartz 构建")
            elif not changed:
                logger.info("无文件变更，跳过 reindex 和构建")
            else:
                # 2. 索引重建
                state["building"] = True
                write_sync_state(state)
                reindex_ok, reindex_detail = _trigger_reindex()
                if not reindex_ok:
                    if sync_status == "success":
                        sync_status = "reindex_failed"
                        sync_detail = reindex_detail

                # 3. Quartz 重建
                rebuild_ok, rebuild_detail = _trigger_web_rebuild()
                if not rebuild_ok:
                    if sync_status == "success":
                        sync_status = "build_failed"
                        sync_detail = rebuild_detail

            # 4. 清除 pending 与 building
            state = read_sync_state()
            state["pending"] = False
            state["syncing"] = False
            state["building"] = False
            state["manual_trigger"] = False
            state["syncing_started_at"] = 0
            state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
            state["last_sync_status"] = sync_status
            state["last_sync_detail"] = "" if sync_status == "success" else sync_detail
            write_sync_state(state)

            logger.info("同步流程结束: %s", sync_status)

            # 同步失败且开启同步通知时发邮件告警（只在最终失败态触发一次）
            if sync_status in ("sync_failed", "reindex_failed", "build_failed"):
                try:
                    from .notifier import notify_sync_failure
                    notify_sync_failure(settings, sync_status, sync_detail)
                except Exception as ne:
                    logger.warning("同步通知发送失败: %s", ne)
        except Exception as e:
            logger.error("syncer 循环异常: %s", e)

        time.sleep(POLL_INTERVAL)


def start_syncer_thread() -> None:
    """启动 syncer 后台线程（由 FastAPI startup 事件调用）"""
    t = threading.Thread(target=_syncing_loop, name="syncer", daemon=True)
    t.start()
