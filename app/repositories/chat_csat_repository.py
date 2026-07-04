"""chat_csat 数据访问层。"""

from app.models.chat_csat import ChatCsat
from app.repositories.base_repository import BaseRepository


class ChatCsatRepository(BaseRepository[ChatCsat]):
    """会话满意度 Repository（只需通用 create，分析走 SQL）。"""

    def __init__(self) -> None:
        super().__init__(ChatCsat)


# 模块级单例
chat_csat_repository = ChatCsatRepository()
