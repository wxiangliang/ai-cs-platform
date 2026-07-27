"""阶段边界提交回归（真实 PG）：LLM 中段不得持有 DB 连接。

锁定容量修复：load_session_state 结束时 commit，连接归还池——
此后直到下一次 SQL（检索/save_turn）之间的 LLM 调用不占用连接池。
"""

import uuid

from app.chat.graph.nodes.load_session_state import load_session_state
from app.db.session import AsyncSessionLocal, async_engine, dispose_engine


async def test_load_session_state_releases_connection():
    try:
        async with AsyncSessionLocal() as session:
            config = {"configurable": {"db_session": session}}
            state = {
                "tenant_id": "cap-t",
                "session_id": f"cap-{uuid.uuid4().hex[:12]}",
                "user_id": "cap-u",
                "channel": "web",
                "message": "你好",
                "graph_trace": [],
            }
            result = await load_session_state(state, config)
            assert result["current_state"] == "IDLE"
            # 节点结束即已 commit：连接归还池，中段 LLM 期间 checkedout 应为 0
            assert async_engine.pool.checkedout() == 0, (
                "load_session_state 后连接未归还——阶段边界提交被移除会导致"
                "整轮 LLM 期间占用连接池（容量回退）"
            )
            # 同一 session 后续 SQL 仍可用（新事务重新借连接）
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
            assert async_engine.pool.checkedout() == 1
            await session.commit()
    finally:
        await dispose_engine()
