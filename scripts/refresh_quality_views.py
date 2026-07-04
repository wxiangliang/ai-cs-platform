"""质量看板物化视图刷新 CLI（Stage 09）。

    uv run python scripts/refresh_quality_views.py

幂等可重跑：CONCURRENTLY 刷新不锁读（依赖 uq_quality_daily 唯一索引）；
视图不存在（迁移未跑）时报明确错误。定时任务建议每小时/每天跑一次。
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db.session import AsyncSessionLocal, dispose_engine  # noqa: E402

VIEWS = ["quality_daily"]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        for view in VIEWS:
            start = time.perf_counter()
            # CONCURRENTLY 不能在事务块内执行，autocommit 隔离级别单独跑
            conn = await session.connection(
                execution_options={"isolation_level": "AUTOCOMMIT"}
            )
            await conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
            print(f"refreshed {view} in {time.perf_counter() - start:.2f}s")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
