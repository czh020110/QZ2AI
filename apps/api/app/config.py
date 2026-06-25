from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    embedding_provider: str = "dashscope"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int | None = Field(default=None, ge=1)
    embedding_text_type: str = "document"

    rerank_provider: str = "dashscope"
    rerank_api_key: str = ""
    rerank_model: str = "qwen3-rerank"
    rerank_top_n: int | None = Field(default=None, ge=1, le=500)
    rerank_enabled: bool = True
    rerank_instruct: str = ""

    chroma_dir: str = "/data/chroma"
    notes_dir: str = "/data/notes"
    docstore_path: str = "/data/docstore.json"
    chroma_collection: str = "blog"

    similarity_top_k: int = 5
    llm_mock: bool = False
    reindex_token: str = ""
    allowed_origins: str = "http://localhost"

    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    embedding_timeout_seconds: float = Field(default=60.0, gt=0)
    rerank_timeout_seconds: float = Field(default=60.0, gt=0)
    http_max_retries: int = Field(default=2, ge=0, le=5)
    http_retry_backoff_seconds: float = Field(default=1.0, ge=0)
    embedding_batch_size: int = Field(default=10, ge=1, le=100)

    @property
    def allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]



@lru_cache
def get_settings() -> Settings:
    return Settings()
