"""鉴权核心（Stage 08）。

信任模型：调用方是**租户的业务后端**（服务端对服务端）。
完整密钥形如 `ak_xxx.sk_yyy`，经 `Authorization: Bearer` 传入；
库中只存 key_id + secret 的 bcrypt 哈希。

- AUTH_ENABLED=false（开发模式）：get_auth_context 返回 None，
  路由回落到请求体/查询参数中的 tenant_id（行为与 Stage 07 前一致）；
- AUTH_ENABLED=true：tenant_id 一律取自凭证；401 统一话术不区分
  key 不存在/密钥错误（防探测）；scope 不足 403。

bcrypt 验证有意做慢（~百毫秒量级），验证结果做进程内 TTL 缓存
（键为完整密钥的 sha256，不缓存明文）。
"""

import asyncio
import hashlib
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import bcrypt
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.db.session import get_db_session
from app.repositories.api_credential_repository import api_credential_repository

logger = get_logger(__name__)

SCOPE_CHAT = "chat"
SCOPE_ADMIN = "admin"


@dataclass
class AuthContext:
    """一次请求的鉴权上下文。"""

    tenant_id: str
    key_id: str
    scopes: list[str] = field(default_factory=list)


# 进程内验证缓存（LRU，Stage 13 改造：满时逐出最久未用，不再整表清空——
# 清空会引发全量 bcrypt 惊群）：sha256(完整密钥) -> (过期时间戳, 吊销版本, AuthContext)
_cache: OrderedDict[str, tuple[float, int, AuthContext]] = OrderedDict()
_CACHE_MAX = 1024

# 计时侧信道防护（Stage 13）：key_id 未命中时也做一次假 bcrypt，
# 拉平「key 不存在」与「密钥错误」的响应耗时
_DUMMY_HASH = bcrypt.hashpw(b"timing-pad-not-a-real-secret", bcrypt.gensalt()).decode()


def generate_credential(tenant_id: str, scopes: list[str]) -> tuple[str, str, str]:
    """生成新凭证：返回 (key_id, 完整密钥, secret_hash)。

    完整密钥只在此刻存在一次，调用方（CLI）负责展示给用户。
    """
    key_id = "ak_" + secrets.token_hex(8)
    secret = "sk_" + secrets.token_hex(24)
    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()
    return key_id, f"{key_id}.{secret}", secret_hash


def _rev_key(key_id: str) -> str:
    return f"auth:rev:{key_id}"


async def _current_revocation(key_id: str) -> int | None:
    """读当前吊销版本（Redis）；故障返回 None（无法校验时容忍缓存，fail-open）。"""
    try:
        from app.cache.redis_client import get_redis_client

        raw = await get_redis_client().get(_rev_key(key_id))
        return int(raw) if raw else 0
    except Exception:  # noqa: BLE001 - 吊销校验不做可用性单点
        return None


async def bump_revocation(key_id: str) -> bool:
    """吊销/改权后调用（CLI）：版本 +1，所有进程的缓存即时失效（Stage 13）。"""
    try:
        from app.cache.redis_client import get_redis_client

        await get_redis_client().incr(_rev_key(key_id))
        return True
    except Exception:  # noqa: BLE001
        logger.exception("bump revocation failed（缓存将在 TTL 内自然过期）")
        return False


def _cache_get(token_digest: str) -> tuple[int, AuthContext] | None:
    entry = _cache.get(token_digest)
    if entry and entry[0] > time.monotonic():
        _cache.move_to_end(token_digest)  # LRU 触达
        return entry[1], entry[2]
    _cache.pop(token_digest, None)
    return None


def _cache_put(token_digest: str, revocation: int, ctx: AuthContext) -> None:
    while len(_cache) >= _CACHE_MAX:
        _cache.popitem(last=False)  # LRU 逐出最久未用
    _cache[token_digest] = (time.monotonic() + settings.AUTH_CACHE_TTL, revocation, ctx)


def _unauthorized() -> AppException:
    """401 统一话术：不区分 key 不存在 / 密钥错误，防探测。"""
    return AppException(message="鉴权失败", error_code="UNAUTHORIZED", status_code=401)


