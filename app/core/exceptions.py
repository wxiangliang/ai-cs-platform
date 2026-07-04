"""统一异常模块。

定义基础业务异常 AppException，并提供全局异常处理函数，
保证所有异常都被记录日志并以统一响应结构返回，不向外暴露堆栈细节。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, get_trace_id
from app.core.responses import error_response

logger = get_logger(__name__)


class AppException(Exception):
    """基础业务异常。

    业务代码应抛出本异常（或其子类），统一携带：
    - error_code：业务错误码，便于调用方判断
    - message：可对外展示的错误信息
    - status_code：HTTP 状态码
    """

    def __init__(
        self,
        message: str = "internal error",
        error_code: str = "INTERNAL_ERROR",
        status_code: int = 500,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


def register_exception_handlers(app: FastAPI) -> None:
    """向 FastAPI 应用注册全局异常处理函数。

    统一处理三类异常：业务异常、请求参数校验异常、未捕获的兜底异常。
    """

    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        """处理业务异常：按异常自带的错误码与状态码返回。"""
        # 业务异常通常是可预期的，记录为 warning
        logger.warning(
            "AppException: code=%s message=%s path=%s",
            exc.error_code,
            exc.message,
            request.url.path,
        )
        # 限流异常附带 Retry-After 头（Stage 08）
        headers = {}
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                code=exc.error_code,
                message=exc.message,
                trace_id=get_trace_id(),
            ),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """处理请求参数校验失败异常，返回 422。

        Pydantic v2 的 errors() 含 input 字段（用户原始输入，可能是手机号/消息全文），
        记日志与返回前必须剔除，避免敏感信息进日志或回显内部结构。
        """
        safe_errors = [
            {k: v for k, v in err.items() if k not in ("input", "url", "ctx")}
            for err in exc.errors()
        ]
        logger.warning("ValidationError: path=%s errors=%s", request.url.path, safe_errors)
        return JSONResponse(
            status_code=422,
            content=error_response(
                code="VALIDATION_ERROR",
                message="请求参数校验失败",
                data=safe_errors,
                trace_id=get_trace_id(),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """处理 FastAPI/Starlette 抛出的 HTTP 异常（如 404）。"""
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                code="HTTP_ERROR",
                message=str(exc.detail),
                trace_id=get_trace_id(),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        """兜底处理所有未捕获异常，避免向外暴露堆栈细节。"""
        # 未预期异常记录完整堆栈，便于排查
        logger.exception("Unhandled exception at path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_response(
                code="INTERNAL_ERROR",
                message="服务器内部错误",
                trace_id=get_trace_id(),
            ),
        )
