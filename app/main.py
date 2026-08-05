"""FastAPI 应用入口。

负责：
- 初始化日志；
- 创建 FastAPI app 并注册全局异常处理与中间件（trace / 请求体限长 / CORS）；
- 通过 lifespan 管理 Redis 等资源的启动与优雅关闭；
- 注册路由。
"""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import (
    cases,
    chat,
    events,
    handoff,
    health,
    kb,
    metrics,
    observe,
    product,
    ws,
)
from app.cache.redis_client import close_redis, init_redis
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, set_trace_id, setup_logging
from app.core.responses import error_response
from app.db.session import dispose_engine

# 先初始化日志，保证后续所有模块日志格式统一
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。

    startup 阶段：初始化 Redis 连接池；
    shutdown 阶段：优雅关闭 Redis 与数据库连接池，避免资源泄漏。
    """
    # ----- startup -----
    logger.info("Starting %s (env=%s)...", settings.APP_NAME, settings.APP_ENV)
    if not settings.AUTH_ENABLED:
        logger.warning("AUTH_ENABLED=false（开发模式）：tenant_id 来自请求体，生产必须开启鉴权")
    if settings.KB_ADMIN_TOKEN and not settings.AUTH_ENABLED:
        logger.warning("KB_ADMIN_TOKEN 属过渡方案，请尽快迁移到 admin scope 的 API Key（Stage 08）")
    await init_redis()
    # 启动即探活 DB（Stage 13：与 Redis fail-fast 语义一致——依赖挂了不该"假装启动成功"）
    from sqlalchemy import text as _sql_text

    from app.db.session import AsyncSessionLocal as _ProbeSession

    async with _ProbeSession() as _probe:
        await _probe.execute(_sql_text("SELECT 1"))
    logger.info("PostgreSQL connectivity verified.")
    # 冷启动预热（延迟修复）：jieba 词典构建（~1s 同步 CPU）与本地重排模型加载
    # （数秒到数十秒）不预热会打在首个检索请求上，且阻塞事件循环影响同批请求
    if settings.KB_ENABLED:
        import asyncio as _asyncio

        import jieba as _jieba

        await _asyncio.to_thread(_jieba.initialize)
        logger.info("jieba dictionary warmed up.")
        if settings.RERANKER_PROVIDER == "local":
            from app.kb.rerank import _local_reranker

            await _asyncio.to_thread(_local_reranker._ensure_loaded)
            logger.info("local reranker warmed up.")
    yield
    # ----- shutdown -----
    logger.info("Shutting down %s...", settings.APP_NAME)
    # 先收敛在途后台任务（记忆写入等），再关资源（Stage 13：不让任务用到已关闭的池）
    from app.services.chat_service import wait_background_tasks

    await wait_background_tasks()
    # WebSocket 枢纽监听任务关停（Stage 15）
    from app.services.notify_service import ws_hub

    await ws_hub.shutdown()
    # Langfuse 追踪缓冲 flush（Stage 12，未启用为空操作）
    from app.core.tracing import shutdown_langfuse

    shutdown_langfuse()
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    """应用工厂：创建并配置 FastAPI 实例。"""
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # 注册全局异常处理，保证所有异常返回统一响应结构
    register_exception_handlers(app)

    @app.middleware("http")
    async def trace_and_body_limit(request: Request, call_next):
        """请求级中间件：trace_id 全链路 + 请求体大小限制（Stage 08）。

        trace 在入口生成（此前在 Service 才生成，422/401 等早期失败无 trace 可查——
        代码评审遗留 P2-12 清偿），响应回写 X-Trace-Id 头。
        """
        trace_id = request.headers.get("X-Trace-Id") or ("trace_" + uuid4().hex)
        set_trace_id(trace_id)

        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content=error_response(
                    code="PAYLOAD_TOO_LARGE", message="请求体过大", trace_id=trace_id
                ),
            )

        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

    # CORS（配置了来源才启用）
    if settings.CORS_ORIGINS:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # 注册路由
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(kb.router)
    app.include_router(product.router)
    app.include_router(handoff.router)
    app.include_router(cases.router)
    app.include_router(events.router)
    app.include_router(observe.router)
    app.include_router(metrics.router)
    app.include_router(ws.router)

    return app


# ASGI 入口对象，供 `uvicorn app.main:app` 使用
app = create_app()
