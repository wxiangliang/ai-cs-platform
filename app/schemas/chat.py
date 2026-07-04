"""Chat API 的请求 / 响应 Schema（Pydantic v2）。"""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ChatMessageRequest(BaseModel):
    """发送聊天消息请求体。

    tenant_id：开发模式（AUTH_ENABLED=false）必填；鉴权开启后忽略（以凭证为准）。
    """

    tenant_id: str | None = Field(default=None, description="租户 ID（鉴权开启后忽略）")
    user_id: str = Field(..., min_length=1, max_length=64, description="用户 ID")
    # 消息限长：空消息在 422 层直接拦截（不进主链路），超长消息防止 Text 列与决策日志膨胀
    message: str = Field(..., min_length=1, max_length=4000, description="用户消息内容")
    channel: str = Field(default="web", max_length=32, description="渠道")
    # 语言（BCP-47 简码 zh/en…，缺省用会话记忆或 LOCALE_DEFAULT；Stage 19）
    locale: str | None = Field(default=None, max_length=16, description="用户语言")
    stream: bool = Field(default=False, description="是否流式（第一版忽略）")

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _coerce_tenant_id(cls, v: Any) -> str | None:
        """把 int / 其它类型的 tenant_id 统一转成字符串。"""
        return None if v is None else str(v)

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        """去首尾空白；纯空白消息视为无效输入。"""
        stripped = v.strip()
        if not stripped:
            raise ValueError("消息内容不能为空")
        return stripped


class ChatMessageData(BaseModel):
    """发送聊天消息响应的 data 部分。"""

    message_id: str
    session_id: str
    reply: str
    intent: str | None
    status: str | None
    state: str | None
    slots: dict[str, Any]
    trace_id: str | None


class SessionCreateRequest(BaseModel):
    """创建会话请求（session_id 由服务端生成）。"""

    tenant_id: str | None = Field(default=None, description="租户 ID（鉴权开启后忽略）")
    user_id: str = Field(..., min_length=1, max_length=64, description="用户 ID")
    channel: str = Field(default="web", max_length=32, description="渠道")

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _coerce_tenant_id(cls, v: Any) -> str | None:
        return None if v is None else str(v)


class SessionData(BaseModel):
    """会话信息。"""

    session_id: str
    tenant_id: str
    user_id: str
    channel: str
    status: str


class HistoryMessageItem(BaseModel):
    """历史消息条目。"""

    message_id: str
    role: str
    content: str
    intent: str | None
    status: str | None
    created_at: str
    metadata: dict[str, Any] | None = None


class FeedbackRequest(BaseModel):
    """用户反馈请求体（Stage 09）。"""

    tenant_id: str | None = Field(default=None, description="租户 ID（鉴权开启后忽略）")
    user_id: str = Field(..., min_length=1, max_length=64, description="用户 ID")
    message_id: str = Field(..., min_length=1, max_length=36, description="被评价的 AI 消息 ID")
    rating: str = Field(..., description="up 点赞 / down 点踩")
    comment: str | None = Field(default=None, max_length=1000, description="补充说明")

    @field_validator("rating")
    @classmethod
    def _check_rating(cls, v: str) -> str:
        if v not in ("up", "down"):
            raise ValueError("rating 必须是 up 或 down")
        return v
