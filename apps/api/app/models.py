from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str


class PageContext(BaseModel):
    title: str = ""
    description: str = ""
    slug: str = ""


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    page_context: PageContext | None = None


class SourceRef(BaseModel):
    title: str
    source_url: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: Any | None = None


class ReindexResponse(BaseModel):
    status: str
    processed: int = 0
    skipped: int = 0
    deleted: int = 0
    detail: str = ""


class HealthResponse(BaseModel):
    status: str
    chroma: str
    notes_dir_exists: bool
