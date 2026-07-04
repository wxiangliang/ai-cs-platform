"""工具协议与结果类型。"""

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolResult:
    """工具调用结果（后端无关的统一结构）。"""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    latency_ms: float = 0.0


class ToolProvider(Protocol):
    """工具提供方协议。tenant_id 必传；实现内部必须有超时。"""

    name: str

    async def invoke(
        self, tool_id: str, params: dict[str, Any], *, tenant_id: str
    ) -> ToolResult:
        """调用一个工具。未知 tool_id 返回 ok=False, error_code=TOOL_NOT_FOUND。"""
        ...


_PHONE_RE = re.compile(r"(1[3-9]\d)\d{4}(\d{4})")

# 视为地址的槽位/字段名（Stage 13 脱敏扩展）：值保留前 6 字符（省市级），其余打码
_ADDRESS_KEYS = frozenset(
    {"address", "new_address", "old_address", "receiver_address", "detail_address", "地址"}
)


def _mask_address(value: str) -> str:
    """地址打码：保留前 6 字符（大致到市/区），其余掩码。"""
    if len(value) <= 6:
        return value
    return value[:6] + "＊" * min(len(value) - 6, 8)


def mask_sensitive(params: dict[str, Any]) -> dict[str, Any]:
    """审计落库前的脱敏：手机号打码（保留前 3 后 4）+ 地址字段打码（Stage 13）。"""
    masked: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str):
            text = _PHONE_RE.sub(r"\1****\2", value)
            if key.lower() in _ADDRESS_KEYS:
                text = _mask_address(text)
            masked[key] = text
        else:
            masked[key] = value
    return masked
