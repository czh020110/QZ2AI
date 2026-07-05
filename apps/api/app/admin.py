import base64
from datetime import datetime
import fcntl
import json
import os
import time
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, status

from pydantic import BaseModel

from .sync_state import read_sync_state, write_sync_state
from .database import get_connection
from .models import SyncStatusResponse, WebhookResponse
from .config import get_settings
import logging

logger = logging.getLogger("admin")

router = APIRouter(prefix="/admin/api", tags=["admin"])


class FeedbackStatusUpdate(BaseModel):
    new_status: Literal["pending", "resolved", "dismissed"]


def _verify_admin(x_admin_token: str | None) -> None:
    s = get_settings()
    if not s.admin_token or x_admin_token != s.admin_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的管理员 token")


# ============================ 反馈管理 ============================ #


@router.get("/feedback")
def list_feedback(
    status_filter: Literal["pending", "resolved", "dismissed"] | None = None,
    x_admin_token: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    _verify_admin(x_admin_token)
    conn = get_connection()
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.patch("/feedback/{feedback_id}")
def update_feedback_status(
    feedback_id: int,
    body: FeedbackStatusUpdate,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_admin(x_admin_token)
    new_status = body.new_status
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "反馈不存在")

        resolved_at = datetime.now().isoformat() if new_status == "resolved" else None
        conn.execute(
            "UPDATE feedback SET status = ?, resolved_at = ? WHERE id = ?",
            (new_status, resolved_at, feedback_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
        ).fetchone()
        return dict(updated)
    finally:
        conn.close()


# ============================ 配置管理 ============================ #


@router.get("/config")
def get_config(
    x_admin_token: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    _verify_admin(x_admin_token)
    s = get_settings()
    sensitive = s.sensitive_keys
    result = []
    for field_name, field_info in s.model_fields.items():
        value = getattr(s, field_name, "")
        is_sensitive = field_name in sensitive
        display_value = _partial_mask(str(value)) if is_sensitive else value
        result.append(
            {
                "key": field_name,
                "value": display_value,
                "sensitive": is_sensitive,
                "type": _field_type_name(field_info),
            }
        )
    return result


@router.put("/config")
def update_config(
    updates: dict[str, str],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_admin(x_admin_token)
    s = get_settings()
    sensitive = s.sensitive_keys

    env_path = s.model_config.get("env_file", ".env")
    if not env_path:
        env_path = ".env"

    existing: dict[str, str] = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    existing[k.strip().upper()] = v.strip()
    except FileNotFoundError:
        pass

    updated_keys: list[str] = []
    for key, new_value in updates.items():
        if key not in s.model_fields:
            continue
        is_sensitive = key in sensitive
        # 敏感字段：含 **** 表示未修改（部分脱敏值或全星号），跳过覆盖
        if is_sensitive and "****" in new_value:
            continue
        existing[key.upper()] = new_value
        updated_keys.append(key)

    if not updated_keys:
        return {"status": "ok", "updated": [], "message": "无变更"}

    # 加文件锁读写 .env，防止并发写入竞态（读取和写入在同一个锁内）
    with open(env_path, "a+") as f:
        f.seek(0)
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            raw_lines = f.readlines()

            lines: list[str] = []
            written_keys: set[str] = set()
            for line in raw_lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    lines.append(line.rstrip("\n"))
                    continue
                if "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in existing:
                        lines.append(f"{k}={existing[k]}")
                        written_keys.add(k)
                        continue
                lines.append(line.rstrip("\n"))

            for k, v in existing.items():
                if k not in written_keys:
                    lines.append(f"{k}={v}")

            f.seek(0)
            f.truncate()
            f.write("\n".join(lines) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    # 同步更新当前进程环境变量 + 清除配置缓存
    # Docker compose 通过 env_file 注入的变量优先于文件，须同步更新 os.environ
    for key in updated_keys:
        os.environ[key.upper()] = str(existing[key.upper()])

    from .config import clear_settings_cache
    clear_settings_cache()

    return {
        "status": "ok",
        "updated": updated_keys,
        "message": "配置已更新并即时生效",
    }


def _field_type_name(field_info: Any) -> str:
    annotation = field_info.annotation
    if annotation is bool:
        return "bool"
    if annotation is int or (
        hasattr(annotation, "__args__") and int in getattr(annotation, "__args__", [])
    ):
        return "int"
    if annotation is float or (
        hasattr(annotation, "__args__") and float in getattr(annotation, "__args__", [])
    ):
        return "float"
    return "string"


def _partial_mask(value: str) -> str:
    """部分脱敏：保留前6后4字符，中间用 **** 替代；短值按比例保留"""
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    if len(value) <= 8:
        return value[:2] + "****" + value[-2:]
    if len(value) <= 12:
        return value[:3] + "****" + value[-3:]
    return value[:6] + "****" + value[-4:]


# ============================ 自动同步 ============================ #


def _trigger_sync_pending(s, skip_auto: bool = False) -> WebhookResponse:
    """置 pending=true 并设置防抖窗口，返回接收回执。

    webhook 入口（COS/GitHub）鉴权通过后都走这里，确保 pending 状态写入格式一致。
    skip_auto=True 时标记为手动模式触发（来自 webhook/sync）：syncer 不会自动拾取，
    UI 显示"待同步"提示用户点按钮触发，避免手动模式下 webhook 被直接丢弃。
    """
    state = read_sync_state()
    state["pending"] = True
    state["syncing"] = False
    state["building"] = False
    state["manual_trigger"] = skip_auto and not s.auto_sync_enabled
    state["debounce_until"] = 0 if skip_auto else time.time() + s.debounce_seconds
    state["triggered_at"] = datetime.now().isoformat()
    state["event_count"] = state.get("event_count", 0) + 1
    write_sync_state(state)
    msg = "同步任务已接收，等待防抖窗口结束后执行" if not skip_auto else "同步任务已接收，等待手动触发"
    return WebhookResponse(status="accepted", message=msg)


def verify_webhook(x_webhook_secret: str | None, x_admin_token: str | None, secret: str | None = None) -> None:
    """webhook 鉴权：webhook_secret 匹配则放行，否则回退到 admin token。对外暴露供 main 调用。"""
    _verify_webhook(x_webhook_secret, x_admin_token, secret)


def trigger_sync_pending(s, skip_auto: bool = False) -> WebhookResponse:
    """对外暴露的 pending 触发入口，供 main 的通用 webhook 路由复用。"""
    return _trigger_sync_pending(s, skip_auto=skip_auto)


def _verify_webhook(x_webhook_secret: str | None, x_admin_token: str | None, secret: str | None = None) -> None:
    s = get_settings()
    if s.webhook_secret and (x_webhook_secret == s.webhook_secret or secret == s.webhook_secret):
        return
    _verify_admin(x_admin_token)


@router.post("/webhook/cos", response_model=WebhookResponse)
def cos_webhook(
    body: dict[str, Any] | None = None,
    secret: str | None = None,
    x_webhook_secret: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> WebhookResponse:
    _verify_webhook(x_webhook_secret, x_admin_token, secret)
    s = get_settings()

    if not s.auto_sync_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "自动同步未启用")

    # 前缀过滤：只处理 notes_cos_prefix 下的事件
    if body and s.notes_cos_prefix:
        records = body.get("Records", [])
        prefix = s.notes_cos_prefix.lstrip("/")
        matched = False
        for record in records:
            cos_obj = record.get("cos", {}).get("cosObject", {})
            if not cos_obj:
                cos_obj = record.get("cosObject", {})
            key = cos_obj.get("key", "")
            # COS 事件 key 格式：appid/bucket/path，取 path 部分做前缀匹配
            if "/" in key and len(key.split("/", 2)) >= 3:
                key = key.split("/", 2)[2]
            if key.startswith(prefix):
                matched = True
                break
        if not matched and records:
            return WebhookResponse(status="ignored", message="事件不在监听前缀范围内")

    return _trigger_sync_pending(s)


@router.get("/sync-status", response_model=SyncStatusResponse)
def get_sync_status(
    x_admin_token: str | None = Header(default=None),
) -> SyncStatusResponse:
    _verify_admin(x_admin_token)
    state = read_sync_state()
    return SyncStatusResponse(**state)


@router.post("/sync-status", response_model=SyncStatusResponse)
def update_sync_status(
    body: dict[str, Any] | None = None,
    x_admin_token: str | None = Header(default=None),
) -> SyncStatusResponse:
    """合并更新同步状态（syncer 守护进程和管理后台使用）"""
    _verify_admin(x_admin_token)
    state = read_sync_state()
    if body:
        for key in ("pending", "syncing", "building", "manual_trigger", "debounce_until", "triggered_at",
                     "event_count", "last_sync_at", "last_sync_status"):
            if key in body:
                state[key] = body[key]
    write_sync_state(state)
    return SyncStatusResponse(**state)


# ============================ GitHub Actions 工作流管理 ============================ #

WORKFLOW_PATH = ".github/workflows/blog-sync.yml"


def _validate_github_config(s) -> tuple:
    """验证 GitHub 配置是否完整且远程源已切换到 GitHub，返回 (有效, 错误消息)。

    工作流只会触发 /api/webhook/sync，真正拉取走 _remote_sync()，后者依赖 remote_type=github；
    若未切换远程源，工作流触发后 syncer 会走 COS 分支导致行为错位，因此在此提前拦截。
    """
    if s.remote_type != "github":
        return False, "远程源未切换到 GitHub (当前 remote_type=" + (s.remote_type or "未设置") + ")，请先在[自动同步]中选择 GitHub 并保存"
    if not s.github_repo_url:
        return False, "未配置 GitHub 仓库地址 (github_repo_url)"
    if not s.github_token:
        return False, "未配置 GitHub Token (github_token)"
    if not s.webhook_secret:
        return False, '未配置 Webhook 密钥 (webhook_secret)，请先在[自动同步]配置中设置'
    return True, ""


@router.get("/github/workflow")
def get_github_workflow(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """检查博客同步工作流是否已存在于笔记仓库中"""
    _verify_admin(x_admin_token)
    s = get_settings()

    ok, msg = _validate_github_config(s)
    if not ok:
        return {"status": "not_configured", "message": msg, "exists": False, "current": None, "expected": None}

    try:
        from .github_client import get_file, build_sync_workflow

        existing = get_file(s.github_token, s.github_repo_url, s.github_branch, WORKFLOW_PATH)
        blog_url = s.allowed_origin_list[0] if s.allowed_origin_list else "http://localhost"
        expected = build_sync_workflow(s.github_branch, s.notes_github_prefix, blog_url, s.webhook_secret)
        current_content = None
        if existing and existing.get("content"):
            current_content = base64.b64decode(existing["content"]).decode("utf-8")

        return {
            "status": "ok",
            "message": "",
            "exists": existing is not None,
            "current": current_content,
            "expected": expected,
        }
    except Exception as e:
        logger.exception("获取 GitHub 工作流状态失败")
        return {"status": "error", "message": str(e), "exists": False, "current": None, "expected": None}


@router.post("/github/workflow")
def upsert_github_workflow(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """在笔记仓库中创建或更新博客同步工作流"""
    _verify_admin(x_admin_token)
    s = get_settings()

    ok, msg = _validate_github_config(s)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)

    try:
        from .github_client import build_sync_workflow, put_file

        blog_url = s.allowed_origin_list[0] if s.allowed_origin_list else "http://localhost"
        content = build_sync_workflow(s.github_branch, s.notes_github_prefix, blog_url, s.webhook_secret)

        result = put_file(
            token=s.github_token,
            repo_url=s.github_repo_url,
            branch=s.github_branch,
            path=WORKFLOW_PATH,
            content=content,
            message="添加/更新博客自动同步 Actions 工作流",
        )

        return {
            "status": "ok",
            "message": "工作流已创建/更新",
            "path": WORKFLOW_PATH,
            "url": result.get("content", {}).get("html_url", ""),
        }
    except Exception as e:
        logger.exception("创建 GitHub 工作流失败")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"操作失败: {e}")
