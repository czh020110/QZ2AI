"""反馈邮件通知器 - 定期检查未处理反馈并通过 SMTP 发送邮件通知"""
import logging
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from .config import get_settings

logger = logging.getLogger("notifier")

LAST_NOTIFIED_PATH = Path("/data/notifier-last-id.json")
POLL_INTERVAL = 60  # 轮询间隔（秒）


def _read_last_id() -> int:
    """读取最后通知过的反馈 ID"""
    try:
        import json
        data = json.loads(LAST_NOTIFIED_PATH.read_text(encoding="utf-8"))
        return data.get("last_id", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def _write_last_id(last_id: int) -> None:
    """保存最后通知过的反馈 ID"""
    import json
    import fcntl
    with open(LAST_NOTIFIED_PATH, "a+") as f:
        f.seek(0)
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            json.dump({"last_id": last_id, "updated_at": datetime.now().isoformat()}, f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _get_pending_feedback(since_id: int) -> list[dict]:
    """从 SQLite 获取自 since_id 之后的新反馈"""
    import sqlite3
    import os
    db_path = os.environ.get("DB_PATH", "/data/blog.db")
    if not Path(db_path).exists():
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, page_url, page_title, feedback_type, content, created_at "
            "FROM feedback WHERE id > ? AND status = 'pending' ORDER BY id",
            (since_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _format_feedback_html(feedbacks: list[dict], blog_url: str) -> str:
    """生成反馈列表 HTML"""
    items_html = ""
    for fb in feedbacks:
        type_label = fb["feedback_type"]
        type_class = {
            "error": "badge-error",
            "suggestion": "badge-suggestion",
            "other": "badge-other",
        }.get(type_label, "badge-other")
        type_emoji = {"error": "🐛", "suggestion": "💡", "other": "📝"}.get(type_label, "📝")

        page_title = fb.get("page_title") or "未知页面"
        page_url = fb.get("page_url") or ""
        content = fb.get("content") or ""
        created_at = fb.get("created_at") or ""

        items_html += f"""
        <div class="feedback-item">
          <div style="margin-bottom:10px">
            <span class="badge {type_class}">{type_emoji} {type_label}</span>
            <strong style="margin-left:10px">{page_title}</strong>
          </div>
          <p style="margin:10px 0;color:#555">{content}</p>
          <div style="font-size:12px;color:#999">
            <span>🔗 {page_url}</span> | <span>🕐 {created_at}</span>
          </div>
        </div>"""

    admin_url = blog_url.rstrip("/") + "/admin/"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; line-height:1.6; color:#333; max-width:600px; margin:0 auto; }}
  .header {{ background:#6c7ee1; color:white; padding:20px; border-radius:8px 8px 0 0; }}
  .content {{ background:#f9f9f9; padding:20px; border-radius:0 0 8px 8px; }}
  .feedback-item {{ background:white; margin:15px 0; padding:15px; border-radius:5px; border-left:4px solid #6c7ee1; }}
  .badge {{ display:inline-block; padding:4px 8px; border-radius:4px; font-size:12px; font-weight:bold; }}
  .badge-error {{ background:#ffe5e9; color:#e85d6f; }}
  .badge-suggestion {{ background:#e5f2ff; color:#5b9cf5; }}
  .badge-other {{ background:#f0f0f0; color:#8b8fa3; }}
  .button {{ display:inline-block; padding:10px 20px; background:#6c7ee1; color:white; text-decoration:none; border-radius:5px; margin-top:15px; }}
  .footer {{ margin-top:20px; padding-top:20px; border-top:1px solid #ddd; font-size:12px; color:#999; }}
</style></head>
<body>
  <div class="header">
    <h2>📬 博客反馈通知</h2>
    <p>您有 <strong>{len(feedbacks)}</strong> 条新反馈待处理</p>
  </div>
  <div class="content">
    <p>以下反馈来自您的博客读者：</p>
    {items_html}
    <a href="{admin_url}" class="button">🔗 前往管理后台处理</a>
  </div>
  <div class="footer">
    <p>此邮件由博客系统自动发送</p>
    <p>博客地址: {blog_url}</p>
  </div>
</body>
</html>"""


def _send_email(settings, html_body: str, subject: str) -> bool:
    """通过 SMTP 发送邮件"""
    if not all([settings.mail_server, settings.mail_username, settings.mail_password, settings.notify_email]):
        logger.warning("邮件配置不完整，跳过发送")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"博客反馈系统 <{settings.mail_username}>"
        msg["To"] = settings.notify_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        ctx = ssl.create_default_context()
        port = settings.mail_port

        with smtplib.SMTP_SSL(settings.mail_server, port, context=ctx, timeout=30) as server:
            server.login(settings.mail_username, settings.mail_password)
            server.sendmail(settings.mail_username, [settings.notify_email], msg.as_string())

        logger.info("邮件发送成功: %s", settings.notify_email)
        return True
    except Exception as e:
        logger.error("邮件发送失败: %s", e)
        return False


def _notifier_loop() -> None:
    """通知器后台线程主循环"""
    logger.info("notifier 线程启动")

    while True:
        try:
            settings = get_settings()

            if not settings.notify_enabled:
                time.sleep(POLL_INTERVAL)
                continue

            check_interval = settings.notify_interval_seconds
            last_id = _read_last_id()
            feedbacks = _get_pending_feedback(last_id)

            if feedbacks:
                max_id = max(fb["id"] for fb in feedbacks)
                logger.info("发现 %d 条新反馈（ID %d~%d），发送邮件通知", len(feedbacks), feedbacks[0]["id"], max_id)

                blog_url = settings.allowed_origin_list[0] if settings.allowed_origin_list else "http://localhost"
                html = _format_feedback_html(feedbacks, blog_url)
                subject = f"【博客反馈】您有 {len(feedbacks)} 条新反馈待处理"

                if _send_email(settings, html, subject):
                    _write_last_id(max_id)
                # 发送失败不更新 last_id，下次重试
            else:
                logger.debug("无新反馈，跳过通知")

        except Exception as e:
            logger.error("notifier 循环异常: %s", e)

        time.sleep(check_interval or 1800)


def start_notifier_thread() -> None:
    """启动 notifier 后台线程（由 FastAPI startup 事件调用）"""
    t = threading.Thread(target=_notifier_loop, name="notifier", daemon=True)
    t.start()
