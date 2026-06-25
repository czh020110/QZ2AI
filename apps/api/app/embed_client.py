from collections.abc import Sequence

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

        try:
            with httpx.Client(timeout=_build_timeout(self.timeout_seconds)) as client:
                response = client.post(self._endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(message="Embedding 服务响应超时", detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise UpstreamServiceError("Embedding 服务调用失败", detail=str(exc)) from exc

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
    )
