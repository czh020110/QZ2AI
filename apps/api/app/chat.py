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

from .config import get_settings
from .errors import AppError
from .feedback_tool import submit_feedback
from .models import ChatRequest, ErrorResponse
from .prompt_templates import SYSTEM_PROMPT
from .indexer import get_note_context
from .retriever import retrieve, retrieve_by_slug

logger = logging.getLogger("api")

MOCK_CHUNKS = ["（MOCK）", "后端骨架已就绪，", "Agent 链路已接入。"]

# 当前请求的检索来源（协程隔离，替代模块级全局变量避免并发竞态）
_current_sources: ContextVar[list[dict]] = ContextVar("_current_sources", default=[])
_current_page_url: ContextVar[str] = ContextVar("_current_page_url", default="")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_rag_tool():
    def search_blog_notes(query: str) -> str:
        """搜索博客所有笔记内容。当用户提问需要查找博客中的知识、观点、技术笔记、学习记录等内容时调用此工具。

        适用场景：
        - 用户询问博客中记录过的技术、概念、方法、工具
        - 用户询问博主的观点、经验、学习笔记
        - 用户询问"博客里有没有..."、"你写过..."、"笔记中关于..."
        - 当前页面无法回答的问题，但可能在其他笔记中有答案

        参数说明：
        - query: 搜索查询，应提取用户问题中的核心关键词
        """
        contexts = retrieve(query)
        if not contexts:
            _current_sources.set([])
            return "未找到相关博客内容。"
        content_block = "\n\n".join(
            f"[来源:{c.get('title', '')}]\n{c.get('text', '')}" for c in contexts
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
        description="搜索博客所有笔记内容。当用户询问博客中记录的知识、观点、技术笔记、学习记录或当前页面无法回答的问题时使用。",
    )


def _build_feedback_tool():
    return FunctionTool.from_defaults(fn=submit_feedback)


def _build_current_page_tool():
    def search_current_page(query: str) -> str:
        """检索当前用户正在阅读的这篇笔记的内容。
        当用户询问"这篇文章讲了什么"、"这里说的XXX是什么意思"、"这个章节的内容"等明确指向当前页面的问题时调用。

        参数说明：
        - query: 搜索查询，提取问题的核心关键词
        """
        source_url = _current_page_url.get()
        if not source_url:
            return "当前页面信息不可用，请尝试全局搜索。"
        contexts = retrieve_by_slug(query, source_url)
        if not contexts:
            return "当前页面中未找到相关内容。"
        content_block = "\n\n".join(
            f"[来源:{c.get('title', '')}]\n{c.get('text', '')}" for c in contexts
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
        description="检索当前用户正在阅读的这篇笔记内容。用于回答指向当前页面的问题，如'这篇文章讲了什么'、'这里的XXX是什么意思'。",
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
        user_msg = req.question
        if page_ctx and page_ctx.slug:
            _current_page_url.set(page_ctx.slug)
            note_ctx = get_note_context(page_ctx.slug)
            ctx_lines = [f"[当前页面：{page_ctx.slug}]"]
            if note_ctx.get("frontmatter"):
                fm = note_ctx["frontmatter"]
                ctx_lines.append("笔记元数据：" + "，".join(f"{k}: {v}" for k, v in fm.items()))
            if note_ctx.get("headings"):
                ctx_lines.append("章节大纲：\n" + "\n".join(note_ctx["headings"]))
            user_msg = "\n".join(ctx_lines) + "\n\n" + req.question

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
