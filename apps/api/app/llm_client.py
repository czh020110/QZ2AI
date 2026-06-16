import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from .config import get_settings
from .errors import AppError, UpstreamServiceError, UpstreamTimeoutError

MOCK_CHUNKS = ["（MOCK）", "后端骨架已就绪，", "RAG 链路将在阶段三接入。"]


def _build_timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=seconds, read=seconds, write=seconds, pool=seconds)


def _build_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}" + "/chat/completions"


def _extract_stream_delta(payload: dict) -> str | None:
    choices = payload.get("choices") or []
    if not choices:
        return None

    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str) and content:
        return content

    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        joined = "".join(part for part in parts if isinstance(part, str))
        return joined or None

    return None


async def _stream_chat_once(messages: list[dict], settings) -> AsyncIterator[str]:
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": True,
    }

    try:
        async with httpx.AsyncClient(timeout=_build_timeout(settings.llm_timeout_seconds)) as client:
            async with client.stream(
                "POST",
                _build_endpoint(settings.llm_base_url),
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    error_body = (await response.aread()).decode("utf-8", errors="ignore").strip()
                    detail = error_body[:500] if error_body else response.reason_phrase
                    raise UpstreamServiceError(
                        message=f"上游服务返回异常状态（{response.status_code}）",
                        detail=detail,
                    )

                saw_content = False
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        return

                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise UpstreamServiceError("上游返回了无法解析的流式数据", detail=str(exc)) from exc

                    token = _extract_stream_delta(event)
                    if token:
                        saw_content = True
                        yield token

                if not saw_content:
                    raise UpstreamServiceError("上游未返回任何可用内容")
    except httpx.TimeoutException as exc:
        raise UpstreamTimeoutError(detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise UpstreamServiceError(detail=str(exc)) from exc


async def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
    settings = get_settings()
    if settings.llm_mock or not settings.llm_api_key:
        for chunk in MOCK_CHUNKS:
            yield chunk
        return

    last_error: AppError | None = None
    for attempt in range(settings.http_max_retries + 1):
        emitted = False
        try:
            async for token in _stream_chat_once(messages, settings):
                emitted = True
                yield token
            return
        except AppError as exc:
            last_error = exc
            if emitted or attempt >= settings.http_max_retries:
                raise
            if settings.http_retry_backoff_seconds > 0:
                await asyncio.sleep(settings.http_retry_backoff_seconds * (attempt + 1))

    if last_error is not None:
        raise last_error
