"""统一响应结构模块。

所有 API 返回统一的 JSON 结构，方便前端 / 调用方统一处理：
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {},
  "trace_id": null
}
"""

from typing import Any

from pydantic import BaseModel


class ResponseModel(BaseModel):
    """统一响应模型，用于 OpenAPI 文档展示与序列化。"""

    success: bool
    code: str
    message: str
    data: Any | None = None
    trace_id: str | None = None


def success_response(
    data: Any | None = None,
    message: str = "success",
    code: str = "OK",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """构造成功响应。

    :param data: 业务数据
    :param message: 提示信息
    :param code: 业务码，成功固定为 OK
    :param trace_id: 链路追踪 ID，预留字段
    """
    return {
        "success": True,
        "code": code,
        "message": message,
        "data": data,
        "trace_id": trace_id,
    }


def error_response(
    code: str,
    message: str,
    data: Any | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """构造失败响应。

    :param code: 业务错误码，如 INTERNAL_ERROR
    :param message: 错误信息（注意不要包含敏感信息）
    :param data: 附加数据，通常为 None
    :param trace_id: 链路追踪 ID，预留字段
    """
    return {
        "success": False,
        "code": code,
        "message": message,
        "data": data,
        "trace_id": trace_id,
    }
