"""限流与幂等单元测试（真实 Redis，key 唯一前缀隔离）。"""

import uuid

import pytest

from app.cache.redis_client import close_redis, init_redis
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.idempotency import (
    acquire_processing_lock,
    body_fingerprint,
    cache_response,
    get_cached_response,
    release_processing_lock,
)
from app.core.rate_limit import RateLimitExceeded, _check_window, check_chat_rate_limit


@pytest.fixture
async def _redis():
    await init_redis()
    yield
    await close_redis()


async def test_window_limits(_redis):
    key = f"rl:test:{uuid.uuid4().hex}"
    for _ in range(3):
        await _check_window(key, limit=3, scope="tenant")
    with pytest.raises(RateLimitExceeded) as e:
        await _check_window(key, limit=3, scope="tenant")
    assert e.value.status_code == 429 and e.value.retry_after > 0


async def test_session_level_limit(_redis, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_TENANT_PER_MINUTE", 100)
    monkeypatch.setattr(settings, "RATE_LIMIT_SESSION_PER_MINUTE", 2)
    tenant, sess = f"t-{uuid.uuid4().hex[:6]}", "s1"
    await check_chat_rate_limit(tenant, sess)
    await check_chat_rate_limit(tenant, sess)
    with pytest.raises(RateLimitExceeded):
        await check_chat_rate_limit(tenant, sess)
    # 换会话不受影响（租户额度未满）
    await check_chat_rate_limit(tenant, "s2")


async def test_rate_limit_allows_when_redis_down(monkeypatch):
    """Redis 不可用 → 放行（限流不做可用性单点）。"""
    monkeypatch.setattr(
        "app.core.rate_limit.get_redis_client",
        lambda: (_ for _ in ()).throw(RuntimeError("down")),
    )
    await check_chat_rate_limit("t1", "s1")  # 不抛异常即通过


async def test_idempotency_roundtrip(_redis):
    tenant, sess, key = "t1", "s1", uuid.uuid4().hex
    fp = body_fingerprint('{"message": "你好"}')
    assert await get_cached_response(tenant, sess, key, fp) is None
    payload = {"success": True, "data": {"reply": "你好"}}
    await cache_response(tenant, sess, key, payload, fp)
    assert await get_cached_response(tenant, sess, key, fp) == payload


async def test_idempotency_fingerprint_mismatch_422(_redis):
    """同 key 不同 body：客户端 bug，明确 422 而非静默复用旧结果（Stage 13）。"""
    tenant, sess, key = "t1", "s1", uuid.uuid4().hex
    fp1 = body_fingerprint('{"message": "退款"}')
    await cache_response(tenant, sess, key, {"data": 1}, fp1)
    with pytest.raises(AppException) as exc:
        await get_cached_response(tenant, sess, key, body_fingerprint('{"message": "取消"}'))
    assert exc.value.error_code == "IDEMPOTENCY_KEY_REUSED"


async def test_idempotency_inflight_lock(_redis):
    """在途占位：同 key 并发第二个请求 409；释放后可再次获取（Stage 13）。"""
    tenant, sess, key = "t1", "s1", uuid.uuid4().hex
    fp = body_fingerprint("x")
    await acquire_processing_lock(tenant, sess, key, fp)
    with pytest.raises(AppException) as exc:
        await acquire_processing_lock(tenant, sess, key, fp)
    assert exc.value.error_code == "REQUEST_IN_FLIGHT" and exc.value.status_code == 409
    await release_processing_lock(tenant, sess, key, fp)
    await acquire_processing_lock(tenant, sess, key, fp)  # 释放后可重新占位
    await release_processing_lock(tenant, sess, key, fp)


async def test_idempotency_size_cap(_redis, monkeypatch):
    monkeypatch.setattr(settings, "IDEMPOTENCY_MAX_BODY_BYTES", 10)
    key = uuid.uuid4().hex
    fp = body_fingerprint("x")
    await cache_response("t1", "s1", key, {"data": "超过十个字节的内容" * 10}, fp)
    assert await get_cached_response("t1", "s1", key, fp) is None  # 超限未缓存
