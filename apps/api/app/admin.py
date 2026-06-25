from datetime import datetime
import fcntl
import os
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, status

from pydantic import BaseModel

from .config import get_settings
from .database import get_connection

router = APIRouter(prefix="/admin/api", tags=["admin"])


class FeedbackStatusUpdate(BaseModel):
    new_status: Literal["resolved", "dismissed"]


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
        result.append(
            {
                "key": field_name,
                "value": "****" if is_sensitive else value,
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
                    existing[k.strip()] = v.strip()
    except FileNotFoundError:
        pass

    updated_keys: list[str] = []
    for key, new_value in updates.items():
        if key not in s.model_fields:
            continue
        is_sensitive = key in sensitive
        # 敏感字段：**** 表示不修改，只有非 **** 值才覆盖
        if is_sensitive and new_value == "****":
            continue
        existing[key] = new_value
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

    return {
        "status": "ok",
        "updated": updated_keys,
        "message": "配置已更新，需重启 API 服务生效",
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
