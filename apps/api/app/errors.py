from typing import Any

from .models import ErrorResponse


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail

    def to_response(self) -> dict[str, Any]:
        return ErrorResponse(
            code=self.code,
            message=self.message,
            detail=self.detail,
        ).model_dump(mode="json", exclude_none=True)


class UpstreamServiceError(AppError):
    def __init__(
        self,
        message: str = "上游服务调用失败",
        *,
        detail: Any | None = None,
        status_code: int = 502,
        code: str = "upstream_error",
    ) -> None:
        super().__init__(status_code, code, message, detail)


class UpstreamTimeoutError(UpstreamServiceError):
    def __init__(self, message: str = "上游服务响应超时", *, detail: Any | None = None) -> None:
        super().__init__(message, detail=detail, status_code=504, code="upstream_timeout")
