import asyncio
import json
import logging
from collections.abc import AsyncIterator

from .config import get_settings
from .errors import AppError
from .llm_client import stream_chat
from .models import ChatRequest, ErrorResponse
from .prompt_templates import build_messages
from .retriever import retrieve

logger = logging.getLogger("api")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def chat_event_stream(req: ChatRequest) -> AsyncIterator[str]:
    history = [message.model_dump() for message in req.history]
    settings = get_settings()
    page_ctx = req.page_context

    try:
        if settings.llm_mock or not settings.llm_api_key:
            contexts: list[dict] = []
        else:
            retrieval_query = req.question
            if page_ctx and page_ctx.title:
                retrieval_query = f"{page_ctx.title} {req.question}"
            contexts = await asyncio.to_thread(retrieve, retrieval_query)
        messages = build_messages(req.question, contexts, history, page_ctx)
        async for token in stream_chat(messages):
            yield _sse({"delta": token})
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
                "error": ErrorResponse(code="internal_error", message="服务内部错误").model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                "done": True,
                "sources": [],
            }
        )
        return

    seen_source_urls: set[str | None] = set()
    sources = []
    for context in contexts:
        source_url = context.get("source_url")
        if source_url in seen_source_urls:
            continue
        seen_source_urls.add(source_url)
        sources.append({"title": context.get("title", ""), "source_url": source_url})

    yield _sse({"done": True, "sources": sources})
