import asyncio
from collections.abc import Sequence

import httpx

from .config import get_settings
from .errors import UpstreamServiceError, UpstreamTimeoutError


def _build_timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=seconds, read=seconds, write=seconds, pool=seconds)


def rerank_contexts(question: str, contexts: Sequence[dict]) -> list[dict]:
    settings = get_settings()
    if not settings.rerank_enabled or not contexts or not settings.rerank_api_key:
        return list(contexts)

    documents = [context.get("text", "") for context in contexts]
    headers = {
        "Authorization": f"Bearer {settings.rerank_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": settings.rerank_model,
        "query": question,
        "documents": documents,
    }
    if settings.rerank_top_n is not None:
        payload["top_n"] = settings.rerank_top_n
    if settings.rerank_instruct:
        payload["instruct"] = settings.rerank_instruct

    try:
        with httpx.Client(timeout=_build_timeout(settings.rerank_timeout_seconds)) as client:
            response = client.post(
                "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
                headers=headers,
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise UpstreamTimeoutError(message="Reranker 服务响应超时", detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise UpstreamServiceError("Reranker 服务调用失败", detail=str(exc)) from exc

    if response.status_code >= 400:
        detail = response.text[:500].strip() or response.reason_phrase
        raise UpstreamServiceError("Reranker 服务返回异常状态", detail=detail)

    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamServiceError("Reranker 服务返回了无法解析的数据", detail=str(exc)) from exc

    results = data.get("results") or []
    if not results:
        return list(contexts)

    ranked_contexts = []
    for item in results:
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(contexts):
            continue
        context = dict(contexts[index])
        context["rerank_score"] = item.get("relevance_score")
        ranked_contexts.append(context)

    return ranked_contexts or list(contexts)


async def async_rerank_contexts(question: str, contexts: Sequence[dict]) -> list[dict]:
    return await asyncio.to_thread(rerank_contexts, question, contexts)
