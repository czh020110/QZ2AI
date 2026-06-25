import json
import logging
from collections.abc import AsyncIterator
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
from llama_index.llms.openai import OpenAI

from .config import get_settings
from .errors import AppError
from .feedback_tool import submit_feedback
from .models import ChatRequest, ErrorResponse
from .prompt_templates import SYSTEM_PROMPT
from .retriever import retrieve

logger = logging.getLogger("api")

MOCK_CHUNKS = ["（MOCK）", "后端骨架已就绪，", "Agent 链路已接入。"]

# 当前请求的检索来源（在 search_blog_notes 工具中写入，在 SSE 层读取）
_current_sources: list[dict] = []


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_rag_tool():
    def search_blog_notes(query: str) -> str:
        """搜索博客笔记内容。当用户提问关于博客文章的问题时调用此工具检索相关内容来回答。

        参数说明：
        - query: 搜索查询，应提取用户问题中的核心关键词
        """
        global _current_sources
        contexts = retrieve(query)
        if not contexts:
            _current_sources = []
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
        _current_sources = deduped
        return content_block

    return FunctionTool.from_defaults(
        fn=search_blog_notes,
        name="search_blog_notes",
        description="搜索博客笔记内容。当用户提问关于博客文章的问题时使用此工具检索相关内容来回答。",
    )


def _build_feedback_tool():
    return FunctionTool.from_defaults(fn=submit_feedback)


@lru_cache
def _get_agent() -> FunctionAgent:
    settings = get_settings()
    llm = OpenAI(
        model=settings.llm_model,
        api_base=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0.7,
    )
    return FunctionAgent(
        tools=[_build_rag_tool(), _build_feedback_tool()],
        llm=llm,
        system_prompt=SYSTEM_PROMPT,
    )


_ROLE_MAP = {"user": MessageRole.USER, "assistant": MessageRole.ASSISTANT}


async def chat_event_stream(req: ChatRequest) -> AsyncIterator[str]:
    global _current_sources
    settings = get_settings()
    page_ctx = req.page_context

    if settings.llm_mock or not settings.llm_api_key:
        for chunk in MOCK_CHUNKS:
            yield _sse({"delta": chunk})
        yield _sse({"done": True, "sources": []})
        return

    try:
        user_msg = req.question
        if page_ctx and page_ctx.title:
            user_msg = f"[当前阅读：{page_ctx.title}] {req.question}"

        chat_history = [
            ChatMessage(role=_ROLE_MAP.get(m.role, MessageRole.USER), content=m.content)
            for m in req.history
        ]
        _current_sources = []

        agent = _get_agent()
        handler = agent.run(user_msg=user_msg, chat_history=chat_history)

        async for event in handler.stream_events():
            if isinstance(event, AgentStream):
                if event.delta:
                    yield _sse({"delta": event.delta})
            elif isinstance(event, ToolCallResult):
                if event.tool_name == "submit_feedback" and event.tool_output:
                    yield _sse({"delta": event.tool_output})

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

    yield _sse({"done": True, "sources": _current_sources})
