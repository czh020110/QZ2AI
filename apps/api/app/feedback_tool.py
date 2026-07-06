from datetime import datetime
from typing import Literal

from .database import get_connection


def submit_feedback(
    page_url: str,
    page_title: str,
    feedback_type: Literal["error", "suggestion", "other"],
    content: str,
) -> str:
    """Submit user feedback about a blog document. Call this when the user points out a documentation error, suggests an improvement, or raises other opinions.

    Parameters:
    - page_url: the page path the user is reading, e.g. "/examples/blog-架构"
    - page_title: the title of the page the user is reading
    - feedback_type: "error" (doc content is wrong), "suggestion" (improvement idea), "other" (other opinion)
    - content: the feedback content, organized as a clear and concise description
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO feedback (page_url, page_title, feedback_type, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (page_url, page_title, feedback_type, content, datetime.now().isoformat()),
        )
        conn.commit()
        return "Feedback submitted. Thank you! The admin will review it soon."
    finally:
        conn.close()