async def verify_bearer_token(
    session: AsyncSession, authorization: str | None
) -> AuthContext | None:
    """验证 Bearer API Key（HTTP 依赖与 WebSocket 共用的核心逻辑，Stage 15 提取）。

    开发模式返回 None；鉴权开启时验证失败抛 401 AppException。
    """
    if not settings.AUTH_ENABLED:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized()
    token = authorization.removeprefix("Bearer ").strip()
    if "." not in token or not token.startswith("ak_"):
        raise _unauthorized()

    token_digest = hashlib.sha256(token.encode()).hexdigest()
    cached = _cache_get(token_digest)
    if cached is not None:
        cached_rev, ctx = cached
        # 吊销版本校验（Stage 13）：一次轻量 Redis GET，吊销/改权即时生效；
        # Redis 故障时容忍缓存（最长 TTL 延迟，与旧行为一致）
        current = await _current_revocation(ctx.key_id)
        if current is None or current == cached_rev:
            return ctx
        _cache.pop(token_digest, None)  # 版本已变：作废缓存，走全量重验

    key_id, _, secret = token.partition(".")
    credential = await api_credential_repository.get_active_by_key_id(session, key_id)
    if credential is None:
        # 假 bcrypt 拉平耗时：不让「key 不存在」比「密钥错误」快一个量级（Stage 13）
        await asyncio.to_thread(bcrypt.checkpw, secret.encode(), _DUMMY_HASH.encode())
        raise _unauthorized()
    # bcrypt 校验是 CPU 密集慢操作，放线程池避免阻塞事件循环
    ok = await asyncio.to_thread(
        bcrypt.checkpw, secret.encode(), credential.secret_hash.encode()
    )
    if not ok:
        logger.warning("auth failed: key_id=%s（密钥错误）", key_id)
        raise _unauthorized()

    from datetime import datetime, timezone

    credential.last_used_at = datetime.now(timezone.utc)  # 缓存 TTL 内不再更新，容忍不精确
    ctx = AuthContext(
        tenant_id=credential.tenant_id,
        key_id=credential.key_id,
        scopes=list(credential.scopes or []),
    )
    _cache_put(token_digest, await _current_revocation(key_id) or 0, ctx)
    return ctx


async def get_auth_context(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    authorization: str | None = Header(default=None),
) -> AuthContext | None:
    """解析并验证 API Key（HTTP 依赖入口）。开发模式返回 None（路由回落请求参数）。"""
    return await verify_bearer_token(session, authorization)


async def require_chat(
    auth: AuthContext | None = Depends(get_auth_context),
) -> AuthContext | None:
    """聊天面鉴权：需要 chat scope（开发模式放行）。"""
    if auth is not None and SCOPE_CHAT not in auth.scopes:
        raise AppException(message="无权访问", error_code="FORBIDDEN", status_code=403)
    return auth


async def require_admin(
    auth: AuthContext | None = Depends(get_auth_context),
    x_kb_admin_token: str | None = Header(default=None),
) -> AuthContext | None:
    """管理面鉴权：需要 admin scope。

    开发模式兼容期：沿用 KB_ADMIN_TOKEN 头校验（Stage 08 后废除，启动时告警提示迁移）。
    """
    if settings.AUTH_ENABLED:
        if auth is None or SCOPE_ADMIN not in auth.scopes:
            raise AppException(message="无权访问", error_code="FORBIDDEN", status_code=403)
        return auth
    # 开发模式也必须配置非空 KB_ADMIN_TOKEN（Stage 13 P0 整改）：
    # 此前 token 为空即全放行，默认配置起服务=管理面裸奔（可改价格/投毒知识库/拉工单上下文）
    if not settings.KB_ADMIN_TOKEN:
        raise AppException(
            message="管理面未配置访问令牌（请设置 KB_ADMIN_TOKEN 或开启 AUTH_ENABLED）",
            error_code="ADMIN_TOKEN_REQUIRED",
            status_code=403,
        )
    if x_kb_admin_token != settings.KB_ADMIN_TOKEN:
        raise AppException(message="无权访问", error_code="FORBIDDEN", status_code=403)
    return auth


def resolve_tenant_id(auth: AuthContext | None, provided: str | None) -> str:
    """确定本次请求生效的 tenant_id。

    鉴权开启：一律取凭证；请求中携带且不一致时告警（帮调用方发现配置错误）。
    开发模式：取请求携带值，缺失报 400。
    """
    if auth is not None:
        if provided and provided != auth.tenant_id:
            logger.warning(
                "请求携带的 tenant_id=%s 与凭证租户 %s 不一致，已按凭证处理（key=%s）",
                provided, auth.tenant_id, auth.key_id,
            )
        return auth.tenant_id
    if not provided:
        raise AppException(
            message="开发模式下必须提供 tenant_id", error_code="MISSING_TENANT_ID", status_code=400
        )
    return provided
