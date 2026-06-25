from datetime import datetime
from typing import Literal

from .database import get_connection


def submit_feedback(
    page_url: str,
    page_title: str,
    feedback_type: Literal["error", "suggestion", "other"],
    content: str,
) -> str:
    """提交用户对博客文档的反馈。当用户表达文档有错误、需要优化改进或有其他意见时调用此工具。

    参数说明：
    - page_url: 用户当前阅读的页面路径，如 "/examples/blog-架构"
    - page_title: 用户当前阅读的页面标题
    - feedback_type: 反馈类型，"error" 表示文档内容有错误，"suggestion" 表示优化建议，"other" 表示其他意见
    - content: 反馈的具体内容，应整理为清晰简洁的描述
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO feedback (page_url, page_title, feedback_type, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (page_url, page_title, feedback_type, content, datetime.now().isoformat()),
        )
        conn.commit()
        return "反馈已提交，感谢您的贡献！管理员会尽快处理。"
    finally:
        conn.close()
