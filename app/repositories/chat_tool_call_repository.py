"""chat_tool_call 数据访问层（append-only 审计）。"""

from app.models.chat_tool_call import ChatToolCall
from app.repositories.base_repository import BaseRepository


class ChatToolCallRepository(BaseRepository[ChatToolCall]):
    """工具调用审计表 Repository（只用基类 create/get）。"""

    def __init__(self) -> None:
        super().__init__(ChatToolCall)


# 模块级单例
chat_tool_call_repository = ChatToolCallRepository()
