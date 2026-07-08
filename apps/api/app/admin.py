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
from . import appearance as appearance_mod
from . import visibility as visibility_mod
import logging

logger = logging.getLogger("admin")

router = APIRouter(prefix="/admin/api", tags=["admin"])


class FeedbackStatusUpdate(BaseModel):
    new_status: Literal["pending", "resolved", "dismissed"]


class AppearanceUploadRequest(BaseModel):
    image: str  # data URL,如 data:image/png;base64,xxxx
    kind: Literal["avatar", "favicon", "icon"] = "icon"


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

    # 锁外先计算本次要应用的更新（仅依赖 model_fields/sensitive，不依赖文件内容）
    to_apply: dict[str, str] = {}
    updated_keys: list[str] = []
    for key, new_value in updates.items():
        if key not in s.model_fields:
            continue
        is_sensitive = key in sensitive
        # 敏感字段：含 **** 表示未修改（部分脱敏值或全星号），跳过覆盖
        if is_sensitive and "****" in new_value:
            continue
        to_apply[key.upper()] = new_value
        updated_keys.append(key)

    if not updated_keys:
        return {"status": "ok", "updated": [], "message": "无变更"}

    # 加文件锁读写 .env，防止并发写入竞态（读取和写入在同一个锁内）
    with open(env_path, "a+") as f:
        f.seek(0)
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            raw_lines = f.readlines()

            # 锁内基于最新文件构建 existing，避免并发保存互相覆盖
            existing: dict[str, str] = {}
            for line in raw_lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" in stripped:
                    k, _, v = stripped.partition("=")
                    existing[k.strip().upper()] = v.strip()
            existing.update(to_apply)

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

    # 展示范围改由 content_visibility.json 控制;COS webhook 不再按前缀丢弃事件。

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
        for key in ("pending", "syncing", "building", "content_visibility_pending", "manual_trigger", "debounce_until", "triggered_at",
                     "event_count", "last_sync_at", "last_sync_status", "last_sync_detail"):
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
        expected = build_sync_workflow(s.github_branch, blog_url, s.webhook_secret)
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
        content = build_sync_workflow(s.github_branch, blog_url, s.webhook_secret)

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


# ============================ 内容展示范围 ============================ #


@router.get("/content/tree")
def get_content_tree(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """获取笔记目录/Markdown 文件树与当前展示范围(不返回文件内容)。"""
    _verify_admin(x_admin_token)
    return visibility_mod.build_content_tree()


@router.get("/content/visibility")
def get_content_visibility(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """获取当前展示范围配置。"""
    _verify_admin(x_admin_token)
    return visibility_mod.get_visibility_admin()


@router.get("/content/file")
def get_content_file(
    path: str,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """获取指定 Markdown 文件的原始内容预览(仅管理后台可见)。"""
    _verify_admin(x_admin_token)
    preview = visibility_mod.get_markdown_preview(path)
    if not preview:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在或不可预览")
    return preview


@router.put("/content/visibility")
def update_content_visibility(
    body: dict[str, Any],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """保存展示范围,刷新构建清单并触发或排队 reindex + Quartz rebuild。"""
    _verify_admin(x_admin_token)
    saved = visibility_mod.save_visibility(body)
    from .syncer import trigger_content_visibility_rebuild

    rebuild_state = trigger_content_visibility_rebuild()
    message = (
        "展示范围已保存,正在重建索引和博客"
        if rebuild_state == "started"
        else "展示范围已保存,当前有任务执行中,已排队等待应用"
    )
    return {
        "status": "ok" if rebuild_state == "started" else "accepted",
        "visibility": saved,
        "rebuild_state": rebuild_state,
        "message": message,
    }


# ============================ 外观设置 ============================ #


@router.get("/appearance")
def get_appearance_config(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """获取外观配置(含完整背景预设列表,便于后台编辑;文件名转 URL 便于预览)"""
    _verify_admin(x_admin_token)
    return appearance_mod.get_appearance_admin()


@router.put("/appearance")
def update_appearance_config(
    body: dict[str, Any],
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """保存外观配置(校验社交链接数量、shape 枚举等)"""
    _verify_admin(x_admin_token)
    # blog_locale 变化需触发 Quartz rebuild:locale 是构建期注入,rebuild 才生效。
    # 笔记无变更时 _syncing_loop 会跳过 rebuild,故走独立的 trigger_rebuild_only。
    # 首次设置语言(appearance.json 无 blog_locale)也触发:旧配置默认 en-US 与新值相等,
    # 但博客实际仍是旧 locale(如 zh-CN),需 rebuild 让 entrypoint sed 覆盖 config。
    first_locale_set = not appearance_mod.has_blog_locale()
    old_locale = appearance_mod.get_appearance_admin().get("blog_locale", "en-US")
    saved = appearance_mod.save_appearance(body)
    if first_locale_set or saved.get("blog_locale", "en-US") != old_locale:
        from .syncer import trigger_rebuild_only
        trigger_rebuild_only()
    return {"status": "ok", "appearance": saved, "message": "外观配置已生效,刷新博客可见"}


@router.post("/appearance/upload")
def upload_appearance_asset(
    body: AppearanceUploadRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """上传图片(data URL)到 data/assets/,返回公开 URL"""
    _verify_admin(x_admin_token)
    filename = appearance_mod.save_data_url_asset(body.image)
    return {
        "status": "ok",
        "filename": filename,
        "url": appearance_mod.ASSET_URL_PREFIX + filename,
    }
