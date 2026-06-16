import json
import logging
from collections.abc import AsyncIterator

from .errors import AppError
from .llm_client import stream_chat
from .models import ChatRequest, ErrorResponse
from .prompt_templates import build_messages

logger = logging.getLogger("api")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"


async def chat_event_stream(req: ChatRequest) -> AsyncIterator[str]:
    history = [message.model_dump() for message in req.history]
    contexts: list[dict] = []
    messages = build_messages(req.question, contexts, history)

    try:
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

    sources = [{"title": context.get("title", ""), "source_url": context.get("source_url")} for context in contexts]
    yield _sse({"done": True, "sources": sources})
