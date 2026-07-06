import logging
import os
from typing import Any

from fastapi import FastAPI, Header, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .chat import chat_event_stream
from .config import get_settings
from .database import init_db
from .errors import AppError
from .indexer import get_chroma_collection, reindex
from .models import ChatRequest, ErrorResponse, HealthResponse, ReindexResponse, WebhookResponse
from .admin import router as admin_router, verify_webhook, trigger_sync_pending
from .appearance import public_router as appearance_public_router

# 配置根 logger：supervisord 把 stdout 接到容器日志，INFO 级别让 syncer/notifier 等后台线程的日志可见
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

logger = logging.getLogger("api")

app = FastAPI(title="Blog AI API", version="0.1.0")
app.include_router(admin_router)
app.include_router(appearance_public_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    from .syncer import start_syncer_thread
    start_syncer_thread()
    from .notifier import start_notifier_thread
    start_notifier_thread()


settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origin_list,
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["*"],
)


def _error_response(status_code: int, code: str, message: str, detail: Any | None = None) -> JSONResponse:
    payload = ErrorResponse(code=code, message=message, detail=detail).model_dump(
        mode="json",
        exclude_none=True,
    )
    return JSONResponse(status_code=status_code, content=payload)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "validation_error",
        "请求参数不合法",
        exc.errors(),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        code = "unauthorized"
        default_message = "未授权请求"
    elif exc.status_code == status.HTTP_404_NOT_FOUND:
        code = "not_found"
        default_message = "资源不存在"
    elif 400 <= exc.status_code < 500:
        code = "bad_request"
        default_message = "请求失败"
    else:
        code = "http_error"
        default_message = "请求失败"

    detail = exc.detail
    message = detail if isinstance(detail, str) and detail else default_message
    extra_detail = None if isinstance(detail, str) else detail
    return _error_response(exc.status_code, code, message, extra_detail)


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常: %s", exc)
    return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "服务内部错误")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    s = get_settings()
    try:
        collection = get_chroma_collection()
        chroma_status = f"ok(count={collection.count()})"
    except Exception as exc:  # noqa: BLE001
        logger.warning("chroma 健康检查失败: %s", exc)
        chroma_status = "unavailable"

    return HealthResponse(
        status="ok",
        chroma=chroma_status,
        notes_dir_exists=os.path.isdir(s.notes_dir),
    )


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        chat_event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/reindex", response_model=ReindexResponse)
def reindex_endpoint(x_reindex_token: str | None = Header(default=None)) -> ReindexResponse:
    s = get_settings()
    if not s.reindex_token or x_reindex_token != s.reindex_token:
        raise AppError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "无效的 reindex token")
    return ReindexResponse(**reindex())


# 通用同步 webhook：供 GitHub Actions / 其他外部 Source 触发，对应工作流中的 /api/webhook/sync。
# 鉴权：X-Webhook-Secret（或 query/body 里的 secret）匹配 webhook_secret，否则回退 admin token。
# 不限 auto_sync_enabled 模式：手动模式下也会记录 pending，UI 显示"待同步"提示用户手动触发。
@app.post("/api/webhook/sync", response_model=WebhookResponse)
def webhook_sync(
    secret: str | None = None,
    x_webhook_secret: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> WebhookResponse:
    verify_webhook(x_webhook_secret, x_admin_token, secret)
    s = get_settings()
    return trigger_sync_pending(s, skip_auto=True)
