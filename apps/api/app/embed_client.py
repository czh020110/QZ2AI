from collections.abc import Sequence
import time

import httpx
from llama_index.core.base.embeddings.base import BaseEmbedding
from pydantic import Field, PrivateAttr

from .config import get_settings
from .errors import UpstreamServiceError, UpstreamTimeoutError


def _build_timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=seconds, read=seconds, write=seconds, pool=seconds)


class DashScopeEmbedding(BaseEmbedding):
    model_name: str = Field(default="text-embedding-v4")
    api_key: str = Field(default="")
    text_type: str = Field(default="document")
    dimensions: int | None = Field(default=None)
    timeout_seconds: float = Field(default=60.0)
    max_retries: int = Field(default=2)
    embed_batch_size: int = Field(default=10)
    _backoff_seconds: float = PrivateAttr(default=1.0)

    _endpoint: str = PrivateAttr("https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding")

    @classmethod
    def class_name(cls) -> str:
        return "DashScopeEmbedding"

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed_texts([query], text_type="query")[0]

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed_texts([text], text_type=self.text_type)[0]

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._embed_texts(texts, text_type=self.text_type)

    async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._get_text_embeddings(texts)

    def _embed_texts(self, texts: Sequence[str], *, text_type: str) -> list[list[float]]:
        if not self.api_key:
            raise UpstreamServiceError("Embedding 服务缺少 API Key", code="embedding_credentials_missing")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        parameters: dict[str, object] = {"text_type": text_type}
        if self.dimensions is not None:
            parameters["dimension"] = self.dimensions

        payload = {
            "model": self.model_name,
            "input": {"texts": list(texts)},
            "parameters": parameters,
        }

        # 百炼 embedding 偶发返回 5xx 服务端瞬时错误（大批量索引时更易触发），按配置重试
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=_build_timeout(self.timeout_seconds)) as client:
                    response = client.post(self._endpoint, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                raise UpstreamTimeoutError(message="Embedding 服务响应超时", detail=str(exc)) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self._backoff_seconds * (attempt + 1))
                    continue
                raise UpstreamServiceError("Embedding 服务调用失败", detail=str(exc)) from exc

            # 5xx 视为可重试；4xx 是请求本身问题，直接抛出
            if response.status_code >= 500 and attempt < self.max_retries:
                last_error = UpstreamServiceError(
                    "Embedding 服务返回异常状态", detail=f"HTTP {response.status_code}: {response.text[:200]}"
                )
                time.sleep(self._backoff_seconds * (attempt + 1))
                continue

            if response.status_code >= 400:
                detail = response.text[:500].strip() or response.reason_phrase
                raise UpstreamServiceError("Embedding 服务返回异常状态", detail=detail)

            try:
                data = response.json()
            except ValueError as exc:
                raise UpstreamServiceError("Embedding 服务返回了无法解析的数据", detail=str(exc)) from exc

            embeddings = (data.get("output") or {}).get("embeddings") or []
            if not embeddings:
                raise UpstreamServiceError("Embedding 服务未返回有效向量")

            ordered = sorted(embeddings, key=lambda item: item.get("text_index", 0))
            return [item["embedding"] for item in ordered if isinstance(item.get("embedding"), list)]

        # 重试耗尽仍失败
        raise last_error or UpstreamServiceError("Embedding 服务调用失败")


def get_embed_model() -> BaseEmbedding:
    settings = get_settings()
    if settings.embedding_provider.lower() == "openai":
        from llama_index.embeddings.openai import OpenAIEmbedding

        return OpenAIEmbedding(
            model=settings.embedding_model,
            api_base="https://api.openai.com/v1",
            api_key=settings.embedding_api_key,
            timeout=settings.embedding_timeout_seconds,
            max_retries=settings.http_max_retries,
            embed_batch_size=settings.embedding_batch_size,
            dimensions=settings.embedding_dimensions,
        )

    return DashScopeEmbedding(
        model_name=settings.embedding_model,
        api_key=settings.embedding_api_key,
        text_type=settings.embedding_text_type,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
        max_retries=settings.http_max_retries,
        embed_batch_size=settings.embedding_batch_size,
        _backoff_seconds=settings.http_retry_backoff_seconds,
    )
