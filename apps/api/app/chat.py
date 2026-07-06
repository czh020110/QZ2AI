import json
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar
from functools import lru_cache

from llama_index.core.agent.workflow import (
    AgentOutput,
    AgentStream,
    FunctionAgent,
    ToolCall,
    ToolCallResult,
)
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai_like import OpenAILike

from .appearance import get_appearance_public
from .config import get_settings
from .errors import AppError
from .feedback_tool import submit_feedback
from .models import ChatRequest, ErrorResponse
from .prompt_templates import SYSTEM_PROMPT
from .indexer import get_note_context
from .retriever import retrieve, retrieve_by_slug

logger = logging.getLogger("api")

MOCK_CHUNKS = ["[MOCK] ", "Backend skeleton ready, ", "Agent pipeline connected."]

# 当前请求的检索来源（协程隔离，替代模块级全局变量避免并发竞态）
_current_sources: ContextVar[list[dict]] = ContextVar("_current_sources", default=[])
_current_page_url: ContextVar[str] = ContextVar("_current_page_url", default="")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_rag_tool():
    def search_blog_notes(query: str) -> str:
        """Search all blog notes. Call this when the user asks about knowledge, opinions, technical notes, or study records in the blog.

        Applicable scenarios:
        - The user asks about techniques, concepts, methods, or tools recorded in the blog
        - The user asks about the blogger's opinions, experiences, or study notes
        - The user asks "does the blog have...", "have you written...", "notes about..."
        - A question the current page cannot answer but other notes might

        Parameters:
        - query: the search query; extract the core keywords from the user's question
        """
        contexts = retrieve(query)
        if not contexts:
            _current_sources.set([])
            return "No relevant blog content found."
        content_block = "\n\n".join(
            f"[Source: {c.get('title', '')}]\n{c.get('text', '')}" for c in contexts
        )
        seen_urls: set[str] = set()
        deduped = []
        for c in contexts:
            url = c.get("source_url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped.append({"title": c.get("title", ""), "source_url": url})
        _current_sources.set(deduped)
        return content_block

    return FunctionTool.from_defaults(
        fn=search_blog_notes,
        name="search_blog_notes",
        description="Search all blog notes. Use when the user asks about knowledge, opinions, technical notes, study records in the blog, or a question the current page cannot answer.",
    )


def _build_feedback_tool():
    return FunctionTool.from_defaults(fn=submit_feedback)


def _build_current_page_tool():
    def search_current_page(query: str) -> str:
        """Retrieve the content of the note the user is currently reading.
        Call this when the user asks "what is this article about", "what does XXX here mean", or other questions clearly pointing to the current page.

        Parameters:
        - query: the search query; extract the core keywords from the question
        """
        source_url = _current_page_url.get()
        if not source_url:
            return "Current page info unavailable; try a global search."
        contexts = retrieve_by_slug(query, source_url)
        if not contexts:
            return "No relevant content found in the current page."
        content_block = "\n\n".join(
            f"[Source: {c.get('title', '')}]\n{c.get('text', '')}" for c in contexts
        )
        seen_urls: set[str] = set()
        deduped = []
        for c in contexts:
            url = c.get("source_url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped.append({"title": c.get("title", ""), "source_url": url})
        _current_sources.set(deduped)
        return content_block

    return FunctionTool.from_defaults(
        fn=search_current_page,
        name="search_current_page",
        description="Retrieve the content of the note the user is currently reading. Use for questions pointing to the current page, e.g. 'what is this article about', 'what does XXX here mean'.",
    )


@lru_cache
def _get_agent() -> FunctionAgent:
    settings = get_settings()
    llm = OpenAILike(
        model=settings.llm_model,
        api_base=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0.7,
        context_window=128000,
        is_chat_model=True,
        is_function_calling_model=True,
    )
    return FunctionAgent(
        tools=[_build_rag_tool(), _build_current_page_tool(), _build_feedback_tool()],
        llm=llm,
        system_prompt=SYSTEM_PROMPT,
    )


_ROLE_MAP = {"user": MessageRole.USER, "assistant": MessageRole.ASSISTANT}


async def chat_event_stream(req: ChatRequest) -> AsyncIterator[str]:
    settings = get_settings()
    page_ctx = req.page_context

    if settings.llm_mock or not settings.llm_api_key:
        for chunk in MOCK_CHUNKS:
            yield _sse({"delta": chunk})
        yield _sse({"done": True, "sources": []})
        return

    try:
        # 反馈语言提示:submit_feedback 的 content 用管理员语言(admin_locale)撰写,
        # 因反馈供管理员查看。提示词与工具描述保持英文,仅此处动态注入语言名。
        try:
            admin_locale = get_appearance_public().get("admin_locale", "en-US")
        except Exception:
            admin_locale = "en-US"
        lang_name = {"en-US": "English", "zh-CN": "Chinese", "ja-JP": "Japanese"}.get(admin_locale, "English")
        feedback_hint = (
            f"[Instruction] When you call submit_feedback, write the feedback content in {lang_name} "
            f"(the site admin's language) so the admin can read it."
        )
        user_msg = feedback_hint + "\n\n" + req.question
        if page_ctx and page_ctx.slug:
            _current_page_url.set(page_ctx.slug)
            note_ctx = get_note_context(page_ctx.slug)
            ctx_lines = [f"[Current page: {page_ctx.slug}]"]
            if note_ctx.get("frontmatter"):
                fm = note_ctx["frontmatter"]
                ctx_lines.append("Note metadata: " + ", ".join(f"{k}: {v}" for k, v in fm.items()))
            if note_ctx.get("headings"):
                ctx_lines.append("Headings outline:\n" + "\n".join(note_ctx["headings"]))
            user_msg = feedback_hint + "\n\n" + "\n".join(ctx_lines) + "\n\n" + req.question

        chat_history = [
            ChatMessage(role=_ROLE_MAP.get(m.role, MessageRole.USER), content=m.content)
            for m in req.history
        ]
        _current_sources.set([])

        agent = _get_agent()
        handler = agent.run(user_msg=user_msg, chat_history=chat_history)

        async for event in handler.stream_events():
            if isinstance(event, AgentStream):
                if event.delta:
                    yield _sse({"delta": event.delta})
            elif isinstance(event, ToolCallResult):
                if event.tool_name == "submit_feedback" and event.tool_output:
                    output = str(event.tool_output)
                    if output:
                        yield _sse({"delta": output})

        await handler

    except AppError as exc:
        yield _sse(
            {
                "error": ErrorResponse(
                    code=exc.code,
                    message=exc.message,
                    detail=exc.detail,
                ).model_dump(mode="json", exclude_none=True),
                "done": True,
                "sources": [],
            }
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("chat 流式输出失败: %s", exc)
        yield _sse(
            {
                "error": ErrorResponse(
                    code="internal_error", message="服务内部错误"
                ).model_dump(mode="json", exclude_none=True),
                "done": True,
                "sources": [],
            }
        )
        return

    yield _sse({"done": True, "sources": _current_sources.get()})
