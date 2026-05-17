"""全局异常处理与自定义错误类型。"""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 500, detail: dict | None = None) -> None:
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "资源未找到") -> None:
        super().__init__(message=message, status_code=404)


class ExternalAPIError(AppError):
    def __init__(self, message: str = "外部 API 调用失败", detail: dict | None = None) -> None:
        super().__init__(message=message, status_code=502, detail=detail)


class LLMError(AppError):
    def __init__(self, message: str = "LLM 调用失败") -> None:
        super().__init__(message=message, status_code=500)


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    logger.warning("应用错误", error=exc.message, status_code=exc.status_code)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message, "detail": exc.detail})


async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理的异常", error=str(exc))
    return JSONResponse(status_code=500, content={"error": "服务器内部错误"})
